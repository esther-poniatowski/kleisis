// Run the conformance cases of `specs/cases/contract-identifiers.yml` against the module of the
// grammar, and check that its pattern equals the pattern of the published schema.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { isContractIdentifier, parseContractIdentifier, PATTERN } from "../src/index.js";

interface Entry {
  identifier: string;
  note: string;
}

function specFile(path: string): string {
  return readFileSync(fileURLToPath(new URL(`../../../specs/${path}`, import.meta.url)), "utf8");
}

const cases = parse(specFile("cases/contract-identifiers.yml")) as {
  valid: Entry[];
  invalid: Entry[];
};
const schema = JSON.parse(specFile("schemas/contract-identifier.schema.json")) as {
  pattern: string;
};

describe("contract identifier cases", () => {
  for (const { identifier, note } of cases.valid) {
    it(`accepts ${JSON.stringify(identifier)}: ${note}`, () => {
      expect(isContractIdentifier(identifier)).toBe(true);
      const { namespace, name, version } = parseContractIdentifier(identifier);
      expect(`${namespace}:${name}@${version}`).toBe(identifier);
      expect(new RegExp(schema.pattern).test(identifier)).toBe(true);
    });
  }

  for (const { identifier, note } of cases.invalid) {
    it(`rejects ${JSON.stringify(identifier)}: ${note}`, () => {
      expect(isContractIdentifier(identifier)).toBe(false);
      expect(() => parseContractIdentifier(identifier)).toThrow(/no contract identifier/);
      expect(new RegExp(schema.pattern).test(identifier)).toBe(false);
    });
  }

  it("publishes the pattern of the schema", () => {
    expect(PATTERN).toBe(schema.pattern);
  });

  it("splits an identifier into its parts", () => {
    expect(parseContractIdentifier("gnomon:view.next-act@8")).toEqual({
      namespace: "gnomon",
      name: "view.next-act",
      version: 8,
    });
  });
});
