export { detectProject } from "./detector.js";
export type {
  ProjectInfo,
  Framework,
  Language,
  PackageManager,
  DetectedLibrary,
} from "./detector.js";

export {
  generateInstrumentation,
  getRequiredPackages,
  getNodeRequireFlag,
  getNodeImportFlag,
} from "./generator.js";
export type { GeneratorOptions } from "./generator.js";
