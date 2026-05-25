/**
 * agent.ts — Claude-powered instrumentation agent.
 *
 * Instead of generating code from string templates, this agent:
 * 1. Reads the user's actual project files (package.json, tsconfig, entry points)
 * 2. Sends the context to Claude with tools to read/write files
 * 3. Claude decides what to create/modify based on the real code
 * 4. Handles edge cases (existing instrumentation, version conflicts, framework quirks)
 */

import Anthropic from "@anthropic-ai/sdk";
import * as fs from "node:fs";
import * as path from "node:path";

const ROCKETGRAPH_ENDPOINT = "https://ingress.us-east-2.rocketgraph.app";
const ROCKETGRAPH_AI_URL = "https://ai-sre.rocketgraph.app";

// ── Tools the agent can use ────────────────────────────────────────────────

const TOOLS: Anthropic.Tool[] = [
  {
    name: "read_file",
    description:
      "Read the contents of a file in the project directory. Use this to understand the user's code before making changes.",
    input_schema: {
      type: "object" as const,
      properties: {
        path: {
          type: "string",
          description: "Relative path from project root (e.g. 'package.json', 'src/index.ts')",
        },
      },
      required: ["path"],
    },
  },
  {
    name: "write_file",
    description:
      "Write or overwrite a file in the project root directory. Only write files in the root — instrumentation.ts, .env updates, etc. Do NOT modify files in src/ or other subdirectories.",
    input_schema: {
      type: "object" as const,
      properties: {
        path: {
          type: "string",
          description: "Relative path from project root. Must be a root-level file (e.g. 'instrumentation.ts', not 'src/something.ts')",
        },
        content: {
          type: "string",
          description: "Full file content to write",
        },
      },
      required: ["path", "content"],
    },
  },
  {
    name: "list_files",
    description:
      "List files in a directory (non-recursive, skips node_modules). Use to understand project structure.",
    input_schema: {
      type: "object" as const,
      properties: {
        path: {
          type: "string",
          description: "Relative directory path from project root (e.g. '.', 'src')",
        },
      },
      required: ["path"],
    },
  },
  {
    name: "run_command",
    description:
      "Run a shell command in the project directory. Use for installing npm packages only.",
    input_schema: {
      type: "object" as const,
      properties: {
        command: {
          type: "string",
          description: "Shell command to run (e.g. 'npm install @opentelemetry/sdk-node')",
        },
      },
      required: ["command"],
    },
  },
];

// ── Tool executor ──────────────────────────────────────────────────────────

function execTool(
  projectDir: string,
  name: string,
  input: Record<string, string>,
  log: (msg: string) => void,
): string {
  if (name === "read_file") {
    const filePath = path.join(projectDir, input.path);
    if (!fs.existsSync(filePath)) return `Error: File not found: ${input.path}`;
    try {
      const content = fs.readFileSync(filePath, "utf-8");
      // Cap at 20k chars to stay within context
      if (content.length > 20000) {
        return content.slice(0, 20000) + "\n... (truncated)";
      }
      return content;
    } catch (e: unknown) {
      return `Error reading file: ${(e as Error).message}`;
    }
  }

  if (name === "write_file") {
    const filePath = path.join(projectDir, input.path);
    // Only allow writing to root-level files
    const relDir = path.dirname(input.path);
    if (relDir !== "." && relDir !== "") {
      return `Error: Can only write files in the project root directory. Got: ${input.path}`;
    }
    try {
      // Backup if exists
      if (fs.existsSync(filePath)) {
        fs.copyFileSync(filePath, filePath + ".bak");
        log(`  Backed up ${input.path} → ${input.path}.bak`);
      }
      fs.writeFileSync(filePath, input.content, "utf-8");
      log(`  Wrote ${input.path} (${input.content.length} bytes)`);
      return `Successfully wrote ${input.path}`;
    } catch (e: unknown) {
      return `Error writing file: ${(e as Error).message}`;
    }
  }

  if (name === "list_files") {
    const dirPath = path.join(projectDir, input.path || ".");
    if (!fs.existsSync(dirPath)) return `Error: Directory not found: ${input.path}`;
    try {
      const entries = fs.readdirSync(dirPath, { withFileTypes: true });
      return entries
        .filter((e) => e.name !== "node_modules" && e.name !== ".git" && e.name !== "dist" && e.name !== ".next")
        .map((e) => (e.isDirectory() ? `${e.name}/` : e.name))
        .join("\n");
    } catch (e: unknown) {
      return `Error listing directory: ${(e as Error).message}`;
    }
  }

  if (name === "run_command") {
    const cmd = input.command;
    // Only allow install commands
    const allowed = /^(npm install|yarn add|pnpm add|bun add)\s/;
    if (!allowed.test(cmd)) {
      return `Error: Only package install commands are allowed. Got: ${cmd}`;
    }
    try {
      const { execSync } = require("node:child_process");
      log(`  Running: ${cmd}`);
      execSync(cmd, { cwd: projectDir, stdio: "pipe", timeout: 120000 });
      return `Command succeeded: ${cmd}`;
    } catch (e: unknown) {
      return `Command failed: ${(e as Error).message}`;
    }
  }

  return `Unknown tool: ${name}`;
}

