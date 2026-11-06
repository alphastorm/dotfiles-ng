import { afterAll, beforeAll, expect, test } from "bun:test";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, realpathSync } from "node:fs";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

const publicRoot = realpathSync(join(import.meta.dir, ".."));
const sandboxes: string[] = [];
const installedPaths = [
	join(homedir(), ".dotfiles"),
	join(homedir(), ".omp", "agent", "managed-skills"),
	join(homedir(), ".omp", "plugins"),
];
let installedBefore: string[];

// find does not follow symlinks: inspect installed links without traversing
// their targets. Hash the complete -ls output to keep failed assertions small.
function snapshot(path: string): string {
	if (!existsSync(path)) return "absent";
	const listing = execFileSync("find", [path, "-ls"], {
		encoding: "utf8",
		maxBuffer: 128 * 1024 * 1024,
	});
	return createHash("sha256").update(listing).digest("hex");
}

beforeAll(() => {
	installedBefore = installedPaths.map(snapshot);
}, 30_000);

afterAll(async () => {
	try {
		expect(installedPaths.map(snapshot)).toEqual(installedBefore);
	} finally {
		await Promise.all(sandboxes.map(path => rm(path, { recursive: true, force: true })));
	}
}, 30_000);

interface Fixture {
	base: string;
	home: string;
	privateRoot: string;
	env: NodeJS.ProcessEnv;
}

function git(fixture: Fixture, directory: string, ...args: string[]): string {
	return execFileSync("git", ["-C", directory, ...args], {
		env: fixture.env,
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	}).trim();
}

async function fixture(linked = false): Promise<Fixture> {
	const base = realpathSync(await mkdtemp(join(tmpdir(), "setup-check-")));
	sandboxes.push(base);
	const home = join(base, "home");
	const seed = join(base, "seed");
	const clone = join(base, "private");
	const config = join(base, "gitconfig");
	await mkdir(home);
	await mkdir(seed);
	await writeFile(config, "[user]\n\tname = setup-check test\n\temail = test@example.invalid\n");
	const result: Fixture = {
		base,
		home,
		privateRoot: clone,
		env: {
			...process.env,
			HOME: home,
			GIT_CONFIG_GLOBAL: config,
			GIT_CONFIG_NOSYSTEM: "1",
		},
	};
	git(result, seed, "init", "--quiet", "--initial-branch=main");
	await mkdir(join(seed, "omp-private"));
	await writeFile(join(seed, "omp-private", ".setup-check-private"), "private fixture\n");
	await mkdir(join(seed, "zsh-private"));
	await writeFile(join(seed, "zsh-private", ".setup-check-zsh"), "zsh fixture\n");
	git(result, seed, "add", "--all");
	git(result, seed, "commit", "--quiet", "-m", "seed");
	git(result, base, "clone", "--quiet", seed, clone);
	if (linked) {
		result.privateRoot = join(base, "candidate");
		git(result, clone, "worktree", "add", "--quiet", "-b", "candidate", result.privateRoot);
	}
	return result;
}

function check(fixture: Fixture, privateRoot = fixture.privateRoot, path = process.env.PATH) {
	const homeBefore = snapshot(fixture.home);
	// Also cover private Git metadata and the isolated global config, not just
	// stowed targets. Read-only Git commands must not refresh indexes or hooks.
	const sandboxBefore = snapshot(fixture.base);
	const result = spawnSync("/bin/bash", ["./setup.sh", "--check"], {
		cwd: publicRoot,
		env: { ...fixture.env, PATH: path, DOTFILES_PRIVATE_DIR: privateRoot },
		encoding: "utf8",
		stdio: ["ignore", "pipe", "pipe"],
	});
	expect(result.error).toBeUndefined();
	expect(snapshot(fixture.home)).toBe(homeBefore);
	expect(snapshot(fixture.base)).toBe(sandboxBefore);
	return { status: result.status, output: result.stdout + result.stderr };
}

function expectRoots(fixture: Fixture, output: string) {
	const publicBranch = git(fixture, publicRoot, "branch", "--show-current") || "detached";
	const privateBranch = git(fixture, fixture.privateRoot, "branch", "--show-current") || "detached";
	expect(output).toContain(
		`public: ${publicRoot} @ ${git(fixture, publicRoot, "rev-parse", "--short", "HEAD")} (${publicBranch})`,
	);
	expect(output).toContain(
		`private: ${fixture.privateRoot} @ ${git(fixture, fixture.privateRoot, "rev-parse", "--short", "HEAD")} (${privateBranch})`,
	);
}

