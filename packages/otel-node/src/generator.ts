import type { ProjectInfo, DetectedLibrary, Framework } from "./detector.js";

export interface GeneratorOptions {
  endpoint: string;
  serviceName: string;
  exporterType: "otlp-grpc" | "otlp-http" | "console";
  projectInfo: ProjectInfo;
}

function generateImports(
  libraries: DetectedLibrary[],
  exporterType: string
): string {
  const lines: string[] = [];

  lines.push(
    `import { NodeSDK } from "@opentelemetry/sdk-node";`
  );
  lines.push(
    `import { resourceFromAttributes } from "@opentelemetry/resources";`
  );
  lines.push(
    `import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";`
  );

  if (exporterType === "otlp-grpc") {
    lines.push(
      `import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-grpc";`
    );
    lines.push(
      `import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-grpc";`
    );
    lines.push(
      `import { OTLPLogExporter } from "@opentelemetry/exporter-logs-otlp-grpc";`
    );
  } else if (exporterType === "otlp-http") {
    lines.push(
      `import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";`
    );
    lines.push(
      `import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-http";`
    );
    lines.push(
      `import { OTLPLogExporter } from "@opentelemetry/exporter-logs-otlp-http";`
    );
  } else {
    lines.push(
      `import { ConsoleSpanExporter } from "@opentelemetry/sdk-trace-node";`
    );
  }

  lines.push(
    `import { PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";`
  );
  lines.push(
    `import { BatchLogRecordProcessor, LoggerProvider } from "@opentelemetry/sdk-logs";`
  );

  // Import each instrumentation
  for (const lib of libraries) {
    lines.push(
      `import { ${lib.otelInstrumentation} } from "${lib.otelPackage}";`
    );
  }

  return lines.join("\n");
}

function generateSdkSetup(options: GeneratorOptions): string {
  const { endpoint, serviceName, exporterType, projectInfo } = options;
  const { libraries } = projectInfo;

  const lines: string[] = [];

  // Endpoint from env or default
  lines.push(`const ENDPOINT = process.env.OTEL_EXPORTER_OTLP_ENDPOINT || "${endpoint}";`);
  lines.push(`const HEADERS: Record<string, string> = process.env.ROCKETGRAPH_API_KEY`);
  lines.push(`  ? { Authorization: \`Bearer \${process.env.ROCKETGRAPH_API_KEY}\` }`);
  lines.push(`  : {};`);
  lines.push("");

  // Trace exporter
  if (exporterType === "console") {
    lines.push(`const traceExporter = new ConsoleSpanExporter();`);
  } else {
    lines.push(`const traceExporter = new OTLPTraceExporter({`);
    lines.push(`  url: \`\${ENDPOINT}/v1/traces\`,`);
    lines.push(`  headers: HEADERS,`);
    lines.push(`});`);
  }

  lines.push("");

  // Metric exporter
  if (exporterType !== "console") {
    lines.push(`const metricExporter = new OTLPMetricExporter({`);
    lines.push(`  url: \`\${ENDPOINT}/v1/metrics\`,`);
    lines.push(`  headers: HEADERS,`);
    lines.push(`});`);
  }

  lines.push("");

  // Log exporter
  if (exporterType !== "console") {
    lines.push(`const logExporter = new OTLPLogExporter({`);
    lines.push(`  url: \`\${ENDPOINT}/v1/logs\`,`);
    lines.push(`  headers: HEADERS,`);
    lines.push(`});`);
    lines.push("");
    lines.push(`const loggerProvider = new LoggerProvider({`);
    lines.push(`  processors: [new BatchLogRecordProcessor(logExporter)],`);
    lines.push(`});`);
  }

  lines.push("");

  // Instrumentations array
  lines.push(`const instrumentations = [`);
  for (const lib of libraries) {
    lines.push(`  new ${lib.otelInstrumentation}(),`);
  }
  lines.push(`];`);

  lines.push("");

  // SDK initialization
  lines.push(`const sdk = new NodeSDK({`);
  lines.push(`  resource: resourceFromAttributes({`);
  lines.push(
    `    [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME || "${serviceName}",`
  );
  lines.push(`  }),`);
  lines.push(`  traceExporter,`);
  if (exporterType !== "console") {
    lines.push(`  metricReader: new PeriodicExportingMetricReader({`);
    lines.push(`    exporter: metricExporter,`);
    lines.push(`    exportIntervalMillis: 60000,`);
    lines.push(`  }),`);
    lines.push(`  logRecordProcessor: new BatchLogRecordProcessor(logExporter),`);
  }
  lines.push(`  instrumentations,`);
  lines.push(`});`);

  lines.push("");
  lines.push(`sdk.start();`);
  lines.push("");

  // Console bridge — forward console.log/warn/error to OTel logs
  if (exporterType !== "console") {
    lines.push(`// Bridge console.log/warn/error to OpenTelemetry logs`);
    lines.push(`const otelLogger = loggerProvider.getLogger("console");`);
    lines.push(`const SEVERITY: Record<string, number> = { debug: 5, info: 9, warn: 13, error: 17 };`);
    lines.push(`for (const [method, level] of [["log", "info"], ["info", "info"], ["warn", "warn"], ["error", "error"], ["debug", "debug"]] as const) {`);
    lines.push(`  const orig = console[method].bind(console);`);
    lines.push(`  console[method] = (...args: unknown[]) => {`);
    lines.push(`    orig(...args);`);
    lines.push(`    try {`);
    lines.push(`      const body = args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ");`);
    lines.push(`      otelLogger.emit({ body, severityNumber: SEVERITY[level] || 9, severityText: level.toUpperCase() });`);
    lines.push(`    } catch {}`);
    lines.push(`  };`);
    lines.push(`}`);
    lines.push("");
  }

  return lines.join("\n");
}