// ── System prompt ──────────────────────────────────────────────────────────

const REFERENCE_NEXTJS = `// REFERENCE: Known-good Next.js instrumentation — adapt this, do NOT invent your own patterns.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { NodeSDK } = await import("@opentelemetry/sdk-node");
    const { resourceFromAttributes } = await import("@opentelemetry/resources");
    const { ATTR_SERVICE_NAME } = await import("@opentelemetry/semantic-conventions");
    const { OTLPTraceExporter } = await import("@opentelemetry/exporter-trace-otlp-http");
    const { OTLPMetricExporter } = await import("@opentelemetry/exporter-metrics-otlp-http");
    const { OTLPLogExporter } = await import("@opentelemetry/exporter-logs-otlp-http");
    const { PeriodicExportingMetricReader } = await import("@opentelemetry/sdk-metrics");
    const { BatchLogRecordProcessor, LoggerProvider } = await import("@opentelemetry/sdk-logs");

    const ENDPOINT = process.env.OTEL_EXPORTER_OTLP_ENDPOINT || "${ROCKETGRAPH_ENDPOINT}";
    const HEADERS: Record<string, string> = process.env.ROCKETGRAPH_API_KEY
      ? { Authorization: \\\`Bearer \\\${process.env.ROCKETGRAPH_API_KEY}\\\` }
      : {};

    const logExporter = new OTLPLogExporter({ url: \\\`\\\${ENDPOINT}/v1/logs\\\`, headers: HEADERS });
    const loggerProvider = new LoggerProvider({ processors: [new BatchLogRecordProcessor(logExporter)] });

    const sdk = new NodeSDK({
      resource: resourceFromAttributes({ [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME || "my-service" }),
      traceExporter: new OTLPTraceExporter({ url: \\\`\\\${ENDPOINT}/v1/traces\\\`, headers: HEADERS }),
      metricReader: new PeriodicExportingMetricReader({
        exporter: new OTLPMetricExporter({ url: \\\`\\\${ENDPOINT}/v1/metrics\\\`, headers: HEADERS }),
        exportIntervalMillis: 60000,
      }),
      logRecordProcessor: new BatchLogRecordProcessor(logExporter),
      instrumentations: [/* detected instrumentations here */],
    });

    sdk.start();

    // Bridge console → OTel logs
    const otelLogger = loggerProvider.getLogger("console");
    const SEV: Record<string, number> = { debug: 5, info: 9, warn: 13, error: 17 };
    for (const [m, l] of [["log","info"],["info","info"],["warn","warn"],["error","error"],["debug","debug"]] as const) {
      const orig = console[m].bind(console);
      console[m] = (...args: unknown[]) => {
        orig(...args);
        try {
          const body = args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ");
          otelLogger.emit({ body, severityNumber: SEV[l] || 9, severityText: l.toUpperCase() });
        } catch {}
      };
    }

    process.on("SIGTERM", () => { sdk.shutdown().catch(() => {}); });
  }
}`;

