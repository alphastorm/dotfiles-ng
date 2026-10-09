// Tests for omp/.omp/agent/extensions/stow-health.ts. They live outside the omp
// package because OMP loads every `.ts` directly under an agent `extensions/`
// directory as an extension, and setup.sh stows only its named packages.
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	collectDangling,
	coalesceRuntimeConvergence,
	inspectRuntimeWorktrees,
	syncRuntimeWorktree,
} from "../omp/.omp/agent/extensions/stow-health.ts";

const homes: string[] = [];

async function newHome(): Promise<string> {
	const home = await mkdtemp(join(tmpdir(), "stow-health-"));
	homes.push(home);
	return home;
}

afterAll(async () => {
	await Promise.all(homes.map(home => rm(home, { recursive: true, force: true })));
});

test("reports a stow link whose package file is gone, not a runtime link that never resolved", async () => {
	const home = await newHome();
	const pkg = join(home, ".dotfiles", "omp");
	const agents = join(home, ".omp", "agent", "agents");
	const profile = join(home, ".omp", "browser-profiles", "google-chrome-for-testing-0");
	await mkdir(join(pkg, ".omp", "agent", "agents"), { recursive: true });
	await mkdir(agents, { recursive: true });
	await mkdir(profile, { recursive: true });
	await writeFile(join(pkg, ".omp", "agent", "agents", "kept.md"), "");
	await symlink("../../../.dotfiles/omp/.omp/agent/agents/kept.md", join(agents, "kept.md"));
	await symlink("../../../.dotfiles/omp/.omp/agent/agents/moved.md", join(agents, "moved.md"));
	// Chrome writes these into a live profile; their targets are data, never paths.
	await symlink("example-host-77718", join(profile, "SingletonLock"));
	await symlink("4815162342", join(profile, "SingletonCookie"));

	expect(await collectDangling(join(home, ".omp"), [pkg])).toEqual([join(agents, "moved.md")]);
});

test("coalesces overlapping start and periodic triggers, then admits the next tick", async () => {
	const pending = Promise.withResolvers<void>();
	let calls = 0;
	const first = coalesceRuntimeConvergence(async () => {
		calls += 1;
		await pending.promise;
});
	await coalesceRuntimeConvergence(async () => {
		calls += 1;
});
	expect(calls).toBe(1);
	pending.resolve();
	await first;
	await coalesceRuntimeConvergence(async () => {
		calls += 1;
});
	expect(calls).toBe(2);
});

test("releases the convergence guard after a failed trigger", async () => {
	await expect(coalesceRuntimeConvergence(async () => {
		throw new Error("failed convergence");
	})).rejects.toThrow("failed convergence");
	let called = false;
	await coalesceRuntimeConvergence(async () => {
		called = true;
});
	expect(called).toBe(true);
});

