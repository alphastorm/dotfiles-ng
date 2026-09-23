import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { lstat, readdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join, relative } from "node:path";

// Runtime state, not stowed configuration. `sessions` and `blobs` in particular
// are large enough that walking them on every session start would be felt.
const SKIP: Record<string, true> = {
	blobs: true,
	cache: true,
	checkpoints: true,
	logs: true,
	memories: true,
	"performance-v1": true,
	"performance-v2": true,
	run: true,
	sessions: true,
	"terminal-sessions": true,
};
const MAX_DEPTH = 3;

// Directories OMP's own writers own. setup.sh's ensure_runtime_worktree keeps
// each one a git worktree of the private repository, so a change there is backed
// up only once it is committed and pushed from inside the directory -- and the
// private checkout's own `git status` never shows it.
const RUNTIME_WORKTREES = [".omp/agent/managed-skills", ".omp/plugins"];

async function collectDangling(dir: string, depth: number, out: string[]): Promise<void> {
	let entries;
	try {
		entries = await readdir(dir, { withFileTypes: true });
	} catch {
		return; // unreadable is not the failure this is looking for
	}
	for (const entry of entries) {
		const path = join(dir, entry.name);
		// A symlinked directory is checked but never entered. That is what keeps the
		// walk off the far side of skills/critical-review/lrhe, whose .venv alone is
		// six thousand files.
		if (entry.isSymbolicLink()) {
			if (!existsSync(path)) out.push(path);
		} else if (entry.isDirectory() && depth < MAX_DEPTH && !SKIP[entry.name]) {
			await collectDangling(path, depth + 1, out);
		}
	}
}

interface RunResult {
	code: number;
	stdout: string;
	stderr: string;
}

/** Run a command; undefined when it could not run at all (not installed, timed out). */
function run(file: string, args: string[]): Promise<RunResult | undefined> {
	const { promise, resolve } = Promise.withResolvers<RunResult | undefined>();
	execFile(file, args, { timeout: 10_000 }, (error, stdout, stderr) => {
		const code = error ? error.code : 0;
		resolve(typeof code === "number" ? { code, stdout, stderr } : undefined);
	});
	return promise;
}

/**
 * Simulate the restow setup.sh performs for each package that targets ~/.omp.
 * Stow aborts a whole package on its first conflict, and a writer that saves by
 * renaming a temp file over one of its links -- bun does this to bun.lock --
 * leaves exactly such a conflict behind, unseen until update.sh stops halfway.
 * The exit status is the verdict; the output only names the paths.
 */
async function collectConflicts(home: string): Promise<string[]> {
	const packages: Array<[dir: string, name: string]> = [
		[join(home, ".dotfiles"), "omp"],
		[process.env.DOTFILES_PRIVATE_DIR ?? join(home, ".dotfiles-private"), "omp-private"],
	];
	const found = await Promise.all(
		packages.map(async ([dir, name]) => {
			if (!existsSync(join(dir, name))) return [];
			const result = await run("stow", ["--simulate", "--restow", "--dir", dir, "--target", home, name]);
			if (!result || result.code === 0) return [];
			const lines = result.stderr.split("\n");
			const conflicts = lines.filter(line => line.startsWith("  * ")).map(line => line.slice(4));
			const detail = conflicts.length > 0 ? conflicts : lines.filter(Boolean).slice(-1);
			return detail.map(line => `${name}: ${line}`);
		}),
	);
	return found.flat();
}

interface RuntimeState {
	broken: string[];
	unsaved: string[];
}

async function inspectRuntimeWorktrees(home: string): Promise<RuntimeState> {
	const state: RuntimeState = { broken: [], unsaved: [] };
	await Promise.all(
		RUNTIME_WORKTREES.map(async rel => {
			const dir = join(home, rel);
			const stat = await lstat(dir).catch(() => undefined);
			if (!stat) return; // setup.sh has not run, or there is no private repository
			if (stat.isSymbolicLink() || !existsSync(join(dir, ".git"))) {
				state.broken.push(`~/${rel}`);
				return;
			}
			const status = await run("git", ["-C", dir, "status", "--porcelain=v2", "--branch"]);
			if (!status) return;
			if (status.code !== 0) {
				state.broken.push(`~/${rel}`);
				return;
			}
			const lines = status.stdout.split("\n");
			const changes = lines.filter(line => line && !line.startsWith("#")).length;
			const ahead = Number(/^# branch\.ab \+(\d+) /m.exec(status.stdout)?.[1] ?? 0);
			const parts: string[] = [];
			if (changes > 0) parts.push(`${changes} uncommitted`);
			if (!lines.some(line => line.startsWith("# branch.upstream "))) parts.push("no upstream");
			else if (ahead > 0) parts.push(`${ahead} unpushed`);
			if (parts.length > 0) state.unsaved.push(`~/${rel}: ${parts.join(", ")}`);
		}),
	);
	return state;
}

/**
 * Warn when stowed configuration has drifted from what setup.sh maintains.
 *
 * Three failures stay silent until something unrelated trips over them:
 * - A link left dangling after a file moves between the public and private
 *   packages. OMP just does not load what it pointed at: three agent
 *   definitions -- among them a council reviewer its own qualification file
 *   marked `councilEnabled: true` -- sat dangling for weeks.
 * - A stowed link a runtime replaced with a real file. The next restow aborts
 *   the whole package: every `omp plugin install` did this to bun.lock until
 *   ~/.omp/plugins became a worktree.
 * - A runtime worktree that stopped being one, or holds changes nobody has
 *   committed and pushed yet.
 *
 * Session start is the right moment to check, because it is exactly when the
 * configuration is loaded and someone is there to read the result.
 */
export default function stowHealth(pi: ExtensionAPI): void {
	pi.on("session_start", async (_event, ctx) => {
		// Nobody reads a notification without a UI; print mode and subagents skip
		// the walk and the subprocesses.
		if (!ctx.hasUI) return;
		const home = homedir();
		const root = join(home, ".omp");
		const broken: string[] = [];
		const [, conflicts, runtime] = await Promise.all([
			collectDangling(root, 0, broken),
			collectConflicts(home),
			inspectRuntimeWorktrees(home),
		]);
		const repair = "Repair: cd ~/.dotfiles && ./setup.sh";

		if (broken.length > 0) {
			const list = broken.map(path => `  ~/.omp/${relative(root, path)}`).join("\n");
			const one = broken.length === 1;
			ctx.ui.notify(
				`${broken.length} dangling config symlink${one ? "" : "s"} — ` +
					`whatever ${one ? "it points" : "they point"} at is not loading:\n${list}\n${repair}`,
				"warning",
			);
		}
		if (conflicts.length > 0) {
			ctx.ui.notify(
				"The next ./setup.sh or update.sh will abort at stow:\n" +
					`${conflicts.map(line => `  ${line}`).join("\n")}\n` +
					"Usually a writer replaced a stowed link with a real file: keep the right copy in the package, " +
					"delete the other, then rerun setup.sh.",
				"warning",
			);
		}
		if (runtime.broken.length > 0) {
			ctx.ui.notify(
				`${runtime.broken.join(" and ")} must be a git worktree of the private repository: ` +
					`OMP writes there, and a stow link or stray directory breaks that.\n${repair}`,
				"warning",
			);
		}
		if (runtime.unsaved.length > 0) {
			ctx.ui.notify(
				"OMP runtime state not backed up yet — commit and push from inside each directory:\n" +
					runtime.unsaved.map(line => `  ${line}`).join("\n"),
				"info",
			);
		}
	});
}
