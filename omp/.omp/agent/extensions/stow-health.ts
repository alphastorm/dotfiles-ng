import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { lstat, readdir, readlink } from "node:fs/promises";
import { homedir } from "node:os";
import { join, relative, resolve, sep } from "node:path";

// Runtime state, not stowed configuration. `sessions` and `blobs` in particular
// are large enough that walking them on every session start would be felt.
const SKIP: Record<string, true> = {
	blobs: true,
	cache: true,
	checkpoints: true,
	logs: true,
	memories: true,
	run: true,
	sessions: true,
	"terminal-sessions": true,
};
const MAX_DEPTH = 3;

export interface RuntimeWorktree {
	rel: string;
	/** Conventional Commits type and scope of the commits session start makes. */
	scope: string;
	/** What the subject counts when it has no room to list names. */
	noun: string;
}

// Directories OMP's own writers own. setup.sh's ensure_runtime_worktree keeps
// each one a git worktree of the private repository, so a change there is backed
// up only once it is committed and pushed from inside the directory -- and the
// private checkout's own `git status` never shows it. Every host pushes to the
// same branch. Session start commits, takes in the other hosts' commits, pushes.
const RUNTIME_WORKTREES: readonly RuntimeWorktree[] = [
	{ rel: ".omp/agent/managed-skills", scope: "docs(skills)", noun: "skills" },
	{ rel: ".omp/plugins", scope: "chore(plugins)", noun: "files" },
];

/** Each package setup.sh stows into ~/.omp, as [stow directory, package name]. */
function stowPackages(home: string): Array<[dir: string, name: string]> {
	return [
		[join(home, ".dotfiles"), "omp"],
		[process.env.DOTFILES_PRIVATE_DIR ?? join(home, ".dotfiles-private"), "omp-private"],
	];
}

/**
 * Collect the links under `dir` that stow planted and whose target is gone. A
 * link counts only when it points into one of `packageRoots`: runtime state
 * under ~/.omp keeps links that are dangling by design -- Chrome's SingletonLock
 * in a browser profile names `<host>-<pid>`, a lane worktree's bazel-out points
 * into a pruned output base -- and setup.sh could repair neither.
 */
export async function collectDangling(
	dir: string,
	packageRoots: readonly string[],
	depth = 0,
	out: string[] = [],
): Promise<string[]> {
	let entries;
	try {
		entries = await readdir(dir, { withFileTypes: true });
	} catch {
		return out; // unreadable is not the failure this is looking for
	}
	for (const entry of entries) {
		const path = join(dir, entry.name);
		// A symlinked directory is checked but never entered. That is what keeps the
		// walk off the far side of skills/critical-review/lrhe, whose .venv alone is
		// six thousand files.
		if (entry.isSymbolicLink()) {
			if (existsSync(path)) continue;
			const link = await readlink(path).catch(() => undefined);
			if (link === undefined) continue; // removed since readdir listed it
			const target = resolve(dir, link);
			if (packageRoots.some(root => target.startsWith(root + sep))) out.push(path);
		} else if (entry.isDirectory() && depth < MAX_DEPTH && !SKIP[entry.name]) {
			await collectDangling(path, packageRoots, depth + 1, out);
		}
	}
	return out;
}

interface RunResult {
	code: number;
	stdout: string;
	stderr: string;
}

/**
 * Run a command; undefined when it could not run at all (not installed, timed
 * out). The child leads a session of its own, so it has no controlling
 * terminal: ssh and gpg fail instead of prompting over the TUI.
 */
