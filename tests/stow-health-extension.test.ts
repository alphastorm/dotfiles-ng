// Tests for omp/.omp/agent/extensions/stow-health.ts. They live outside the omp
// package because OMP loads every `.ts` directly under an agent `extensions/`
// directory as an extension, and setup.sh stows only its named packages.
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
	collectDangling,
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

describe("runtime worktree sync", () => {
	const saved = { ...process.env };

	beforeAll(async () => {
		// The machine's global config signs every commit; these repositories must not.
		const config = join(await newHome(), "gitconfig");
		await writeFile(config, "[user]\n\tname = stow-health test\n\temail = test@example.invalid\n");
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
});
