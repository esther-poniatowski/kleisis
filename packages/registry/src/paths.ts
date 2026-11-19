// Repository-relative paths, checked and normalized as `safe_relative_path` of the Python reader
// checks them, through `PurePosixPath`.

import { RegistryError } from "./errors.js";
import { isBlank } from "./python.js";

/**
 * Return `value` as a normalized path below the root of the repository: without empty segments
 * and without `.` segments. A value that is no non-empty string breaks the rule `registry/invalid`,
 * as in the Python reader; an absolute path, a segment `..` or the path `.` breaks `path/escape`.
 */
export function safeRelativePath(value: unknown, fieldName: string): string {
  if (typeof value !== "string" || isBlank(value)) {
    throw new RegistryError("registry/invalid", `${fieldName} must be a non-empty string`);
  }
  const parts = value.split("/").filter((part) => part !== "" && part !== ".");
  if (value.startsWith("/") || parts.includes("..") || parts.length === 0) {
    throw new RegistryError(
      "path/escape",
      `${fieldName} must stay inside the repository: ${JSON.stringify(value)}`,
    );
  }
  return parts.join("/");
}