describe("runtime worktree sync", () => {
	const saved = { ...process.env };

	beforeAll(async () => {
		// Match the global rerere defaults, but keep signing out of these fixtures.
		const config = join(await newHome(), "gitconfig");
		await writeFile(
			config,
			"[user]\n\tname = stow-health test\n\temail = test@example.invalid\n" +
			"[rerere]\n\tenabled = true\n\tautoupdate = true\n",
		);
		process.env.GIT_CONFIG_GLOBAL = config;
		process.env.GIT_CONFIG_NOSYSTEM = "1";
	});

	afterAll(() => {
		process.env = saved;
	});

	const git = (dir: string, ...args: string[]) =>
		execFileSync("git", ["-C", dir, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();

	/** A home whose managed-skills worktree tracks a branch on a local bare remote. */
	async function skillsHome(): Promise<{ home: string; remote: string; skills: string }> {
		const home = await newHome();
		const remote = join(home, "remote.git");
		const skills = join(home, ".omp", "agent", "managed-skills");
		await mkdir(skills, { recursive: true });
		git(home, "init", "--quiet", "--bare", remote);
		git(skills, "init", "--quiet", "--initial-branch=omp-managed-skills");
		git(skills, "remote", "add", "origin", remote);
		await mkdir(join(skills, "seed"));
		await writeFile(join(skills, "seed", "SKILL.md"), "seed\n");
		git(skills, "add", "--all");
		git(skills, "commit", "--quiet", "-m", "seed");
		git(skills, "push", "--quiet", "--set-upstream", "origin", "omp-managed-skills");
		return { home, remote, skills };
	}

	/** Another host: its own clone of the same branch, tracking the same remote. */
	async function otherHost(remote: string): Promise<{ home: string; skills: string }> {
		const home = await newHome();
		const skills = join(home, ".omp", "agent", "managed-skills");
		await mkdir(join(home, ".omp", "agent"), { recursive: true });
		git(home, "clone", "--quiet", "--branch", "omp-managed-skills", remote, skills);
		return { home, skills };
	}

	async function sync(home: string) {
		const { trees } = await inspectRuntimeWorktrees(home);
		expect(trees).toHaveLength(1);
		return syncRuntimeWorktree(trees[0]);
	}

	function pluginWorktrees(remote: string, hosts: readonly { home: string; skills: string }[]) {
		git(hosts[0].skills, "push", "--quiet", "origin", "HEAD:refs/heads/omp-plugins");
		for (const host of hosts) {
			git(host.home, "clone", "--quiet", "--branch", "omp-plugins", remote, join(host.home, ".omp", "plugins"));
		}
	}

	function cli(home: string, privateDir: string, args = ["--sync-runtime-worktrees"]) {
		return spawnSync(process.execPath, [
			join(import.meta.dir, "..", "omp", ".omp", "agent", "extensions", "stow-health.ts"),
			...args,
		], {
			cwd: home,
			env: { ...process.env, HOME: home, DOTFILES_PRIVATE_DIR: privateDir },
			encoding: "utf8",
			stdio: ["ignore", "pipe", "pipe"],
		});
	}

	test("commits what a writer left and pushes it to the tracked branch", async () => {
		const { home, remote, skills } = await skillsHome();
		await mkdir(join(skills, "new-skill"));
		await writeFile(join(skills, "new-skill", "SKILL.md"), "new\n");

		expect(await sync(home)).toEqual({
			saved: "~/.omp/agent/managed-skills: committed 1 change, pushed 1 commit",
		});
		expect(git(remote, "log", "-1", "--format=%s", "omp-managed-skills")).toBe("docs(skills): add new-skill");
		expect(git(skills, "status", "--porcelain")).toBe("");
	});

	test("leaves a worktree with a detached HEAD to a person", async () => {
		const { home, remote, skills } = await skillsHome();
		git(skills, "checkout", "--quiet", "--detach");
		await writeFile(join(skills, "seed", "SKILL.md"), "half-rebased\n");
		const before = git(remote, "rev-parse", "omp-managed-skills");

		expect(await sync(home)).toEqual({
			unsaved: "~/.omp/agent/managed-skills: 1 uncommitted (detached HEAD)",
		});
		expect(git(skills, "status", "--porcelain")).toBe("M seed/SKILL.md");
		expect(git(remote, "rev-parse", "omp-managed-skills")).toBe(before);
	});

	test("takes in a skill another host pushed", async () => {
		const a = await skillsHome();
		const b = await otherHost(a.remote);
		await mkdir(join(a.skills, "from-a"));
		await writeFile(join(a.skills, "from-a", "SKILL.md"), "a\n");
		await sync(a.home);

		expect(await sync(b.home)).toEqual({ received: "~/.omp/agent/managed-skills: 1 commit" });
		expect(git(b.skills, "rev-parse", "HEAD")).toBe(git(a.remote, "rev-parse", "omp-managed-skills"));
		expect(git(b.skills, "show", "HEAD:from-a/SKILL.md")).toBe("a");
	});

	test("puts its own skill on top of another host's before pushing", async () => {
		const a = await skillsHome();
		const b = await otherHost(a.remote);
		await mkdir(join(a.skills, "from-a"));
		await writeFile(join(a.skills, "from-a", "SKILL.md"), "a\n");
		await sync(a.home);
		await mkdir(join(b.skills, "from-b"));
		await writeFile(join(b.skills, "from-b", "SKILL.md"), "b\n");

		expect(await sync(b.home)).toEqual({
			saved: "~/.omp/agent/managed-skills: committed 1 change, pushed 1 commit",
			received: "~/.omp/agent/managed-skills: 1 commit",
		});
		expect(git(a.remote, "log", "--format=%s", "omp-managed-skills")).toBe(
			"docs(skills): add from-b\ndocs(skills): add from-a\nseed",
		);
	});

	test("keeps its commit unpushed when it conflicts with another host's", async () => {
		const a = await skillsHome();
		const b = await otherHost(a.remote);
		await writeFile(join(a.skills, "seed", "SKILL.md"), "a's version\n");
		await sync(a.home);
		const pushedByA = git(a.remote, "rev-parse", "omp-managed-skills");
		await writeFile(join(b.skills, "seed", "SKILL.md"), "b's version\n");

		expect(await sync(b.home)).toEqual({
			unsaved: "~/.omp/agent/managed-skills: 1 unpushed (conflicts with another host's change)",
		});
		expect(git(b.skills, "branch", "--show-current")).toBe("omp-managed-skills");
		expect(git(b.skills, "show", "HEAD:seed/SKILL.md")).toBe("b's version");
		expect(git(b.skills, "status", "--porcelain")).toBe("");
		expect(git(a.remote, "rev-parse", "omp-managed-skills")).toBe(pushedByA);
	});

	test("never applies a cached rerere resolution of the same content conflict", async () => {
		const a = await skillsHome();
		const b = await otherHost(a.remote);
		const skill = join(b.skills, "seed", "SKILL.md");
		await writeFile(join(a.skills, "seed", "SKILL.md"), "a's version\n");
		await sync(a.home);
		const pushedByA = git(a.remote, "rev-parse", "omp-managed-skills");
		await writeFile(skill, "b's version\n");
		git(b.skills, "add", "--all");
		git(b.skills, "commit", "--quiet", "-m", "b's local change");
		const localCommit = git(b.skills, "rev-parse", "HEAD");

		expect(() => git(b.skills, "pull", "--rebase", "--no-autostash", "--quiet")).toThrow();
		expect(git(b.skills, "diff", "--name-only", "--diff-filter=U")).toBe("seed/SKILL.md");
		await writeFile(skill, "cached resolution\n");
		git(b.skills, "rerere");
		const cache = join(b.skills, ".git", "rr-cache");
		const records = await readdir(cache);
		expect(records).toHaveLength(1);
		const postimage = join(cache, records[0], "postimage");
		expect(await readFile(postimage, "utf8")).toBe("cached resolution\n");
		git(b.skills, "rebase", "--abort");

		// Observe transient index changes too: abort alone would hide a replay.
		const applied = join(b.skills, ".git", "rerere-applied");
		await writeFile(
			join(b.skills, ".git", "hooks", "post-index-change"),
			"#!/bin/sh\n" +
			"if [ \"$(git show :seed/SKILL.md 2>/dev/null)\" = \"cached resolution\" ]; then\n" +
			"  printf 'applied\\n' >>\"$(git rev-parse --git-path rerere-applied)\"\nfi\n",
			{ mode: 0o755 },
		);
		// Prove this cache resolves this exact rebase when rerere is enabled.
		expect(() => git(b.skills, "pull", "--rebase", "--no-autostash", "--quiet")).toThrow();
		expect(git(b.skills, "show", ":seed/SKILL.md")).toBe("cached resolution");
		expect(await readFile(applied, "utf8")).toContain("applied");
		git(b.skills, "rebase", "--abort");
		await rm(applied);

		expect(await sync(b.home)).toEqual({
			unsaved: "~/.omp/agent/managed-skills: 1 unpushed (conflicts with another host's change)",
		});
		expect(await readFile(applied, "utf8").catch(() => undefined)).toBeUndefined();
		expect(git(b.skills, "rev-parse", "HEAD")).toBe(localCommit);
		expect(git(b.skills, "branch", "--show-current")).toBe("omp-managed-skills");
		expect(await readFile(skill, "utf8")).toBe("b's version\n");
		expect(git(b.skills, "status", "--porcelain")).toBe("");
		expect(git(a.remote, "rev-parse", "omp-managed-skills")).toBe(pushedByA);
		expect(await readFile(postimage, "utf8")).toBe("cached resolution\n");
		expect(git(b.skills, "config", "--get", "rerere.enabled")).toBe("true");
		expect(git(b.skills, "config", "--get", "rerere.autoupdate")).toBe("true");
	});

	test("CLI saves and receives both runtime branches, then reports clean", async () => {
		const a = await skillsHome();
		const b = await otherHost(a.remote);
		pluginWorktrees(a.remote, [a, b]);
		await mkdir(join(a.skills, "from-a"));
		await writeFile(join(a.skills, "from-a", "SKILL.md"), "a\n");
		await writeFile(join(a.home, ".omp", "plugins", "package.json"), '{"private":true}\n');

		const saved = cli(a.home, a.remote);
		expect(saved.status).toBe(0);
		expect(saved.stderr).toBe("");
		expect(saved.stdout.trim().split("\n")).toEqual([
			"~/.omp/agent/managed-skills: saved: committed 1 change, pushed 1 commit",
			"~/.omp/plugins: saved: committed 1 change, pushed 1 commit",
		]);
		const received = cli(b.home, a.remote);
		expect(received.status).toBe(0);
		expect(received.stderr).toBe("");
		expect(received.stdout.trim().split("\n")).toEqual([
			"~/.omp/agent/managed-skills: received: 1 commit",
			"~/.omp/plugins: received: 1 commit",
		]);
		expect(git(b.skills, "show", "HEAD:from-a/SKILL.md")).toBe("a");
		expect(git(join(b.home, ".omp", "plugins"), "show", "HEAD:package.json")).toBe('{"private":true}');
		const clean = cli(a.home, a.remote);
		expect(clean.status).toBe(0);
		expect(clean.stdout.trim().split("\n")).toEqual([
			"~/.omp/agent/managed-skills: clean",
			"~/.omp/plugins: clean",
		]);
	});

	test("CLI exits one on a content conflict and keeps the local commit", async () => {
		const a = await skillsHome();
		const b = await otherHost(a.remote);
		pluginWorktrees(a.remote, [a, b]);
		await writeFile(join(a.skills, "seed", "SKILL.md"), "a's version\n");
		expect(cli(a.home, a.remote).status).toBe(0);
		const pushedByA = git(a.remote, "rev-parse", "omp-managed-skills");
		await writeFile(join(b.skills, "seed", "SKILL.md"), "b's version\n");

		const conflict = cli(b.home, a.remote);
		expect(conflict.status).toBe(1);
		expect(conflict.stderr).toBe("");
		expect(conflict.stdout.trim().split("\n")).toEqual([
			"~/.omp/agent/managed-skills: unsaved: 1 unpushed (conflicts with another host's change)",
			"~/.omp/plugins: clean",
		]);
		expect(git(b.skills, "show", "HEAD:seed/SKILL.md")).toBe("b's version");
		expect(git(b.skills, "status", "--porcelain")).toBe("");
		expect(git(a.remote, "rev-parse", "omp-managed-skills")).toBe(pushedByA);
	});

	test("CLI reports a missing runtime worktree as broken when the private repository exists", async () => {
		const a = await skillsHome();
		const result = cli(a.home, a.remote);
		expect(result.status).toBe(1);
		expect(result.stdout.trim().split("\n")).toEqual([
			"~/.omp/agent/managed-skills: clean",
			"~/.omp/plugins: broken: missing or unreadable worktree; run ./setup.sh",
		]);
	});

	test("CLI skips an absent private repository without touching runtime edits", async () => {
		const a = await skillsHome();
		await writeFile(join(a.skills, "seed", "SKILL.md"), "local edit\n");
		const privateDir = join(a.home, "absent-private");
		const result = cli(a.home, privateDir);
		expect(result.status).toBe(0);
		expect(result.stdout.trim()).toBe(`skip: private repository absent: ${privateDir}`);
		expect(result.stderr).toBe("");
		expect(git(a.skills, "status", "--porcelain")).toBe("M seed/SKILL.md");
	});

	test("CLI rejects missing, unknown, or extra arguments with usage", async () => {
		const home = await newHome();
		for (const args of [[], ["--unknown"], ["--sync-runtime-worktrees", "--extra"]]) {
			const result = cli(home, join(home, "absent-private"), args);
			expect(result.status).toBe(2);
			expect(result.stdout).toBe("");
			expect(result.stderr.trim()).toBe("Usage: bun omp/.omp/agent/extensions/stow-health.ts --sync-runtime-worktrees");
		}
	});
});
