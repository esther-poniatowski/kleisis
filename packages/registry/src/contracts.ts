// Grammar of a contract identifier, `<namespace>:<name>@<version>`, as the Python module
// `kleisis.contracts` states it. The schema `specs/schemas/contract-identifier.schema.json`
// publishes the same pattern, and the cases of `specs/cases/contract-identifiers.yml` fix its
// verdicts.

const WORD = "[a-z][a-z0-9]*(?:-[a-z0-9]+)*";

/**
 * Pattern of a contract identifier, as the schema of the grammar publishes it. ECMAScript lets `$`
 * match only at the very end of the input, so a final line break is rejected.
 */
export const PATTERN = `^${WORD}:${WORD}(?:\\.${WORD})*@[1-9][0-9]*$`;

const IDENTIFIER = new RegExp(`^(${WORD}):(${WORD}(?:\\.${WORD})*)@([1-9][0-9]*)$`);

/** A contract identifier split into its three parts. */
export interface ContractIdentifier {
  readonly namespace: string;
  readonly name: string;
  /** The version, exact up to `Number.MAX_SAFE_INTEGER`. */
  readonly version: number;
}

/** Return whether `text` is a string that follows the grammar of a contract identifier. */
export function isContractIdentifier(text: unknown): text is string {
  return typeof text === "string" && IDENTIFIER.test(text);
}

/** Return the parts of a contract identifier, or throw an error naming the grammar. */
export function parseContractIdentifier(text: unknown): ContractIdentifier {
  const match = typeof text === "string" ? IDENTIFIER.exec(text) : null;
  if (match === null) {
    throw new Error(
      `${typeof text === "string" ? JSON.stringify(text) : typeof text} is no contract ` +
        "identifier: write <namespace>:<name>@<version>, with words in lower kebab case, " +
        "words of the name joined by '.', and a positive version",
    );
  }
  const [, namespace = "", name = "", version = ""] = match;
  return { namespace, name, version: Number(version) };
}