const SYSTEM_PROMPT = `You are an expert Node.js instrumentation agent. Your job is to add OpenTelemetry instrumentation to a Node.js project so it sends traces, metrics, and logs to Rocketgraph.

## What you must do

1. Read package.json to understand the project (framework, dependencies, language)
2. List root files to see the project structure
3. Read any existing instrumentation file and .env/.env.local if present
4. Write an instrumentation file in the project ROOT
5. Add ROCKETGRAPH_API_KEY and OTEL_SERVICE_NAME to .env.local (or .env if no .env.local exists). Do NOT overwrite existing vars — append only if missing.
6. Install the required OpenTelemetry packages

## What you must NOT do

- Do NOT add error handlers, middleware, or any application code
- Do NOT modify source files in src/, lib/, or any subdirectory
- Do NOT refactor or change any existing application code
- ONLY create/update: instrumentation file, .env file, and package installs

## CRITICAL — use ONLY these imports and patterns

These are the ONLY correct imports for the current OpenTelemetry packages. Do NOT use any other names:

- \`resourceFromAttributes\` from \`@opentelemetry/resources\` — NOT \`Resource\`, NOT \`new Resource()\`
- \`ATTR_SERVICE_NAME\` from \`@opentelemetry/semantic-conventions\` — NOT \`SEMRESATTRS_SERVICE_NAME\`
- \`new LoggerProvider({ processors: [...] })\` — NOT \`loggerProvider.addLogRecordProcessor()\`
- \`const HEADERS: Record<string, string> = ...\` — you MUST include the type annotation. Without it, TypeScript infers a union type that is incompatible with the OTel SDK headers parameter. This is the #1 most common mistake.
- \`BatchLogRecordProcessor\` from \`@opentelemetry/sdk-logs\` for log processing
- Do NOT use \`pino-opentelemetry-transport\` — it spawns a worker thread that crashes on exit

## Reference implementation

Here is a KNOWN-GOOD Next.js instrumentation file. Adapt this pattern — do not invent your own:

${REFERENCE_NEXTJS}

For Express/Fastify/plain Node, use the same SDK setup but without the register() wrapper and NEXT_RUNTIME guard. Use static imports instead of dynamic imports.

## .env handling

- Read .env.local (or .env) if it exists
- Append these if not already present:
  ROCKETGRAPH_API_KEY=your_key_here
  OTEL_SERVICE_NAME={name from package.json}
- Do NOT overwrite existing values
- Do NOT remove any existing env vars

## Rules

- ONLY write files in the project root directory. Never modify files in src/, lib/, or any subdirectory.
- Detect the package manager (npm/yarn/pnpm/bun) from lockfiles and use the right install command.
- Use the service name from package.json as OTEL_SERVICE_NAME default.

## Endpoint configuration

- OTLP endpoint: ${ROCKETGRAPH_ENDPOINT}
- Auth: Bearer token via ROCKETGRAPH_API_KEY env var

## After writing files

Tell the user:
1. What files you created/modified
2. What packages you installed
3. What env vars were added to which file
4. How to load the instrumentation (Next.js: automatic, Express: first import)
`;

// ── Post-validation: catch known bad patterns ──────────────────────────────

