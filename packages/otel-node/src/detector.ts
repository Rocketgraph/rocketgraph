import * as fs from "node:fs";
import * as path from "node:path";

export type Framework =
  | "express"
  | "fastify"
  | "nestjs"
  | "koa"
  | "hapi"
  | "nextjs"
  | "nuxt"
  | "restify"
  | "unknown";

export type PackageManager = "npm" | "yarn" | "pnpm" | "bun";
export type Language = "typescript" | "javascript";

export interface DetectedLibrary {
  name: string;
  pkg: string;
  otelInstrumentation: string;
  otelPackage: string;
}

export interface ProjectInfo {
  framework: Framework;
  language: Language;
  packageManager: PackageManager;
  libraries: DetectedLibrary[];
  hasExistingOtel: boolean;
  entryPoint: string | null;
  instrumentationPath: string;
  srcDir: string | null;
}

const FRAMEWORK_DETECTORS: Record<string, Framework> = {
  express: "express",
  fastify: "fastify",
  "@nestjs/core": "nestjs",
  koa: "koa",
  "@hapi/hapi": "hapi",
  next: "nextjs",
  nuxt: "nuxt",
  restify: "restify",
};

const LIBRARY_MAP: {
  pkg: string;
  name: string;
  otelInstrumentation: string;
  otelPackage: string;
}[] = [
  // HTTP
  {
    pkg: "http",
    name: "HTTP",
    otelInstrumentation: "HttpInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-http",
  },
  // Frameworks
  {
    pkg: "express",
    name: "Express",
    otelInstrumentation: "ExpressInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-express",
  },
  {
    pkg: "fastify",
    name: "Fastify",
    otelInstrumentation: "FastifyInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-fastify",
  },
  {
    pkg: "@nestjs/core",
    name: "NestJS",
    otelInstrumentation: "NestInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-nestjs-core",
  },
  {
    pkg: "koa",
    name: "Koa",
    otelInstrumentation: "KoaInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-koa",
  },
  {
    pkg: "@hapi/hapi",
    name: "Hapi",
    otelInstrumentation: "HapiInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-hapi",
  },
  {
    pkg: "restify",
    name: "Restify",
    otelInstrumentation: "RestifyInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-restify",
  },
  // Databases
  {
    pkg: "pg",
    name: "PostgreSQL (pg)",
    otelInstrumentation: "PgInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-pg",
  },
  {
    pkg: "mysql",
    name: "MySQL",
    otelInstrumentation: "MySQLInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-mysql",
  },
  {
    pkg: "mysql2",
    name: "MySQL2",
    otelInstrumentation: "MySQL2Instrumentation",
    otelPackage: "@opentelemetry/instrumentation-mysql2",
  },
  {
    pkg: "mongodb",
    name: "MongoDB",
    otelInstrumentation: "MongoDBInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-mongodb",
  },
  {
    pkg: "mongoose",
    name: "Mongoose",
    otelInstrumentation: "MongooseInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-mongoose",
  },
  {
    pkg: "@prisma/client",
    name: "Prisma",
    otelInstrumentation: "PrismaInstrumentation",
    otelPackage: "@prisma/instrumentation",
  },
  {
    pkg: "typeorm",
    name: "TypeORM",
    otelInstrumentation: "TypeormInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-typeorm",
  },
  {
    pkg: "knex",
    name: "Knex",
    otelInstrumentation: "KnexInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-knex",
  },
  {
    pkg: "sequelize",
    name: "Sequelize",
    otelInstrumentation: "SequelizeInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-sequelize",
  },
  // Cache
  {
    pkg: "redis",
    name: "Redis",
    otelInstrumentation: "RedisInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-redis-4",
  },
  {
    pkg: "ioredis",
    name: "IORedis",
    otelInstrumentation: "IORedisInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-ioredis",
  },
  {
    pkg: "memcached",
    name: "Memcached",
    otelInstrumentation: "MemcachedInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-memcached",
  },
  // Messaging
  {
    pkg: "amqplib",
    name: "RabbitMQ (amqplib)",
    otelInstrumentation: "AmqplibInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-amqplib",
  },
  {
    pkg: "kafkajs",
    name: "KafkaJS",
    otelInstrumentation: "KafkaJsInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-kafkajs",
  },
  {
    pkg: "@aws-sdk/client-sqs",
    name: "AWS SQS",
    otelInstrumentation: "AwsInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-aws-sdk",
  },
  // AWS SDK
  {
    pkg: "@aws-sdk/client-s3",
    name: "AWS SDK v3",
    otelInstrumentation: "AwsInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-aws-sdk",
  },
  {
    pkg: "aws-sdk",
    name: "AWS SDK v2",
    otelInstrumentation: "AwsInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-aws-sdk",
  },
  // gRPC
  {
    pkg: "@grpc/grpc-js",
    name: "gRPC",
    otelInstrumentation: "GrpcInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-grpc",
  },
  // GraphQL
  {
    pkg: "graphql",
    name: "GraphQL",
    otelInstrumentation: "GraphQLInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-graphql",
  },
  // DNS & Net
  {
    pkg: "dns",
    name: "DNS",
    otelInstrumentation: "DnsInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-dns",
  },
  // Fetch / undici
  {
    pkg: "undici",
    name: "Undici (fetch)",
    otelInstrumentation: "UndiciInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-undici",
  },
  // Winston / Pino / Bunyan logging
  {
    pkg: "winston",
    name: "Winston",
    otelInstrumentation: "WinstonInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-winston",
  },
  {
    pkg: "pino",
    name: "Pino",
    otelInstrumentation: "PinoInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-pino",
  },
  {
    pkg: "bunyan",
    name: "Bunyan",
    otelInstrumentation: "BunyanInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-bunyan",
  },
  // Socket.io
  {
    pkg: "socket.io",
    name: "Socket.IO",
    otelInstrumentation: "SocketIoInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-socket.io",
  },
  // Generic fetch (Node 18+)
  {
    pkg: "node-fetch",
    name: "node-fetch",
    otelInstrumentation: "UndiciInstrumentation",
    otelPackage: "@opentelemetry/instrumentation-undici",
  },
];

