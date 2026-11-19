// Reader of a schema registry, `kleisis:schema-registry@2`, with the verdicts of the Python
// reader `kleisis.registry.read_registry`. The cases of `specs/cases/registries.yml` fix them.

import { isContractIdentifier } from "./contracts.js";
import { DocumentError, loadDocument, show } from "./document.js";
import type { Mapping, Value } from "./document.js";
import { RegistryError } from "./errors.js";
import { safeRelativePath } from "./paths.js";
import { isBlank } from "./python.js";

/** The format identifier that a registry declares in its key `_schema`. */
export const REGISTRY_FORMAT = "kleisis:schema-registry@2";

const FIELDS = new Set(["_schema", "schemas", "exports", "resources", "keywords"]);
const LEGACY_FIELDS = new Map<bigint, Set<string>>([
  [1n, new Set(["schema_version", "schemas", "exports"])],
  [2n, new Set(["schema_version", "schemas", "exports", "resources"])],
]);

/** An absolute URI starts with a scheme; the letters are those that Python matches without case. */
const ABSOLUTE_URI = /^[A-Za-zİıſK][A-Za-z0-9+.\-İıſK]*:/;
/** Form of a keyword name: `x-` followed by words in lower kebab case. */
const KEYWORD = /^x-[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

/** The content of a registry. Each path is relative to the root of its repository. */
export interface Registry {
  /** The layout version: 2 for the current format and for the legacy `schema_version: 2`. */
  readonly version: 1 | 2;
  /** The file of each contract identifier. */
  readonly schemas: Readonly<Record<string, string>>;
  /** The contracts that the repository owns, each a key of `schemas`. */
  readonly exports: readonly string[];
  /** The file of each absolute `$id` of the registry closure. */
  readonly resources: Readonly<Record<string, string>>;
  /** The description of each `x-` keyword that the repository declares. */
  readonly keywords: Readonly<Record<string, string>>;
}

/**
 * Read the registry of YAML `text`, in its current or its legacy format, or throw `RegistryError`
 * naming the rule broken. `path` names the file in the messages.
 */
export function readRegistry(text: string, path = "registry"): Registry {
  const root = loadMapping(text, path);
  const [version, fields] = registryVersion(path, root);
  const unknown = [...root.keys()].filter((key) => !fields.has(key)).sort();
  if (unknown.length > 0) {
    throw invalid(`${path} contains unknown fields: ${unknown.join(", ")}`);
  }
  const schemas = registryPaths(path, root.get("schemas") ?? null, "schemas");
  requireIdentifiers(path, schemas.keys(), "schemas");
  const rawExports = root.has("exports") ? root.get("exports") : [];
  if (
    !Array.isArray(rawExports) ||
    !rawExports.every((identifier) => typeof identifier === "string" && identifier !== "")
  ) {
    throw invalid(`${path} exports must be a sequence of identifiers`);
  }
  const exports = [...(rawExports as string[])];
  requireIdentifiers(path, exports, "exports");
  if (new Set(exports).size !== exports.length) {
    throw invalid(`${path} exports contains duplicates`);
  }
  const missing = [...new Set(exports)].filter((identifier) => !schemas.has(identifier)).sort();
  if (missing.length > 0) {
    throw invalid(`${path} exports identifiers absent from schemas: ${missing.join(", ")}`);
  }
  let resources = new Map<string, string>();
  if (version >= 2) {
    // An absent map reads as an empty one, as the decision on tool roles states for readers.
    resources = registryPaths(
      path,
      root.has("resources") ? (root.get("resources") ?? null) : new Map(),
      "resources",
    );
    const relative = [...resources.keys()].filter((uri) => !ABSOLUTE_URI.test(uri)).sort();
    if (relative.length > 0) {
      throw invalid(`${path} resources keys must be absolute URIs: ${relative.join(", ")}`);
    }
  }
  const keywords = registryKeywords(
    path,
    root.has("keywords") ? (root.get("keywords") ?? null) : new Map(),
  );
  return {
    version: version === 1n ? 1 : 2,
    schemas: Object.fromEntries(schemas),
    exports,
    resources: Object.fromEntries(resources),
    keywords: Object.fromEntries(keywords),
  };
}

function invalid(message: string): RegistryError {
  return new RegistryError("registry/invalid", message);
}

function isMapping(value: Value | undefined): value is Mapping {
  return value instanceof Map;
}

function loadMapping(text: string, path: string): Map<string, Value> {
  let value: Value;
  try {
    value = loadDocument(text);
  } catch (error) {
    if (error instanceof DocumentError) throw invalid(`cannot read ${path}: ${error.message}`);
    throw error;
  }
  if (!isMapping(value) || ![...value.keys()].every((key) => typeof key === "string")) {
    throw invalid(`${path} must contain a mapping`);
  }
  return value as Map<string, Value>;
}

/** Return the layout version of a registry and the fields that this layout admits. */
function registryVersion(path: string, root: Map<string, Value>): [bigint, Set<string>] {
  const expected = `write _schema: ${REGISTRY_FORMAT}`;
  if (root.has("_schema")) {
    if (root.has("schema_version")) {
      throw invalid(`${path} declares both _schema and schema_version; ${expected} alone`);
    }
    if (root.get("_schema") !== REGISTRY_FORMAT) {
      throw invalid(`${path} declares _schema ${show(root.get("_schema"))}; ${expected}`);
    }
    return [2n, FIELDS];
  }
  if (!root.has("schema_version")) throw invalid(`${path} declares no _schema; ${expected}`);
  const version = root.get("schema_version");
  const fields = typeof version === "bigint" ? LEGACY_FIELDS.get(version) : undefined;
  if (typeof version !== "bigint" || fields === undefined) {
    throw invalid(`${path} declares an unsupported schema_version ${show(version)}; ${expected}`);
  }
  return [version, fields];
}

function registryPaths(path: string, value: Value, fieldName: string): Map<string, string> {
  if (!isMapping(value)) throw invalid(`${path} must contain a ${fieldName} mapping`);
  const result = new Map<string, string>();
  for (const [key, target] of value) {
    if (typeof key !== "string" || key === "") {
      throw invalid(`${path} contains an invalid ${fieldName} key`);
    }
    try {
      result.set(key, safeRelativePath(target, `${fieldName}[${JSON.stringify(key)}]`));
    } catch (error) {
      if (error instanceof RegistryError) {
        throw new RegistryError(error.rule, `${path}: ${error.message}`);
      }
      throw error;
    }
  }
  return result;
}

/** Reject the first key of `schemas` or entry of `exports` outside the grammar. */
function requireIdentifiers(path: string, identifiers: Iterable<string>, fieldName: string): void {
  for (const identifier of identifiers) {
    if (!isContractIdentifier(identifier)) {
      throw new RegistryError(
        "contract/identifier",
        `${path} ${fieldName} holds ${JSON.stringify(identifier)}, which is no contract ` +
          "identifier: write <namespace>:<name>@<version>",
      );
    }
  }
}

/** Return the keywords of a registry, each named `x-<words>` with a non-empty description. */
function registryKeywords(path: string, value: Value): Map<string, string> {
  if (!isMapping(value)) throw invalid(`${path} must contain a keywords mapping`);
  for (const [name, description] of value) {
    const validName = typeof name === "string" && KEYWORD.test(name);
    if (!validName || typeof description !== "string" || isBlank(description)) {
      throw invalid(
        `${path} keywords holds ${show(name)}, which must be named x-<words> and described`,
      );
    }
  }
  return value as Map<string, string>;
}
