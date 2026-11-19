// Run the conformance cases of `specs/cases/registries.yml` against the TypeScript reader. Each
// case gives the text of a registry and either the values read or the rule of the rejection.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { readRegistry, RegistryError, RULES } from "../src/index.js";

interface Case {
  name: string;
  document: string;
  expect: { error: string } | Record<string, unknown>;
}

const casesFile = fileURLToPath(new URL("../../../specs/cases/registries.yml", import.meta.url));
const cases = (parse(readFileSync(casesFile, "utf8")) as { cases: Case[] }).cases;

function outcome(text: string): unknown {
  try {
    return readRegistry(text, "registry.yml");
  } catch (error) {
    if (error instanceof RegistryError) return { error: error.rule };
    throw error;
  }
}

describe("registry cases", () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      const expected =
        "error" in testCase.expect ? testCase.expect : { keywords: {}, ...testCase.expect };
      expect(outcome(testCase.document)).toEqual(expected);
    });
  }

  it("names only rules of the reader", () => {
    const named = cases.flatMap((testCase) =>
      "error" in testCase.expect ? [String(testCase.expect.error)] : [],
    );
    expect(named.filter((rule) => !(RULES as readonly string[]).includes(rule))).toEqual([]);
  });
});