function run(file: string, args: string[], timeoutMs = 10_000): Promise<RunResult | undefined> {
	const { promise, resolve: settle } = Promise.withResolvers<RunResult | undefined>();
	const child = spawn(file, args, { detached: true, stdio: ["ignore", "pipe", "pipe"] });
	let stdout = "";
	let stderr = "";
	child.stdout.setEncoding("utf8").on("data", (chunk: string) => (stdout += chunk));
	child.stderr.setEncoding("utf8").on("data", (chunk: string) => (stderr += chunk));
	// The whole group, so ssh goes too; SIGTERM, so git removes its lock files.
	const timer = setTimeout(() => {
		try {
			if (child.pid) process.kill(-child.pid, "SIGTERM");
		} catch {
			// already gone
		}
	}, timeoutMs);
	child.on("error", () => {
		clearTimeout(timer);
		settle(undefined);
	});
	child.on("close", code => {
		clearTimeout(timer);
		settle(code === null ? undefined : { code, stdout, stderr });
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
	const packages = stowPackages(home);
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

/** A runtime worktree to bring level with the branch every host saves to. */
export interface Pending {
	tree: RuntimeWorktree;
	dir: string;
	/** Entries `git status` lists. */
	changes: number;
	/** Commits the upstream lacks, as of the last fetch. */
	ahead: number;
	/** Why only a person can save it, when only a person can. */
	blocker?: string;
}

export async function inspectRuntimeWorktrees(home: string): Promise<{ broken: string[]; trees: Pending[] }> {
	const broken: string[] = [];
	const trees: Pending[] = [];
	await Promise.all(
		RUNTIME_WORKTREES.map(async tree => {
			const dir = join(home, tree.rel);
			const stat = await lstat(dir).catch(() => undefined);
			if (!stat) return; // setup.sh has not run, or there is no private repository
			if (stat.isSymbolicLink() || !existsSync(join(dir, ".git"))) {
				broken.push(`~/${tree.rel}`);
				return;
			}
			const status = await run("git", ["-C", dir, "status", "--porcelain=v2", "--branch"]);
			if (!status) return;
			if (status.code !== 0) {
				broken.push(`~/${tree.rel}`);
				return;
			}
			const lines = status.stdout.split("\n");
			const entries = lines.filter(line => line && !line.startsWith("#"));
			const ahead = Number(/^# branch\.ab \+(\d+) /m.exec(status.stdout)?.[1] ?? 0);
			const upstream = lines.some(line => line.startsWith("# branch.upstream "));
			let blocker: string | undefined;
			// A rebase in progress detaches HEAD; committing into it or into a conflict
			// would take over someone's half-finished work.
			if (lines.includes("# branch.head (detached)")) blocker = "detached HEAD";
			else if (entries.some(line => line.startsWith("u "))) blocker = "unresolved conflicts";
			else if (!upstream) blocker = "no upstream";
			trees.push({ tree, dir, changes: entries.length, ahead, blocker });
		}),
	);
	return { broken, trees };
}

// Another OMP session on this host is saving the same worktree right now.
const CONTENDED = /index\.lock|cannot lock ref|nothing to commit|already a rebase-(?:merge|apply) directory/;

/** The line of git's output that says what went wrong. */
function failure(result: RunResult | undefined): string {
	if (!result) return "did not finish";
	const lines = `${result.stderr}\n${result.stdout}`
		.split("\n")
		.map(line => line.trim())
		.filter(Boolean);
	return lines.find(line => /^(! |fatal:|error:)|denied/.test(line)) ?? lines.at(-1) ?? `exit ${result.code}`;
}

function commitMessage(
	tree: RuntimeWorktree,
	changes: ReadonlyArray<readonly [status: string, path: string]>,
): { subject: string; body: string } {
	const names = [...new Set(changes.map(([, path]) => path.split("/")[0]))].sort();
	const statuses = new Set(changes.map(([status]) => status));
	const only = statuses.size === 1 ? [...statuses][0] : undefined;
	const verb = only === "A" ? "add" : only === "D" ? "remove" : "update";
	const listed = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
	let subject = `${tree.scope}: ${verb} ${listed}`;
	if (subject.length > 72) subject = `${tree.scope}: ${verb} ${names.length} ${tree.noun}`;
	const listing = changes.map(([status, path]) => `${status} ${path}`);
	return { subject, body: ["Saved by the stow-health extension at session start.", "", ...listing].join("\n") };
}

async function countCommits(dir: string, range: string): Promise<number> {
	const result = await run("git", ["-C", dir, "rev-list", "--count", range]);
	return result?.code === 0 ? Number(result.stdout.trim()) : 0;
}

/**
 * Commit everything OMP's writers left in a runtime worktree, take in what other
 * hosts pushed to its branch, and push the result. Each of `saved`, `received`
 * and `unsaved` is one notification line; none is set when there was nothing to
 * do, nothing was at risk, or another session got there first.
 */
export async function syncRuntimeWorktree(
	item: Pending,
): Promise<{ saved?: string; received?: string; unsaved?: string }> {
	const where = `~/${item.tree.rel}`;
	let uncommitted = item.changes;
	let ahead = item.ahead;
	const unsaved = (why: string) => {
		const parts = [uncommitted > 0 && `${uncommitted} uncommitted`, ahead > 0 && `${ahead} unpushed`];
		return { unsaved: `${where}: ${parts.filter(Boolean).join(", ") || "not pushed"} (${why})` };
	};
	if (item.blocker) return unsaved(item.blocker);
	let committed = 0;
	if (uncommitted > 0) {
		const add = await run("git", ["-C", item.dir, "add", "--all"]);
		if (add?.code !== 0) return CONTENDED.test(failure(add)) ? {} : unsaved(`add failed: ${failure(add)}`);
		const staged = await run("git", ["-C", item.dir, "diff", "--cached", "--name-status", "--no-renames", "-z"]);
		if (staged?.code !== 0) return unsaved(`diff failed: ${failure(staged)}`);
		const fields = staged.stdout.split("\0");
		const changes: Array<[status: string, path: string]> = [];
		for (let i = 0; i + 1 < fields.length; i += 2) changes.push([fields[i], fields[i + 1]]);
		if (changes.length > 0) {
			const { subject, body } = commitMessage(item.tree, changes);
			// Signing can wait on pinentry.
			const result = await run("git", ["-C", item.dir, "commit", "--quiet", "-m", subject, "-m", body], 60_000);
			if (result?.code !== 0) {
				return CONTENDED.test(failure(result)) ? {} : unsaved(`commit failed: ${failure(result)}`);
			}
			committed = changes.length;
			ahead += 1;
		}
		uncommitted = 0;
	}
	// Every host saves to this branch. Replaying this host's commits onto what the
	// others pushed keeps the push a fast-forward, and this host loads what they
	// learned at its next session. Rebasing re-signs those commits.
	const known = await run("git", ["-C", item.dir, "rev-parse", "--verify", "--quiet", "@{upstream}"]);
	const pull = await run("git", ["-C", item.dir, "pull", "--rebase", "--no-autostash", "--quiet"], 120_000);
	if (pull?.code !== 0) {
		const output = pull ? `${pull.stderr}\n${pull.stdout}` : "";
		if (CONTENDED.test(output)) return {};
		// This pull's own stopped rebase: undo it so the commit stays as it was.
		await run("git", ["-C", item.dir, "rebase", "--abort"]);
		// Nothing here that the remote lacks, as when the host is offline.
		if (ahead === 0) return {};
		return unsaved(/^CONFLICT/m.test(output) ? "conflicts with another host's change" : `pull failed: ${failure(pull)}`);
	}
	const received = known?.code === 0 ? await countCommits(item.dir, `${known.stdout.trim()}..@{upstream}`) : 0;
	const receivedLine = received > 0 ? `${where}: ${received} commit${received === 1 ? "" : "s"}` : undefined;
	ahead = await countCommits(item.dir, "@{upstream}..HEAD");
	if (ahead === 0) return receivedLine ? { received: receivedLine } : {};
	// `upstream`, whatever push.default says: this branch, to the branch it tracks.
	const push = await run("git", ["-C", item.dir, "-c", "push.default=upstream", "push", "--quiet"], 120_000);
	if (push?.code !== 0) {
		if (CONTENDED.test(failure(push))) return receivedLine ? { received: receivedLine } : {};
		return { received: receivedLine, ...unsaved(`push failed: ${failure(push)}`) };
	}
	const pushed = `pushed ${ahead} commit${ahead === 1 ? "" : "s"}`;
	const saved = committed === 0 ? `${where}: ${pushed}` : `${where}: committed ${committed} change${committed === 1 ? "" : "s"}, ${pushed}`;
	return { saved, received: receivedLine };
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
 * - A runtime worktree that stopped being one. Changes OMP's writers leave in
 *   one are committed, replayed onto what other hosts pushed, and pushed here,
 *   so nobody has to remember to; only what that cannot save -- a rebase in
 *   progress, a conflict with another host, a rejected push -- is reported.
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
		const [broken, conflicts, runtime] = await Promise.all([
			collectDangling(
				root,
				stowPackages(home).map(([dir, name]) => resolve(dir, name)),
			),
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
		if (runtime.trees.length > 0) {
			// Signing can wait on pinentry and pulling or pushing on the network, so
			// the sync runs past session start and reports when it is done.
			void Promise.all(runtime.trees.map(syncRuntimeWorktree))
				.then(outcomes => {
					const lines = (key: "saved" | "received" | "unsaved") =>
						outcomes.flatMap(outcome => (outcome[key] ? [`  ${outcome[key]}`] : []));
					const received = lines("received");
					const saved = lines("saved");
					const unsaved = lines("unsaved");
					if (received.length > 0) {
						ctx.ui.notify(
							`Took in OMP runtime state another host saved; new sessions load it:\n${received.join("\n")}`,
							"info",
						);
					}
					if (saved.length > 0) ctx.ui.notify(`Backed up OMP runtime state:\n${saved.join("\n")}`, "info");
					if (unsaved.length > 0) {
						ctx.ui.notify(
							`OMP runtime state not backed up — commit and push from inside each directory:\n${unsaved.join("\n")}`,
							"warning",
						);
					}
				})
				.catch((error: unknown) => {
					// Detached: an escaped rejection would take the session down with it.
					try {
						ctx.ui.notify(`Backing up OMP runtime state failed: ${String(error)}`, "warning");
					} catch {
						// the session has already ended
					}
				});
		}
	});
}
