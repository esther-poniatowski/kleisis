// Python semantics that the Python reader relies on: the whitespace of `str.strip`, and the
// conversions `int(text, base)` and `float(text)` that PyYAML applies to a scalar.

/** The characters that `str.isspace` accepts in Python, which `str.strip` removes. */
const WHITESPACE =
  "\\t\\n\\u000b\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029" +
  "\\u202f\\u205f\\u3000";
const BLANK = new RegExp(`^[${WHITESPACE}]*$`);
const PADDING = new RegExp(`^[${WHITESPACE}]+|[${WHITESPACE}]+$`, "g");

/** Return whether `text.strip()` is empty in Python. */
export function isBlank(text: string): boolean {
  return BLANK.test(text);
}

function strip(text: string): string {
  return text.replace(PADDING, "");
}

const DIGITS: Record<2 | 8 | 10 | 16, string> = {
  2: "^([+-]?)(?:0b)?([01]+)$",
  8: "^([+-]?)(?:0o)?([0-7]+)$",
  10: "^([+-]?)([0-9]+)$",
  16: "^([+-]?)(?:0x)?([0-9a-f]+)$",
};
const PREFIX: Record<2 | 8 | 10 | 16, string> = { 2: "0b", 8: "0o", 10: "", 16: "0x" };

/**
 * Return `int(text, base)` of Python for a text without underscores, or `undefined` where Python
 * raises `ValueError`. Python also reads digits of other scripts in base 10; this reader does not.
 */
export function pythonInt(text: string, base: 2 | 8 | 10 | 16): bigint | undefined {
  const match = new RegExp(DIGITS[base], "i").exec(strip(text));
  if (match === null) return undefined;
  const sign = match[1] === "-" ? -1n : 1n;
  const digits = match[2] ?? "";
  return sign * BigInt(`${PREFIX[base]}${digits}`);
}

const FLOAT = /^[+-]?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:e[+-]?[0-9]+)?|inf|infinity|nan)$/i;

/** Return `float(text)` of Python for a text without underscores, or `undefined` on `ValueError`. */
export function pythonFloat(text: string): number | undefined {
  const value = strip(text);
  if (!FLOAT.test(value)) return undefined;
  const body = value.replace(/^[+-]/, "").toLowerCase();
  const sign = value.startsWith("-") ? -1 : 1;
  if (body === "inf" || body === "infinity") return sign * Infinity;
  if (body === "nan") return NaN;
  return Number(value);
}
