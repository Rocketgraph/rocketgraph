#!/usr/bin/env node

import { Command } from "commander";
import * as fs from "node:fs";
import * as path from "node:path";
import { execSync } from "node:child_process";
import { detectProject } from "./detector.js";
import {
  generateInstrumentation,
  getRequiredPackages,
  type GeneratorOptions,
} from "./generator.js";
import { runAgent } from "./agent.js";

const program = new Command();

program
  .name("otel-node")
  .description(
    "AI agent that auto-instruments any Node.js backend with OpenTelemetry"
  )
  .version("0.4.1");

program
  .command("init")
  .description("AI agent reads your code and instruments it with OpenTelemetry")
  .option("-d, --dir <path>", "Project directory", process.cwd())
  .option("--legacy", "Use template-based generator instead of AI agent")
  .option(
    "-e, --endpoint <url>",
    "OTLP endpoint URL (legacy mode)",
    "http://localhost:4318"
  )
  .option(
    "-n, --service-name <name>",
    "Service name for traces (legacy mode)"
  )
  .option(
    "-t, --exporter <type>",
    "Exporter type: otlp-http, otlp-grpc, console (legacy mode)",
    "otlp-http"
  )
  .option("--dry-run", "Show what would be done without making changes (legacy mode)")
  .option("--skip-install", "Skip package installation (legacy mode)")
  .action(async (opts) => {
    // ── Agent mode (default) ────────────────────────────────────────
    if (!opts.legacy) {
      const { default: chalk } = await import("chalk");
      const { default: ora } = await import("ora");

      const projectDir = path.resolve(opts.dir);

      console.log("");
      console.log(chalk.bold("🔭 @rocketgraph/otel-node"));
      console.log(chalk.dim("AI agent — reads your code, writes the right instrumentation\n"));

      const rocketgraphToken = process.env.ROCKETGRAPH_API_KEY;
      const anthropicKey = process.env.ANTHROPIC_API_KEY; // internal/dev only

      if (!rocketgraphToken && !anthropicKey) {
        console.log(chalk.red("  Missing ROCKETGRAPH_API_KEY environment variable.\n"));
        console.log(chalk.dim("  Get your key at https://rocketgraph.app/settings"));
        console.log(chalk.dim("  Then run:\n"));
        console.log(`    export ROCKETGRAPH_API_KEY=${chalk.cyan("rg_live_xxxxxx")}`);
        console.log(`    npx @rgraph/otel-node init\n`);
        console.log(chalk.dim("  Or run with --legacy for template mode (no AI).\n"));
        process.exit(1);
      }

      const spinner = ora("Agent is reading your project...").start();
      const logs: string[] = [];

      try {
        const summary = await runAgent({
          projectDir,
          rocketgraphToken,
          anthropicKey,
          log: (msg) => {
            logs.push(msg);
            spinner.text = msg.trim();
          },
        });

        spinner.succeed("Instrumentation complete");
        console.log("");

        // Show what the agent did
        for (const l of logs) {
          console.log(chalk.dim(l));
        }

        console.log("");
        console.log(summary);
        console.log("");
      } catch (err: unknown) {
        spinner.fail("Agent failed");
        console.error(chalk.red((err as Error).message));
        console.log(chalk.dim("\n  Try --legacy mode as a fallback.\n"));
        process.exit(1);
      }

      return;
    }

    // ── Legacy template mode ────────────────────────────────────────
    const { default: chalk } = await import("chalk");
    const { default: ora } = await import("ora");

    const projectDir = path.resolve(opts.dir);

    console.log("");
    console.log(chalk.bold("🔭 @rocketgraph/otel-node"));
    console.log(chalk.dim("Auto-instrumenting your Node.js backend with OpenTelemetry\n"));

    // Step 1: Detect project
    const spinner = ora("Detecting project structure...").start();

    let projectInfo;
    try {
      projectInfo = detectProject(projectDir);
      spinner.succeed("Project detected");
    } catch (err: unknown) {
      spinner.fail((err as Error).message);
      process.exit(1);
    }

    // Show detection results
    console.log("");
    console.log(chalk.bold("  Detected:"));
    console.log(`    Framework:       ${chalk.cyan(projectInfo.framework)}`);
    console.log(`    Language:        ${chalk.cyan(projectInfo.language)}`);
    console.log(`    Package Manager: ${chalk.cyan(projectInfo.packageManager)}`);
    console.log(
      `    Libraries:       ${chalk.cyan(projectInfo.libraries.map((l) => l.name).join(", "))}`
    );
    console.log(
      `    Instrumentation: ${chalk.cyan(projectInfo.instrumentationPath)}`
    );
    if (projectInfo.hasExistingOtel) {
      console.log(
        chalk.yellow("\n  ⚠ Existing OpenTelemetry packages detected. Will merge configuration.")
      );
    }
    console.log("");

    // Determine service name
    const serviceName =
      opts.serviceName || getServiceNameFromPkg(projectDir) || "my-service";

    const exporterType = opts.exporter as
      | "otlp-grpc"
      | "otlp-http"
      | "console";

    const generatorOpts: GeneratorOptions = {
      endpoint: opts.endpoint,
      serviceName,
      exporterType,
      projectInfo,
    };

    // Step 2: Generate instrumentation file
    const instrumentationCode = generateInstrumentation(generatorOpts);
    const instrumentationFullPath = path.join(
      projectDir,
      projectInfo.instrumentationPath
    );

    if (opts.dryRun) {
      console.log(chalk.bold("  Generated instrumentation file:"));
      console.log(chalk.dim(`  → ${projectInfo.instrumentationPath}`));
      console.log("");
      console.log(chalk.dim(instrumentationCode));
      console.log("");

      const packages = getRequiredPackages(projectInfo, exporterType);
      console.log(chalk.bold("  Packages to install:"));
      for (const pkg of packages) {
        console.log(`    ${chalk.green("+")} ${pkg}`);
      }
      console.log("");
      return;
    }

    // Write instrumentation file
    const writeSpinner = ora("Writing instrumentation file...").start();
    if (fs.existsSync(instrumentationFullPath)) {
      writeSpinner.warn(
        `${projectInfo.instrumentationPath} already exists — backing up to ${projectInfo.instrumentationPath}.bak`
      );
      fs.copyFileSync(
        instrumentationFullPath,
        instrumentationFullPath + ".bak"
      );
    }
    fs.mkdirSync(path.dirname(instrumentationFullPath), { recursive: true });
    fs.writeFileSync(instrumentationFullPath, instrumentationCode, "utf-8");
    writeSpinner.succeed(
      `Written ${projectInfo.instrumentationPath}`
    );

    // Step 3: Install packages
    if (!opts.skipInstall) {
      const packages = getRequiredPackages(projectInfo, exporterType);
      const installSpinner = ora(
        `Installing ${packages.length} OpenTelemetry packages...`
      ).start();

      const installCmd = getInstallCommand(
        projectInfo.packageManager,
        packages
      );

      try {
        execSync(installCmd, {
          cwd: projectDir,
          stdio: "pipe",
        });
        installSpinner.succeed(
          `Installed ${packages.length} packages`
        );
      } catch (err: unknown) {
        installSpinner.fail("Failed to install packages");
        console.error(chalk.red((err as Error).message));
        console.log("");
        console.log(chalk.dim("  Run manually:"));
        console.log(chalk.dim(`  ${installCmd}`));
        console.log("");
      }
    }

    // Step 4: Show next steps
    console.log("");
    console.log(chalk.bold("  ✅ Done! Next steps:\n"));

    if (projectInfo.framework === "nextjs") {
      console.log(
        `  Next.js will auto-load ${chalk.cyan(projectInfo.instrumentationPath)}`
      );
      console.log(
        `  Make sure ${chalk.cyan("experimental.instrumentationHook")} is enabled in next.config.js`
      );
    } else {
      console.log(`  Add the ${chalk.cyan("--require")} or ${chalk.cyan("--import")} flag to your start command:`);
      console.log("");
      if (projectInfo.language === "typescript") {
        console.log(
          chalk.dim(
            `    node --import ./\${outDir}/${path.basename(projectInfo.instrumentationPath, ".ts")}.js your-app.js`
          )
        );
        console.log("");
        console.log(`  Or with ts-node / tsx:`);
        console.log(
          chalk.dim(
            `    node --require ts-node/register --require ./${projectInfo.instrumentationPath} your-app.ts`
          )
        );
        console.log(
          chalk.dim(
            `    tsx --require ./${projectInfo.instrumentationPath} your-app.ts`
          )
        );
      } else {
        console.log(
          chalk.dim(
            `    node --require ./${projectInfo.instrumentationPath} your-app.js`
          )
        );
      }
    }

    console.log("");
    console.log(
      `  Set ${chalk.cyan("OTEL_SERVICE_NAME")} env var to override the service name.`
    );
    console.log(
      `  Set ${chalk.cyan("OTEL_EXPORTER_OTLP_ENDPOINT")} to override the endpoint at runtime.`
    );
    console.log("");
  });