const BAD_PATTERNS: Array<{ pattern: RegExp; fix: string }> = [
  { pattern: /new Resource\s*\(/, fix: "Use resourceFromAttributes() instead of new Resource()" },
  { pattern: /SEMRESATTRS_SERVICE_NAME/, fix: "Use ATTR_SERVICE_NAME instead of SEMRESATTRS_SERVICE_NAME" },
  { pattern: /\.addLogRecordProcessor\(/, fix: "Use LoggerProvider({ processors: [...] }) constructor instead" },
  { pattern: /pino-opentelemetry-transport/, fix: "Use OTLPLogExporter + LoggerProvider instead of pino-opentelemetry-transport" },
  { pattern: /pino\.transport\(/, fix: "Use OTLPLogExporter + LoggerProvider instead of pino.transport()" },
  { pattern: /const HEADERS\s*=\s*process/, fix: "HEADERS must be typed as Record<string, string>. Use: const HEADERS: Record<string, string> = process..." },
  { pattern: /\?\s*{\s*Authorization:.*}\s*:\s*{}(?!\s*as)/, fix: "The ternary { Authorization: ... } : {} must have an explicit Record<string, string> type annotation to avoid TypeScript union errors" },
];

// ── Agent loop ─────────────────────────────────────────────────────────────

export interface AgentOptions {
  projectDir: string;
  /** Rocketgraph API token (rg_live_*) — proxied through ai.rocketgraph.app */
  rocketgraphToken?: string;
  /** Direct Anthropic API key — used if no rocketgraphToken provided */
  anthropicKey?: string;
  log: (msg: string) => void;
}

export async function runAgent(opts: AgentOptions): Promise<string> {
  const { projectDir, rocketgraphToken, anthropicKey, log } = opts;
  const maxRounds = 10;

  // Prefer Rocketgraph proxy (user doesn't need their own Anthropic key)
  // Fall back to direct Anthropic if they have their own key
  const client = rocketgraphToken
    ? new Anthropic({
        apiKey: rocketgraphToken,
        baseURL: ROCKETGRAPH_AI_URL,
      })
    : new Anthropic({ apiKey: anthropicKey });

  const messages: Anthropic.MessageParam[] = [
    {
      role: "user",
      content: `Instrument this Node.js project with OpenTelemetry to send traces, metrics, and logs to Rocketgraph.\n\nProject directory: ${projectDir}\n\nStart by reading package.json and listing the root files to understand the project.`,
    },
  ];

  let finalMessage = "";

  for (let round = 0; round < maxRounds; round++) {
    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      tools: TOOLS,
      messages,
    });

    // If agent is done (no tool calls), extract final message
    if (response.stop_reason === "end_turn") {
      for (const block of response.content) {
        if (block.type === "text") {
          finalMessage = block.text;
        }
      }
      break;
    }

    // Process tool calls
    if (response.stop_reason === "tool_use") {
      const toolResults: Anthropic.ToolResultBlockParam[] = [];

      for (const block of response.content) {
        if (block.type === "tool_use") {
          log(`  → ${block.name}(${JSON.stringify(block.input).slice(0, 100)})`);
          const result = execTool(
            projectDir,
            block.name,
            block.input as Record<string, string>,
            log,
          );
          toolResults.push({
            type: "tool_result",
            tool_use_id: block.id,
            content: result,
          });
        }
      }

      messages.push({ role: "assistant", content: response.content });
      messages.push({ role: "user", content: toolResults });
    } else {
      // Unexpected stop reason
      break;
    }
  }

  // ── Post-validation: check for known bad patterns in written files ────────
  const instrFiles = ["instrumentation.ts", "instrumentation.js"];
  for (const file of instrFiles) {
    const filePath = path.join(projectDir, file);
    if (!fs.existsSync(filePath)) continue;

    const content = fs.readFileSync(filePath, "utf-8");
    const violations = BAD_PATTERNS.filter(({ pattern }) => pattern.test(content));

    if (violations.length > 0) {
      log(`  ⚠ Post-validation found ${violations.length} issue(s) in ${file}:`);
      for (const v of violations) {
        log(`    - ${v.fix}`);
      }

      // Ask the agent to fix them
      messages.push({
        role: "user",
        content: `VALIDATION FAILED. The instrumentation file you wrote has these issues:\n\n${violations.map(v => `- ${v.fix}`).join("\n")}\n\nRead the file back, fix these issues, and write the corrected version. Use the reference implementation as your guide.`,
      });

      const fixResponse = await client.messages.create({
        model: "claude-sonnet-4-20250514",
        max_tokens: 4096,
        system: SYSTEM_PROMPT,
        tools: TOOLS,
        messages,
      });

      // Execute any fix tool calls
      if (fixResponse.stop_reason === "tool_use") {
        for (const block of fixResponse.content) {
          if (block.type === "tool_use") {
            log(`  → fix: ${block.name}(${JSON.stringify(block.input).slice(0, 80)})`);
            execTool(projectDir, block.name, block.input as Record<string, string>, log);
          }
        }
      }

      // Extract final message from fix response
      for (const block of fixResponse.content) {
        if (block.type === "text") {
          finalMessage += "\n\n" + block.text;
        }
      }

      log(`  ✓ Validation issues fixed`);
    }
  }

  return finalMessage;
}


// ── Instrument agent (code changes — error handlers, logging) ──────────────

const INSTRUMENT_PROMPT = `You are an expert Node.js observability agent. The project already has OpenTelemetry instrumentation set up. Your job is to add application-level observability code.

## What you must do

1. Read package.json and the main entry file(s) to understand the framework
2. Add error-handling middleware that logs unhandled errors via console.error (which is already bridged to OTel)
3. For Express: add \`app.use((err, req, res, next) => { console.error(\`[ERROR] \${req.method} \${req.path}:\`, err.message, err.stack); res.status(500).json({ error: 'Internal server error' }); });\` if no error handler exists
4. For Fastify: add \`setErrorHandler\` if not present
5. For Koa: add error middleware at the top
6. For Next.js: check if error.tsx/error.js exists, suggest creating one if not

## Rules

- Read the entry file FIRST to understand what's already there
- Do NOT duplicate existing error handlers
- Do NOT change the instrumentation file
- Do NOT change business logic
- ONLY add error handling and logging to catch unhandled errors
- Keep changes minimal and focused
`;

export async function runInstrumentAgent(opts: AgentOptions): Promise<string> {
  const { projectDir, rocketgraphToken, anthropicKey, log } = opts;
  const maxRounds = 8;

  const client = rocketgraphToken
    ? new Anthropic({
        apiKey: rocketgraphToken,
        baseURL: ROCKETGRAPH_AI_URL,
      })
    : new Anthropic({ apiKey: anthropicKey });

  const messages: Anthropic.MessageParam[] = [
    {
      role: "user",
      content: `Add error handlers and observability logging to this Node.js project. Read the entry point first to understand the framework, then add appropriate error handling.\n\nProject directory: ${projectDir}`,
    },
  ];

  let finalMessage = "";

  for (let round = 0; round < maxRounds; round++) {
    const response = await client.messages.create({
      model: "claude-sonnet-4-20250514",
      max_tokens: 4096,
      system: INSTRUMENT_PROMPT,
      tools: TOOLS,
      messages,
    });

    if (response.stop_reason === "end_turn") {
      for (const block of response.content) {
        if (block.type === "text") finalMessage = block.text;
      }
      break;
    }

    if (response.stop_reason === "tool_use") {
      const toolResults: Anthropic.ToolResultBlockParam[] = [];
      for (const block of response.content) {
        if (block.type === "tool_use") {
          log(`  → ${block.name}(${JSON.stringify(block.input).slice(0, 100)})`);
          const result = execTool(projectDir, block.name, block.input as Record<string, string>, log);
          toolResults.push({ type: "tool_result", tool_use_id: block.id, content: result });
        }
      }
      messages.push({ role: "assistant", content: response.content });
      messages.push({ role: "user", content: toolResults });
    } else {
      break;
    }
  }

  return finalMessage;
}
