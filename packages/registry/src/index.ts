// Public interface of @kleisis/registry. No module of the package imports Node, so that the
// package runs in Obsidian on mobile.

export {
  isContractIdentifier,
  parseContractIdentifier,
  PATTERN,
  type ContractIdentifier,
} from "./contracts.js";
export { RegistryError, RULES, type Rule } from "./errors.js";
export { readRegistry, REGISTRY_FORMAT, type Registry } from "./registry.js";