function generateNextjsInstrumentation(options: GeneratorOptions): string {
  const { endpoint, serviceName, exporterType, projectInfo } = options;
  const { libraries } = projectInfo;
  const isOtlp = exporterType !== "console";
  const proto = exporterType === "otlp-grpc" ? "grpc" : "http";

  const lines: string[] = [];

  lines.push(`export async function register() {`);
  lines.push(`  if (process.env.NEXT_RUNTIME === "nodejs") {`);
  lines.push(`    const { NodeSDK } = await import("@opentelemetry/sdk-node");`);
  lines.push(`    const { resourceFromAttributes } = await import("@opentelemetry/resources");`);
  lines.push(`    const { ATTR_SERVICE_NAME } = await import("@opentelemetry/semantic-conventions");`);

  if (isOtlp) {
    lines.push(`    const { OTLPTraceExporter } = await import("@opentelemetry/exporter-trace-otlp-${proto}");`);
    lines.push(`    const { OTLPMetricExporter } = await import("@opentelemetry/exporter-metrics-otlp-${proto}");`);
    lines.push(`    const { OTLPLogExporter } = await import("@opentelemetry/exporter-logs-otlp-${proto}");`);
  } else {
    lines.push(`    const { ConsoleSpanExporter } = await import("@opentelemetry/sdk-trace-node");`);
  }

  lines.push(`    const { PeriodicExportingMetricReader } = await import("@opentelemetry/sdk-metrics");`);
  lines.push(`    const { BatchLogRecordProcessor, LoggerProvider } = await import("@opentelemetry/sdk-logs");`);
  lines.push("");

  // Import instrumentations
  for (const lib of libraries) {
    lines.push(`    const { ${lib.otelInstrumentation} } = await import("${lib.otelPackage}");`);
  }

  lines.push("");

  // Endpoint + auth
  lines.push(`    const ENDPOINT = process.env.OTEL_EXPORTER_OTLP_ENDPOINT || "${endpoint}";`);
  lines.push(`    const HEADERS: Record<string, string> = process.env.ROCKETGRAPH_API_KEY`);
  lines.push(`      ? { Authorization: \`Bearer \${process.env.ROCKETGRAPH_API_KEY}\` }`);
  lines.push(`      : {};`);
  lines.push("");

  // Log exporter + provider
  if (isOtlp) {
    lines.push(`    const logExporter = new OTLPLogExporter({`);
    lines.push(`      url: \`\${ENDPOINT}/v1/logs\`,`);
    lines.push(`      headers: HEADERS,`);
    lines.push(`    });`);
    lines.push(`    const loggerProvider = new LoggerProvider({`);
    lines.push(`      processors: [new BatchLogRecordProcessor(logExporter)],`);
    lines.push(`    });`);
    lines.push("");
  }

  // SDK
  lines.push(`    const sdk = new NodeSDK({`);
  lines.push(`      resource: resourceFromAttributes({`);
  lines.push(`        [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME || "${serviceName}",`);
  lines.push(`      }),`);

  if (exporterType === "console") {
    lines.push(`      traceExporter: new ConsoleSpanExporter(),`);
  } else {
    lines.push(`      traceExporter: new OTLPTraceExporter({`);
    lines.push(`        url: \`\${ENDPOINT}/v1/traces\`,`);
    lines.push(`        headers: HEADERS,`);
    lines.push(`      }),`);
    lines.push(`      metricReader: new PeriodicExportingMetricReader({`);
    lines.push(`        exporter: new OTLPMetricExporter({`);
    lines.push(`          url: \`\${ENDPOINT}/v1/metrics\`,`);
    lines.push(`          headers: HEADERS,`);
    lines.push(`        }),`);
    lines.push(`        exportIntervalMillis: 60000,`);
    lines.push(`      }),`);
    lines.push(`      logRecordProcessor: new BatchLogRecordProcessor(logExporter),`);
  }

  lines.push(`      instrumentations: [`);
  for (const lib of libraries) {
    lines.push(`        new ${lib.otelInstrumentation}(),`);
  }
  lines.push(`      ],`);
  lines.push(`    });`);
  lines.push("");
  lines.push(`    sdk.start();`);

  // Console bridge — forward console.log/warn/error to OTel logs
  if (isOtlp) {
    lines.push("");
    lines.push(`    // Bridge console → OTel logs`);
    lines.push(`    const otelLogger = loggerProvider.getLogger("console");`);
    lines.push(`    const SEV: Record<string, number> = { debug: 5, info: 9, warn: 13, error: 17 };`);
    lines.push(`    for (const [m, l] of [["log","info"],["info","info"],["warn","warn"],["error","error"],["debug","debug"]] as const) {`);
    lines.push(`      const orig = console[m].bind(console);`);
    lines.push(`      console[m] = (...args: unknown[]) => {`);
    lines.push(`        orig(...args);`);
    lines.push(`        try {`);
    lines.push(`          const body = args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ");`);
    lines.push(`          otelLogger.emit({ body, severityNumber: SEV[l] || 9, severityText: l.toUpperCase() });`);
    lines.push(`        } catch {}`);
    lines.push(`      };`);
    lines.push(`    }`);
  }

  // Shutdown
  lines.push("");
  lines.push(`    process.on("SIGTERM", () => {`);
  lines.push(`      sdk.shutdown().catch(() => {});`);
  lines.push(`    });`);

  lines.push(`  }`);
  lines.push(`}`);

  return lines.join("\n");
}

