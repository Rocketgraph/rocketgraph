import { NodeSDK } from "@opentelemetry/sdk-node";
import { Resource } from "@opentelemetry/resources";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-http";
import { OTLPLogExporter } from "@opentelemetry/exporter-logs-otlp-http";
import { PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";
import { BatchLogRecordProcessor, LoggerProvider } from "@opentelemetry/sdk-logs";
import { HttpInstrumentation } from "@opentelemetry/instrumentation-http";
import { ExpressInstrumentation } from "@opentelemetry/instrumentation-express";

const ENDPOINT = process.env.OTEL_EXPORTER_OTLP_ENDPOINT || "http://localhost:4318";
const HEADERS = process.env.ROCKETGRAPH_API_KEY
  ? { Authorization: `Bearer ${process.env.ROCKETGRAPH_API_KEY}` }
  : {};

const traceExporter = new OTLPTraceExporter({
  url: `${ENDPOINT}/v1/traces`,
  headers: HEADERS,
});

const metricExporter = new OTLPMetricExporter({
  url: `${ENDPOINT}/v1/metrics`,
  headers: HEADERS,
});

const logExporter = new OTLPLogExporter({
  url: `${ENDPOINT}/v1/logs`,
  headers: HEADERS,
});

const loggerProvider = new LoggerProvider();
loggerProvider.addLogRecordProcessor(new BatchLogRecordProcessor(logExporter));

const instrumentations = [
  new HttpInstrumentation(),
  new ExpressInstrumentation(),
];

const sdk = new NodeSDK({
  resource: new Resource({
    [ATTR_SERVICE_NAME]: process.env.OTEL_SERVICE_NAME || "bookstore-app",
  }),
  traceExporter,
  metricReader: new PeriodicExportingMetricReader({
    exporter: metricExporter,
    exportIntervalMillis: 60000,
  }),
  logRecordProcessor: new BatchLogRecordProcessor(logExporter),
  instrumentations,
});

sdk.start();

// Bridge console.log/warn/error to OpenTelemetry logs
const otelLogger = loggerProvider.getLogger("console");
const SEVERITY: Record<string, number> = { debug: 5, info: 9, warn: 13, error: 17 };
for (const [method, level] of [["log", "info"], ["info", "info"], ["warn", "warn"], ["error", "error"], ["debug", "debug"]] as const) {
  const orig = console[method].bind(console);
  console[method] = (...args: unknown[]) => {
    orig(...args);
    try {
      const body = args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ");
      otelLogger.emit({ body, severityNumber: SEVERITY[level] || 9, severityText: level.toUpperCase() });
    } catch {}
  };
}

