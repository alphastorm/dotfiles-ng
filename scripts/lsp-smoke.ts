#!/usr/bin/env bun
/**
 * Run OMP's own `lsp` tool from a working directory, without a model, and report what a session
 * started there gets: the servers configured for it, the executable and version that served each
 * case, and each case's result.
 *
 *   bun scripts/lsp-smoke.ts <cwd> <case>...
 *     definition:<file>:<line>:<symbol>   must resolve to at least one location
 *     hover:<file>:<line>:<symbol>        must return hover text
 *     diagnostics:<file>                  must be answered; append :<text> to require <text> in it
 *
 * Files are relative to <cwd>. The tool comes from this checkout's @oh-my-pi/pi-coding-agent, so the
 * script refuses to run when that pin differs from `omp --version`. Exits 1 when a case fails.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { Settings } from "@oh-my-pi/pi-coding-agent/config/settings";
import { shutdownAll } from "@oh-my-pi/pi-coding-agent/lsp/client";
import { getConfig } from "@oh-my-pi/pi-coding-agent/lsp/config";
import { LspTool } from "@oh-my-pi/pi-coding-agent/lsp/tool";
import type { ToolSession } from "@oh-my-pi/pi-coding-agent/tools";
import { ensureTheme } from "@oh-my-pi/pi-tui/theme";

const [cwdArg, ...cases] = process.argv.slice(2);
if (!cwdArg || cases.length === 0) {
	console.error("usage: bun scripts/lsp-smoke.ts <cwd> definition:<file>:<line>:<symbol> | hover:… | diagnostics:<file>[:<text>] …");
	process.exit(2);
}
const cwd = path.resolve(cwdArg);

const packageDir = path.dirname(path.dirname(Bun.resolveSync("@oh-my-pi/pi-coding-agent", import.meta.dir)));
const pinned: string = (await Bun.file(path.join(packageDir, "package.json")).json()).version;
const installed = Bun.spawnSync(["omp", "--version"]).stdout.toString().trim();
if (installed !== `omp/${pinned}`) {
	console.error(`error: ${installed || "no omp on PATH"} is not the @oh-my-pi/pi-coding-agent ${pinned} this script drives; bump package.json first`);
	process.exit(2);
}

// npm and bun installs name their version in the nearest package.json under node_modules, Homebrew
// in its Cellar path; anything else is asked directly.
function executableVersion(command: string): string {
	const real = fs.realpathSync(command);
	const nodeModules = real.lastIndexOf(`${path.sep}node_modules${path.sep}`);
	if (nodeModules !== -1) {
		for (let dir = path.dirname(real); dir.length > nodeModules; dir = path.dirname(dir)) {
			const manifest = path.join(dir, "package.json");
			if (fs.existsSync(manifest)) {
				const { name, version } = JSON.parse(fs.readFileSync(manifest, "utf8"));
				return `${name}@${version}`;
			}
		}
	}
	const cellar = /\/Cellar\/([^/]+)\/([^/]+)\//.exec(real);
	if (cellar) return `${cellar[1]} ${cellar[2]}`;
	const run = Bun.spawnSync([command, "--version"], { stderr: "pipe", timeout: 5_000 });
	return (run.stdout.toString() || run.stderr.toString()).trim().split("\n")[0] || "unknown";
}
// Diagnostics results carry the TUI's status glyphs.
await ensureTheme();
const settings = await Settings.loadReadOnly({ cwd });
const config = getConfig(cwd);
const tool = new LspTool({ cwd, settings, lspReadOnly: true } as ToolSession);
console.log(`host: ${os.hostname()} (${process.platform} ${process.arch})`);
console.log(`omp: ${installed}`);
console.log(`cwd: ${cwd}`);
console.log(`servers: ${Object.keys(config.servers).join(", ") || "none"}`);

// A cold server may still be loading its project: give every case a minute rather than the
// 10-second default an agent retries past.
const TIMEOUT_SECONDS = 60;
// OMP answers in text when no server covers a file, a server never reported, or hover found nothing.
const UNANSWERED = ["No language server found", "no diagnostics report yet", "servers failed", "No hover information"];

let failed = 0;
for (const spec of cases) {
	const [action, file, ...rest] = spec.split(":");
	const params =
		action === "diagnostics"
			? { action, file, timeout: TIMEOUT_SECONDS }
			: { action, file, line: Number(rest[0]), symbol: rest.slice(1).join(":"), timeout: TIMEOUT_SECONDS };
	const expected = action === "diagnostics" ? rest.join(":") : "";
	let ok = false;
	let report: string;
	try {
		const result = await tool.execute("lsp-smoke", params as Parameters<LspTool["execute"]>[1]);
		const text = result.content.map(part => (part.type === "text" ? part.text : "")).join("\n");
		const details = result.details as { success?: boolean; serverName?: string } | undefined;
		const servers = (details?.serverName ?? "").split(", ").filter(Boolean);
		ok =
			details?.success === true &&
			result.useless !== true &&
			servers.length > 0 &&
			!UNANSWERED.some(marker => text.includes(marker)) &&
			text.includes(expected);
		const served = servers.map(name => {
			const server = config.servers[name];
			if (!server?.resolvedCommand) return `  server: ${name}`;
			const args = server.args?.length ? ` ${server.args.join(" ")}` : "";
			return `  server: ${name} -> ${server.resolvedCommand}${args} (${executableVersion(server.resolvedCommand)})`;
		});
		report = [...served, ...text.split("\n").map(line => `  ${line}`)].join("\n");
	} catch (error) {
		report = `  ${error instanceof Error ? error.message : String(error)}`;
	}
	if (!ok) failed++;
	console.log(`${ok ? "PASS" : "FAIL"} ${spec}\n${report}`);
}
await shutdownAll();
process.exit(failed === 0 ? 0 : 1);