program
  .command("instrument")
  .description("AI agent adds error handlers and observability code to your application")
  .option("-d, --dir <path>", "Project directory", process.cwd())
  .action(async (opts) => {
    const { default: chalk } = await import("chalk");
    const { default: ora } = await import("ora");

    const projectDir = path.resolve(opts.dir);

    console.log("");
    console.log(chalk.bold("🔭 @rocketgraph/otel-node instrument"));
    console.log(chalk.dim("AI agent — adds error handlers and observability to your app code\n"));

    const rocketgraphToken = process.env.ROCKETGRAPH_API_KEY;
    const anthropicKey = process.env.ANTHROPIC_API_KEY;

    if (!rocketgraphToken && !anthropicKey) {
      console.log(chalk.red("  Missing ROCKETGRAPH_API_KEY environment variable.\n"));
      console.log(chalk.dim("  Get your key at https://rocketgraph.app/settings\n"));
      process.exit(1);
    }

    const spinner = ora("Agent is analyzing your code...").start();
    const logs: string[] = [];

    try {
      // Use a different prompt for code instrumentation
      const { runInstrumentAgent } = await import("./agent.js");
      const summary = await runInstrumentAgent({
        projectDir,
        rocketgraphToken,
        anthropicKey,
        log: (msg) => {
          logs.push(msg);
          spinner.text = msg.trim();
        },
      });

      spinner.succeed("Instrumentation complete");
      console.log("");
      for (const l of logs) {
        console.log(chalk.dim(l));
      }
      console.log("");
      console.log(summary);
      console.log("");
    } catch (err: unknown) {
      spinner.fail("Agent failed");
      console.error(chalk.red((err as Error).message));
      process.exit(1);
    }
  });