function readPackageJson(projectDir: string): Record<string, unknown> | null {
  const pkgPath = path.join(projectDir, "package.json");
  if (!fs.existsSync(pkgPath)) return null;
  return JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
}

function getAllDependencies(pkg: Record<string, unknown>): Set<string> {
  const deps = new Set<string>();
  for (const field of ["dependencies", "devDependencies", "peerDependencies"]) {
    const section = pkg[field];
    if (section && typeof section === "object") {
      for (const name of Object.keys(section as Record<string, string>)) {
        deps.add(name);
      }
    }
  }
  return deps;
}

function detectPackageManager(projectDir: string): PackageManager {
  if (fs.existsSync(path.join(projectDir, "bun.lockb")) || fs.existsSync(path.join(projectDir, "bun.lock")))
    return "bun";
  if (fs.existsSync(path.join(projectDir, "pnpm-lock.yaml"))) return "pnpm";
  if (fs.existsSync(path.join(projectDir, "yarn.lock"))) return "yarn";
  return "npm";
}

function detectLanguage(projectDir: string, deps: Set<string>): Language {
  if (
    fs.existsSync(path.join(projectDir, "tsconfig.json")) ||
    deps.has("typescript")
  ) {
    return "typescript";
  }
  return "javascript";
}

function detectFramework(deps: Set<string>): Framework {
  for (const [pkg, fw] of Object.entries(FRAMEWORK_DETECTORS)) {
    if (deps.has(pkg)) return fw;
  }
  return "unknown";
}

function detectLibraries(deps: Set<string>): DetectedLibrary[] {
  const found: DetectedLibrary[] = [];
  const seenPackages = new Set<string>();

  // Always include HTTP instrumentation
  found.push(LIBRARY_MAP.find((l) => l.pkg === "http")!);
  seenPackages.add("@opentelemetry/instrumentation-http");

  for (const lib of LIBRARY_MAP) {
    if (lib.pkg === "http") continue;
    if (deps.has(lib.pkg) && !seenPackages.has(lib.otelPackage)) {
      found.push(lib);
      seenPackages.add(lib.otelPackage);
    }
  }

  return found;
}

function detectEntryPoint(
  projectDir: string,
  pkg: Record<string, unknown>
): string | null {
  // Check package.json main field
  if (typeof pkg.main === "string") return pkg.main;

  // Common entry points
  const candidates = [
    "src/index.ts",
    "src/index.js",
    "src/main.ts",
    "src/main.js",
    "src/server.ts",
    "src/server.js",
    "src/app.ts",
    "src/app.js",
    "index.ts",
    "index.js",
    "server.ts",
    "server.js",
    "app.ts",
    "app.js",
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(path.join(projectDir, candidate))) return candidate;
  }

  return null;
}

function detectSrcDir(projectDir: string): string | null {
  if (fs.existsSync(path.join(projectDir, "src"))) return "src";
  return null;
}

function figureInstrumentationPath(
  framework: Framework,
  language: Language,
  srcDir: string | null
): string {
  const ext = language === "typescript" ? ".ts" : ".js";

  // Next.js expects instrumentation at the root
  if (framework === "nextjs" || framework === "nuxt") {
    return `instrumentation${ext}`;
  }

  // NestJS typically has src/
  if (srcDir) {
    return `${srcDir}/instrumentation${ext}`;
  }

  return `instrumentation${ext}`;
}

function hasExistingOtel(deps: Set<string>): boolean {
  return (
    deps.has("@opentelemetry/sdk-node") ||
    deps.has("@opentelemetry/api") ||
    deps.has("@opentelemetry/sdk-trace-node")
  );
}

export function detectProject(projectDir: string): ProjectInfo {
  const pkg = readPackageJson(projectDir);
  if (!pkg) {
    throw new Error(
      `No package.json found in ${projectDir}. Is this a Node.js project?`
    );
  }

  const deps = getAllDependencies(pkg);
  const framework = detectFramework(deps);
  const language = detectLanguage(projectDir, deps);
  const packageManager = detectPackageManager(projectDir);
  const libraries = detectLibraries(deps);
  const entryPoint = detectEntryPoint(projectDir, pkg);
  const srcDir = detectSrcDir(projectDir);
  const instrumentationPath = figureInstrumentationPath(
    framework,
    language,
    srcDir
  );

  return {
    framework,
    language,
    packageManager,
    libraries,
    hasExistingOtel: hasExistingOtel(deps),
    entryPoint,
    instrumentationPath,
    srcDir,
  };
}