export function generateInstrumentation(options: GeneratorOptions): string {
  const { projectInfo } = options;
  const isNextjs =
    projectInfo.framework === "nextjs" || projectInfo.framework === "nuxt";

  if (isNextjs) {
    return generateNextjsInstrumentation(options);
  }

  const imports = generateImports(
    projectInfo.libraries,
    options.exporterType
  );
  const setup = generateSdkSetup(options);

  return `${imports}\n\n${setup}\n`;
}

export function getRequiredPackages(
  projectInfo: ProjectInfo,
  exporterType: string
): string[] {
  const packages = new Set<string>();

  // Core OTel packages
  packages.add("@opentelemetry/sdk-node");
  packages.add("@opentelemetry/api");
  packages.add("@opentelemetry/resources");
  packages.add("@opentelemetry/semantic-conventions");
  packages.add("@opentelemetry/sdk-metrics");
  packages.add("@opentelemetry/sdk-logs");

  // Exporter packages
  if (exporterType === "otlp-grpc") {
    packages.add("@opentelemetry/exporter-trace-otlp-grpc");
    packages.add("@opentelemetry/exporter-metrics-otlp-grpc");
    packages.add("@opentelemetry/exporter-logs-otlp-grpc");
  } else if (exporterType === "otlp-http") {
    packages.add("@opentelemetry/exporter-trace-otlp-http");
    packages.add("@opentelemetry/exporter-metrics-otlp-http");
    packages.add("@opentelemetry/exporter-logs-otlp-http");
  } else {
    packages.add("@opentelemetry/sdk-trace-node");
  }

  // Instrumentation packages
  for (const lib of projectInfo.libraries) {
    packages.add(lib.otelPackage);
  }

  return Array.from(packages);
}

export function getNodeRequireFlag(instrumentationPath: string): string {
  return `--require ./${instrumentationPath}`;
}

export function getNodeImportFlag(instrumentationPath: string): string {
  return `--import ./${instrumentationPath}`;
}