program
  .command("detect")
  .description("Show detected project info without making changes")
  .option("-d, --dir <path>", "Project directory", process.cwd())
  .action(async (opts) => {
    const { default: chalk } = await import("chalk");
    const projectDir = path.resolve(opts.dir);

    try {
      const info = detectProject(projectDir);
      console.log(JSON.stringify(info, null, 2));
    } catch (err: unknown) {
      console.error(chalk.red((err as Error).message));
      process.exit(1);
    }
  });

program
  .command("uninstall")
  .description("Remove the generated instrumentation file and its backup")
  .option("-d, --dir <path>", "Project directory", process.cwd())
  .action(async (opts) => {
    const { default: chalk } = await import("chalk");
    const { default: ora } = await import("ora");
    const projectDir = path.resolve(opts.dir);

    console.log("");
    console.log(chalk.bold("🔭 @rocketgraph/otel-node — uninstall"));
    console.log("");

    let projectInfo;
    try {
      projectInfo = detectProject(projectDir);
    } catch {
      // If detection fails, try common paths
      projectInfo = { instrumentationPath: "instrumentation.ts" };
    }

    const candidates = [
      projectInfo.instrumentationPath,
      projectInfo.instrumentationPath + ".bak",
      // Also check .js variant
      projectInfo.instrumentationPath.replace(/\.ts$/, ".js"),
      projectInfo.instrumentationPath.replace(/\.ts$/, ".js") + ".bak",
    ];

    let removed = 0;
    for (const file of candidates) {
      const fullPath = path.join(projectDir, file);
      if (fs.existsSync(fullPath)) {
        const spinner = ora(`Removing ${file}...`).start();
        fs.unlinkSync(fullPath);
        spinner.succeed(`Removed ${file}`);
        removed++;
      }
    }

    if (removed === 0) {
      console.log(chalk.yellow("  No instrumentation files found to remove."));
    } else {
      console.log("");
      console.log(chalk.bold("  ✅ Instrumentation removed."));
      console.log(chalk.dim("  OTel packages were left installed — remove them manually if needed."));
    }
    console.log("");
  });

function getServiceNameFromPkg(projectDir: string): string | null {
  try {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(projectDir, "package.json"), "utf-8")
    );
    return pkg.name || null;
  } catch {
    return null;
  }
}

function getInstallCommand(
  packageManager: string,
  packages: string[]
): string {
  const pkgList = packages.join(" ");

  switch (packageManager) {
    case "yarn":
      return `yarn add ${pkgList}`;
    case "pnpm":
      return `pnpm add ${pkgList}`;
    case "bun":
      return `bun add ${pkgList}`;
    default:
      return `npm install ${pkgList}`;
  }
}

program.parse();
