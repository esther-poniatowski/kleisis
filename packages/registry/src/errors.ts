// Errors of the reader, each carrying the rule that the Python reader names in its finding.

/** Rule of a finding that rejects a registry, as the Python reader identifies it. */
export type Rule = "registry/invalid" | "contract/identifier" | "path/escape";

/** Every rule that the reader may report, in the order of their declaration. */
export const RULES: readonly Rule[] = ["registry/invalid", "contract/identifier", "path/escape"];

/** Rejection of a registry, with the rule broken and an actionable message. */
export class RegistryError extends Error {
  readonly rule: Rule;

  constructor(rule: Rule, message: string) {
    super(message);
    this.name = "RegistryError";
    this.rule = rule;
  }
}
