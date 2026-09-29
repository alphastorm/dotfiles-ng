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
	saveRuntimeWorktree,
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

describe("runtime worktree backup", () => {
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

	test("commits what a writer left and pushes it to the tracked branch", async () => {
		const { home, remote, skills } = await skillsHome();
		await mkdir(join(skills, "new-skill"));
		await writeFile(join(skills, "new-skill", "SKILL.md"), "new\n");

		const { pending } = await inspectRuntimeWorktrees(home);
		expect(pending).toHaveLength(1);
		expect(await saveRuntimeWorktree(pending[0])).toEqual({
			saved: "~/.omp/agent/managed-skills: committed 1 change, pushed 1 commit",
		});
		expect(git(remote, "log", "-1", "--format=%s", "omp-managed-skills")).toBe("docs(skills): add new-skill");
		expect((await inspectRuntimeWorktrees(home)).pending).toEqual([]);
	});

	test("leaves a worktree with a detached HEAD to a person", async () => {
		const { home, remote, skills } = await skillsHome();
		git(skills, "checkout", "--quiet", "--detach");
		await writeFile(join(skills, "seed", "SKILL.md"), "half-rebased\n");
		const before = git(remote, "rev-parse", "omp-managed-skills");

		const { pending } = await inspectRuntimeWorktrees(home);
		expect(await saveRuntimeWorktree(pending[0])).toEqual({
			unsaved: "~/.omp/agent/managed-skills: 1 uncommitted (detached HEAD)",
		});
		expect(git(skills, "status", "--porcelain")).toBe("M seed/SKILL.md");
		expect(git(remote, "rev-parse", "omp-managed-skills")).toBe(before);
	});
});
