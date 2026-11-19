// Loading of a YAML document as the Python reader loads it: PyYAML's safe loader, which reads
// YAML 1.1, with duplicate keys and merge keys `<<` rejected, followed by a check that the value
// serializes to strict JSON. The parser of the `yaml` package reads the syntax; this module
// resolves and constructs each node as PyYAML does, since the YAML 1.1 schema of `yaml` resolves
// some plain scalars, such as `y` or `1e3`, otherwise than PyYAML.

import { isAlias, isMap, isScalar, isSeq, parseDocument, Scalar } from "yaml";
import type { Document, Node } from "yaml";

import { pythonFloat, pythonInt } from "./python.js";

/**
 * A value of a loaded document, typed as Python types it: an integer is a `bigint`, a float a
 * `number`, and a mapping a `Map` whose keys keep their own types.
 */
export type Value = null | boolean | bigint | number | string | Value[] | Mapping;

/** A mapping of a loaded document, in the order of its keys. */
export type Mapping = Map<Value, Value>;

/** A document that PyYAML cannot load, or whose value does not serialize to strict JSON. */
export class DocumentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DocumentError";
  }
}

/** Render a loaded value for a message, in the notation of YAML flow collections. */
export function show(value: Value | undefined): string {
  if (value === undefined || value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(show).join(", ")}]`;
  if (value instanceof Map) {
    return `{${[...value].map(([key, item]) => `${show(key)}: ${show(item)}`).join(", ")}}`;
  }
  return value.toString();
}

const TAG = "tag:yaml.org,2002:";

/** The implicit resolvers of PyYAML, in the order of their registration. */
const RESOLVERS: readonly [string, RegExp][] = [
  ["bool", /^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$/],
  [
    "float",
    /^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$/,
  ],
  [
    "int",
    /^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$/,
  ],
  ["merge", /^(?:<<)$/],
  ["null", /^(?:~|null|Null|NULL|)$/],
  [
    "timestamp",
    /^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$/,
  ],
  ["value", /^(?:=)$/],
  ["yaml", /^(?:!|&|\*)$/],
];

/** The characters that the reader of PyYAML accepts in a stream. */
const NON_PRINTABLE = /[^\t\n\r\x20-\x7e\x85\xa0-퟿-�\u{10000}-\u{10ffff}]/u;

/** Return the value of the YAML `text`, or throw `DocumentError` where the Python reader fails. */
export function loadDocument(text: string): Value {
  const unprintable = NON_PRINTABLE.exec(text);
  if (unprintable !== null) {
    throw new DocumentError(`unacceptable character at offset ${unprintable.index}`);
  }
  // The failsafe schema keeps each scalar as its text, which `construct` resolves.
  const document = parseDocument(text, {
    version: "1.1",
    schema: "failsafe",
    merge: false,
    uniqueKeys: false,
    prettyErrors: false,
  });
  const [error] = document.errors;
  if (error !== undefined) throw new DocumentError(error.message);
  return new Constructor(document).construct(document.contents);
}

class Constructor {
  private readonly document: Document;
  private readonly done = new Map<Node, Value>();
  private readonly open = new Set<Node>();

  constructor(document: Document) {
    this.document = document;
  }

  construct(node: unknown): Value {
    const target = this.target(node);
    if (target === null) return null;
    const known = this.done.get(target);
    if (known !== undefined) return known;
    if (this.open.has(target)) {
      // A document that contains itself has no JSON form.
      throw new DocumentError("circular reference");
    }
    this.open.add(target);
    const value = this.build(target);
    this.open.delete(target);
    this.done.set(target, value);
    return value;
  }

  /** Return the node that `node` denotes, through an alias, or `null` for an empty node. */
  private target(node: unknown): Node | null {
    if (node === null || node === undefined) return null;
    if (isAlias(node)) {
      const resolved = node.resolve(this.document);
      if (resolved === undefined) throw new DocumentError(`unknown alias ${node.source}`);
      return this.target(resolved);
    }
    if (isScalar(node) || isMap(node) || isSeq(node)) return node;
    throw new DocumentError("unknown node");
  }

  private build(node: Node): Value {
    const tag = resolveTag(node);
    if (isScalar(node)) return constructScalar(tag, String(node.value));
    if (isSeq(node)) {
      if (tag === `${TAG}seq`) return node.items.map((item) => this.construct(item));
      if (tag === `${TAG}omap` || tag === `${TAG}pairs`) {
        return node.items.map((item) => this.pair(item));
      }
      throw new DocumentError(`a sequence cannot take the tag ${tag}`);
    }
    if (isMap(node) && tag === `${TAG}map`) return this.mapping(node.items);
    throw new DocumentError(`a mapping cannot take the tag ${tag}`);
  }

  /** Construct an item of `!!omap` or `!!pairs`, a mapping of one key, as a list of two values. */
  private pair(item: unknown): Value {
    const node = this.target(item);
    if (!isMap(node) || node.items.length !== 1) {
      throw new DocumentError("an ordered mapping holds mappings of one key");
    }
    const [{ key, value } = { key: null, value: null }] = node.items;
    return [this.construct(key), this.construct(value)];
  }

  private mapping(items: readonly { key: unknown; value: unknown }[]): Mapping {
    const result: Mapping = new Map();
    const seen = new Set<string>();
    for (const { key, value } of items) {
      const keyNode = this.target(key);
      if (keyNode !== null && resolveTag(keyNode) === `${TAG}merge`) {
        throw new DocumentError(
          "found a merge key `<<`, which a YAML 1.2 reader reads as an ordinary key",
        );
      }
      const constructed = this.construct(keyNode);
      const hash = hashKey(constructed);
      if (hash !== undefined && seen.has(hash)) {
        throw new DocumentError(`found duplicate key ${show(constructed)}`);
      }
      if (hash !== undefined) seen.add(hash);
      result.set(constructed, this.construct(value));
    }
    return result;
  }
}

/** Return the tag of `node`: its explicit tag, or the tag that PyYAML resolves. */
function resolveTag(node: Node): string {
  const explicit = node.tag;
  if (isScalar(node)) {
    // PyYAML resolves a scalar under the non-specific tag `!` as a plain one, whatever its style.
    if ((explicit === undefined && node.type === Scalar.PLAIN) || explicit === "!") {
      const text = String(node.value);
      const found = RESOLVERS.find(([, pattern]) => pattern.test(text));
      return `${TAG}${found === undefined ? "str" : found[0]}`;
    }
    return explicit ?? `${TAG}str`;
  }
  if (explicit !== undefined && explicit !== "!") return explicit;
  return isSeq(node) ? `${TAG}seq` : `${TAG}map`;
}

/**
 * Construct a scalar as the safe constructor of PyYAML does. A timestamp, a binary or any tag
 * without a safe constructor fails, as does a float that is not finite: none has a strict JSON
 * form, which the Python reader requires.
 */
function constructScalar(tag: string, text: string): Value {
  switch (tag) {
    case `${TAG}str`:
      return text;
    case `${TAG}null`:
      return null;
    case `${TAG}bool`:
      return constructBool(text);
    case `${TAG}int`:
      return constructInt(text);
    case `${TAG}float`:
      return constructFloat(text);
    default:
      throw new DocumentError(`cannot construct a scalar of tag ${tag}`);
  }
}

function constructBool(text: string): boolean {
  const lowered = text.toLowerCase();
  if (["yes", "true", "on"].includes(lowered)) return true;
  if (["no", "false", "off"].includes(lowered)) return false;
  // PyYAML raises `KeyError` here, which escapes the Python reader; this reader rejects the file.
  throw new DocumentError(`${text} is no boolean`);
}

function constructInt(text: string): bigint {
  let value = text.replace(/_/g, "");
  const sign = value.startsWith("-") ? -1n : 1n;
  if (value.startsWith("-") || value.startsWith("+")) value = value.slice(1);
  let result: bigint | undefined;
  if (value === "0") result = 0n;
  else if (value.startsWith("0b")) result = pythonInt(value.slice(2), 2);
  else if (value.startsWith("0x")) result = pythonInt(value.slice(2), 16);
  else if (value.startsWith("0")) result = pythonInt(value, 8);
  else if (value.includes(":"))
    result = sexagesimal(value.split(":").map((part) => pythonInt(part, 10)));
  else result = pythonInt(value, 10);
  if (result === undefined) throw new DocumentError(`${text} is no integer`);
  return sign * result;
}

function sexagesimal(digits: (bigint | undefined)[]): bigint | undefined {
  let result = 0n;
  for (const digit of digits) {
    if (digit === undefined) return undefined;
    result = result * 60n + digit;
  }
  return result;
}

function constructFloat(text: string): number {
  let value = text.replace(/_/g, "").toLowerCase();
  const sign = value.startsWith("-") ? -1 : 1;
  if (value.startsWith("-") || value.startsWith("+")) value = value.slice(1);
  let result: number | undefined;
  if (value === ".inf") result = Infinity;
  else if (value === ".nan") result = NaN;
  else if (value.includes(":")) {
    const digits = value.split(":").map(pythonFloat);
    result = digits.includes(undefined)
      ? undefined
      : digits.reduce<number>((total, digit) => total * 60 + (digit ?? 0), 0);
  } else result = pythonFloat(value);
  if (result === undefined) throw new DocumentError(`${text} is no float`);
  if (!Number.isFinite(result)) throw new DocumentError(`${text} has no strict JSON form`);
  return sign * result;
}

/**
 * Return a text that two keys share exactly when Python finds them equal, as `1`, `1.0` and `true`
 * are. A list or a mapping is no valid key in Python.
 */
function hashKey(key: Value): string | undefined {
  if (key === null) return "null";
  if (typeof key === "string") return `s:${key}`;
  if (typeof key === "boolean") return key ? "n:1" : "n:0";
  if (typeof key === "bigint") return `n:${key}`;
  if (typeof key === "number") {
    return Number.isInteger(key) ? `n:${BigInt(key)}` : `f:${key}`;
  }
  throw new DocumentError("found an unhashable key");
}
