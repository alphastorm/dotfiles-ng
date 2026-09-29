// Tests for omp/.omp/agent/extensions/stow-health.ts. They live outside the omp
// package because OMP loads every `.ts` directly under an agent `extensions/`
// directory as an extension, and setup.sh stows only its named packages.
import { expect, test } from "bun:test";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { collectDangling } from "../omp/.omp/agent/extensions/stow-health.ts";

test("reports a stow link whose package file is gone, not a runtime link that never resolved", async () => {
	const home = await mkdtemp(join(tmpdir(), "stow-health-"));
	try {
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
	} finally {
		await rm(home, { recursive: true, force: true });
	}
});
