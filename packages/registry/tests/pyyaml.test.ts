// Verdicts that depend on how PyYAML reads YAML 1.1, outside the shared cases. Each expected
// outcome was measured on the Python reader `kleisis.registry.read_registry`.

import { describe, expect, it } from "vitest";

import { readRegistry, RegistryError } from "../src/index.js";

const HEAD = "_schema: kleisis:schema-registry@2\n";

function outcome(text: string): unknown {
  try {
    const { version, schemas, exports, resources } = readRegistry(text);
    return { version, schemas, exports, resources };
  } catch (error) {
    if (error instanceof RegistryError) return error.rule;
    throw error;
  }
}

function accepted(schemas: Record<string, string>, resources: Record<string, string> = {}) {
  return { version: 2, schemas, exports: [], resources };
}

const VERDICTS: [string, string, unknown][] = [
  ["a plain y is a string", "schemas: {a:b@1: y}", accepted({ "a:b@1": "y" })],
  ["a plain yes is a boolean", "schemas: {a:b@1: yes}", "registry/invalid"],
  ["a plain 1e3 is a string", "schemas: {a:b@1: 1e3}", accepted({ "a:b@1": "1e3" })],
  ["an integer is no path", "schemas: {a:b@1: 5}", "registry/invalid"],
  ["a blank path is no path", "schemas: {a:b@1: ' '}", "registry/invalid"],
  ["a sexagesimal integer is no path", "schemas: {a:b@1: 190:20:30}", "registry/invalid"],
  ["an ordered map is no path", "schemas: {a:b@1: !!omap [{a: b}]}", "registry/invalid"],
  ["a non-specific tag resolves a plain scalar", "schemas: {a:b@1: ! 5}", "registry/invalid"],
  ["a non-specific tag resolves a quoted scalar", 'schemas: {a:b@1: ! "5"}', "registry/invalid"],
  ["a non-specific tag resolves a quoted merge key", "schemas: {! '<<': x}", "registry/invalid"],
  ["an explicit string", "schemas: {a:b@1: !!str 5}", accepted({ "a:b@1": "5" })],
  ["an explicit map", "schemas: !!map {}", accepted({})],
  ["a timestamp has no JSON form", "schemas: {a:b@1: 2001-12-14}", "registry/invalid"],
  [
    "a timestamp key has no JSON form",
    "schemas: {}\nresources: {2001-01-01: y}",
    "registry/invalid",
  ],
  ["a NaN has no strict JSON form", "schemas: {a:b@1: .nan}", "registry/invalid"],
  ["a set has no JSON form", "schemas: {a:b@1: !!set {x}}", "registry/invalid"],
  ["a binary has no JSON form", "schemas: {a:b@1: x, !!binary YQ==: y}", "registry/invalid"],
  ["a value key `=` has no constructor", "schemas: {a:b@1: =}", "registry/invalid"],
  ["a merge value has no constructor", "schemas: {a:b@1: <<}", "registry/invalid"],
  ["a merge key is rejected", "schemas: {<<: {}}", "registry/invalid"],
  ["a quoted << is an ordinary key", "schemas: {'<<': x}", "contract/identifier"],
  ["an unknown tag has no constructor", "schemas: {a:b@1: !foo x}", "registry/invalid"],
  ["an invalid explicit integer", "schemas: {a:b@1: !!int x}", "registry/invalid"],
  ["1 and 1.0 are one key", "schemas: {1: a, 1.0: b}", "registry/invalid"],
  ["a sequence key is unhashable", "schemas: {? [a]: x}", "registry/invalid"],
  ["a recursive document", "schemas: {}\nfoo: &x [*x]", "registry/invalid"],
  ["two documents", "schemas: {}\n---\na: 1", "registry/invalid"],
  [
    "an alias",
    "schemas: &a {a:b@1: x}\nresources: *a",
    accepted({ "a:b@1": "x" }, { "a:b@1": "x" }),
  ],
  ["a path is normalized", "schemas: {a:b@1: a//b/./c/}", accepted({ "a:b@1": "a/b/c" })],
  ["the path ./. is the root", "schemas: {a:b@1: ./.}", "path/escape"],
  ["two slashes open an absolute path", "schemas: {a:b@1: //x}", "path/escape"],
  [
    "a Kelvin sign opens a scheme",
    "schemas: {}\nresources: {Ka:b: x}",
    accepted({}, { "Ka:b": "x" }),
  ],
  [
    "a dotless i opens a scheme",
    "schemas: {}\nresources: {ıa:b: x}",
    accepted({}, { "ıa:b": "x" }),
  ],
  [
    "a keyword described by \\x1c only",
    'schemas: {}\nkeywords: {x-a: "\\x1c"}',
    "registry/invalid",
  ],
  ["a keyword described by a BOM", 'schemas: {}\nkeywords: {x-a: "\\ufeff"}', accepted({})],
  ["a null keyword description", "schemas: {}\nkeywords: {x-a: ~}", "registry/invalid"],
];

const LEGACY: [string, string, unknown][] = [
  ["a float version", "schema_version: 2.0\nschemas: {}", "registry/invalid"],
  [
    "a hexadecimal version",
    "schema_version: 0x2\nschemas: {}",
    { version: 2, schemas: {}, exports: [], resources: {} },
  ],
  [
    "an octal version",
    "schema_version: 02\nschemas: {}",
    { version: 2, schemas: {}, exports: [], resources: {} },
  ],
  [
    "a binary version",
    "schema_version: 0b10\nschemas: {}",
    { version: 2, schemas: {}, exports: [], resources: {} },
  ],
  [
    "a signed version",
    "schema_version: +2\nschemas: {}",
    { version: 2, schemas: {}, exports: [], resources: {} },
  ],
  [
    "an explicit integer version",
    "schema_version: !!int '2'\nschemas: {}",
    { version: 2, schemas: {}, exports: [], resources: {} },
  ],
  ["a boolean version", "schema_version: true\nschemas: {}", "registry/invalid"],
  ["an underscored version", "schema_version: 1_0\nschemas: {}", "registry/invalid"],
  ["a sexagesimal version", "schema_version: 1:0\nschemas: {}", "registry/invalid"],
];

describe("YAML 1.1 as PyYAML reads it", () => {
  for (const [name, body, expected] of VERDICTS) {
    it(name, () => {
      expect(outcome(HEAD + body)).toEqual(expected);
    });
  }
  for (const [name, text, expected] of LEGACY) {
    it(name, () => {
      expect(outcome(text)).toEqual(expected);
    });
  }
  it("an empty file", () => {
    expect(outcome("")).toBe("registry/invalid");
  });
});