for (const linked of [false, true]) {
	const kind = linked ? "linked worktree" : "normal clone";

	test(`reports and simulates a private ${kind}, detecting its planted conflict`, async () => {
		const f = await fixture(linked);
		const clean = check(f);
		expectRoots(f, clean.output);
		expect(clean.output).toContain(
			`check passed: ${process.platform === "darwin" ? 8 : 7} package restows simulated without conflicts`,
		);
		expect(clean.status).toBe(0);

		await writeFile(join(f.home, ".setup-check-private"), "keep this real file\n");
		const conflict = check(f);
		expectRoots(f, conflict.output);
		expect(conflict.status).toBe(1);
		expect(conflict.output).toContain("conflict: omp-private");
		expect(conflict.output).toContain(".setup-check-private");
		expect(conflict.output).not.toContain("check passed:");
	}, 30_000);

	test(`accepts runtime worktrees sharing the private ${kind}'s common Git dir`, async () => {
		const f = await fixture(linked);
		await mkdir(join(f.home, ".omp", "agent"), { recursive: true });
		for (const [branch, target] of [
			["omp-managed-skills", join(f.home, ".omp", "agent", "managed-skills")],
			["omp-plugins", join(f.home, ".omp", "plugins")],
		]) {
			git(f, f.privateRoot, "worktree", "add", "--quiet", "-b", branch, target);
		}
		const result = check(f);
		expectRoots(f, result.output);
		expect(result.output).not.toContain("must be a git worktree");
		expect(result.status).toBe(0);
	}, 30_000);

	test(`inspects untracked key files inside the private ${kind}`, async () => {
		const f = await fixture(linked);
		await mkdir(join(f.privateRoot, "omp-private", ".ssh"));
		await writeFile(join(f.privateRoot, "omp-private", ".ssh", "test-key"), "fixture only\n");
		const result = check(f);
		expectRoots(f, result.output);
		expect(result.status).toBe(1);
		expect(result.output).toContain(`error: untracked files in a key directory of ${f.privateRoot}`);
	}, 30_000);
}

test("diagnoses nested and non-repository private candidates instead of silently omitting them", async () => {
	const f = await fixture(true);
	const notRepo = join(f.base, "not-a-repository");
	await mkdir(notRepo);
	for (const candidate of [join(f.privateRoot, "omp-private"), notRepo]) {
		const result = check(f, candidate);
		expect(result.status).toBe(1);
		expect(result.output).toContain(`private: skipped (not a Git repository root: ${candidate})`);
		expect(result.output).toContain(`error: private dotfiles path is not a Git repository root: ${candidate}`);
		expect(result.output).not.toContain("check passed:");
	}
}, 30_000);

test("reports an absent private checkout as skipped and counts only public simulations", async () => {
	const f = await fixture();
	const absent = join(f.base, "absent");
	const result = check(f, absent);
	expect(result.status).toBe(0);
	expect(result.output).toContain(`private: skipped (path does not exist: ${absent})`);
	expect(result.output).toContain("check passed: 6 package restows simulated without conflicts");
}, 30_000);

test("reports a detached private worktree", async () => {
	const f = await fixture(true);
	git(f, f.privateRoot, "checkout", "--quiet", "--detach");
	const result = check(f);
	expectRoots(f, result.output);
	expect(result.output).toContain("(detached)");
	expect(result.status).toBe(0);
}, 30_000);

test("rejects a runtime checkout from another repository despite a matching branch", async () => {
	const f = await fixture(true);
	const other = await fixture();
	const target = join(f.home, ".omp", "agent", "managed-skills");
	await mkdir(join(f.home, ".omp", "agent"), { recursive: true });
	git(other, other.privateRoot, "worktree", "add", "--quiet", "-b", "omp-managed-skills", target);
	const result = check(f);
	expectRoots(f, result.output);
	expect(result.status).toBe(1);
	expect(result.output).toContain(`conflict: ${target} must be a git worktree of ${f.privateRoot}`);
}, 30_000);

test("fails explicitly when PATH has no Stow, without discovering it through Homebrew", async () => {
	const f = await fixture(true);
	const bin = join(f.base, "bin");
	await mkdir(bin);
	// The script needs these real programs before simulation; neither stow nor
	// brew is present. Linux's platform detection also checks for apt-get.
	for (const command of ["dirname", "uname", "git", ...(process.platform === "linux" ? ["apt-get"] : [])]) {
		const executable = execFileSync("/bin/bash", ["-c", 'command -v "$1"', "bash", command], {
			encoding: "utf8",
		}).trim();
		await symlink(executable, join(bin, command));
	}
	const result = check(f, f.privateRoot, bin);
	expectRoots(f, result.output);
	expect(result.status).toBe(1);
	expect(result.output).toContain("error: stow is not installed; package restow was not simulated");
	expect(result.output).not.toContain("check passed:");
}, 30_000);

test("key inspection does not invoke a configured filesystem-monitor hook", async () => {
	const f = await fixture(true);
	const hook = join(f.base, "fsmonitor");
	await writeFile(hook, '#!/bin/sh\nprintf "invoked\\n" > "$0.invoked"\nprintf "token\\000/\\000"\n', {
		mode: 0o755,
	});
	git(f, f.privateRoot, "config", "--local", "core.fsmonitor", hook);
	const result = check(f);
	expect(result.status).toBe(0);
	expect(existsSync(`${hook}.invoked`)).toBe(false);
}, 30_000);

test("update can source the shared root predicate without running setup", async () => {
	const f = await fixture(true);
	const before = snapshot(f.base);
	for (const [candidate, status] of [
		[f.privateRoot, 0],
		[join(f.base, "private"), 0],
		[join(f.privateRoot, "omp-private"), 1],
		[f.home, 1],
	] as const) {
		const result = spawnSync("/bin/bash", ["-c", 'source ./setup.sh; is_git_root "$1"', "bash", candidate], {
			cwd: publicRoot,
			env: f.env,
			encoding: "utf8",
		});
		expect(result.status).toBe(status);
		expect(result.stdout + result.stderr).toBe("");
	}
	expect(snapshot(f.base)).toBe(before);
}, 30_000);
