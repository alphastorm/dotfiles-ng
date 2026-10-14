// Run with: bun test tests/install-stock-omp.test.ts
// Every download and runtime write stays in a temporary HOME; file:// fixtures
// exercise curl and the pinned checksum without using the live OMP installation.
import { afterAll, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, readdir, readlink, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const script = resolve(import.meta.dir, "../scripts/install-stock-omp.sh");
const homes: string[] = [];
const executable = Buffer.from("#!/bin/sh\nprintf 'omp/fixture\\n'\n");
const digest = createHash("sha256").update(executable).digest("hex");

afterAll(async () => {
	await Promise.all(homes.map(home => rm(home, { recursive: true, force: true })));
});

async function fixture(version = "18.10.4", checksum = digest) {
	const home = await mkdtemp(join(tmpdir(), "omp-stock home-"));
	homes.push(home);
	const releases = join(home, "release-fixtures");
	const pin = join(home, ".omp", "stock-runtime.json");
	const root = join(home, ".local", "opt", "omp-stock");

	async function writePin(nextVersion: string, nextChecksum = digest) {
		const release = join(releases, `v${nextVersion}`);
		await mkdir(release, { recursive: true });
		await mkdir(dirname(pin), { recursive: true });
		for (const platform of ["darwin-arm64", "linux-arm64"]) {
			await writeFile(join(release, `omp-${platform}`), executable);
		}
		await writeFile(pin, JSON.stringify({
			version: nextVersion,
			release_url: pathToFileURL(release).href,
			platforms: {
				"darwin-arm64": { asset: "omp-darwin-arm64", sha256: nextChecksum },
				"linux-arm64": { asset: "omp-linux-arm64", sha256: nextChecksum },
			},
		}));
	}

	await writePin(version, checksum);
	const run = (...args: string[]) => spawnSync("/bin/bash", [script, ...args], {
		env: { HOME: home, PATH: process.env.PATH, OMP_STOCK_PIN: pin },
		encoding: "utf8",
	});
	const ok = (...args: string[]) => {
		const result = run(...args);
		expect({ status: result.status, stderr: result.stderr }).toEqual({ status: 0, stderr: "" });
		return result;
	};
	return { home, root, pin, releases, run, ok, writePin };
}

async function exists(path: string): Promise<boolean> {
	try {
		await lstat(path);
		return true;
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
		throw error;
	}
}

// Include contents, modes, link targets and mtimes, so a check cannot silently
// repair a file, select a version, or touch an existing launcher.
async function snapshot(path: string): Promise<unknown> {
	const stat = await lstat(path);
	if (stat.isSymbolicLink()) return { link: await readlink(path), mtime: stat.mtimeMs };
	if (stat.isDirectory()) {
		return Object.fromEntries(await Promise.all((await readdir(path)).sort().map(async name => [name, await snapshot(join(path, name))])));
	}
	return { bytes: (await readFile(path)).toString("base64"), mode: stat.mode & 0o777, mtime: stat.mtimeMs };
}

test("refuses a checksum mismatch without installing or selecting the asset", async () => {
	const f = await fixture("18.10.4", "0".repeat(64));
	const result = f.run();
	expect(result.status).not.toBe(0);
	expect(result.stderr).toContain("checksum");
	expect(await exists(join(f.root, "18.10.4", "omp"))).toBe(false);
	expect(await exists(join(f.root, "current"))).toBe(false);
	expect(await exists(join(f.home, ".local", "bin", "omp"))).toBe(false);
});

test("stages a verified 0755 binary and its pin without selecting or activating it", async () => {
	const f = await fixture();
	f.ok("--stage");
	const binary = join(f.root, "18.10.4", "omp");
	expect(await readFile(binary)).toEqual(executable);
	expect((await lstat(binary)).mode & 0o777).toBe(0o755);
	expect(await readFile(join(f.root, "18.10.4", "stock-runtime.json"))).toEqual(await readFile(f.pin));
	expect(await exists(join(f.root, "current"))).toBe(false);
	expect(await exists(join(f.home, ".local", "bin", "omp"))).toBe(false);
});

test("default install selects the pin and is idempotent without another download", async () => {
	const f = await fixture();
	f.ok();
	expect(await readlink(join(f.root, "current"))).toBe("18.10.4");
	expect(await exists(join(f.root, "previous"))).toBe(false);
	await rm(f.releases, { recursive: true });
	const before = await snapshot(f.home);
	f.ok();
	expect(await snapshot(f.home)).toEqual(before);
});

test("select and rollback swap version links; selecting the same version preserves previous", async () => {
	const f = await fixture("18.10.3");
	f.ok("--stage");
	f.ok("--select", "18.10.3");
	await f.writePin("18.10.4");
	f.ok("--stage");
	expect(await readlink(join(f.root, "current"))).toBe("18.10.3");
	f.ok("--select", "18.10.4");
	expect(await readlink(join(f.root, "current"))).toBe("18.10.4");
	expect(await readlink(join(f.root, "previous"))).toBe("18.10.3");
	f.ok("--select", "18.10.4");
	expect(await readlink(join(f.root, "previous"))).toBe("18.10.3");
	f.ok("--rollback");
	expect(await readlink(join(f.root, "current"))).toBe("18.10.3");
	expect(await readlink(join(f.root, "previous"))).toBe("18.10.4");
	f.ok("--rollback");
	expect(await readlink(join(f.root, "current"))).toBe("18.10.4");
	expect(await readlink(join(f.root, "previous"))).toBe("18.10.3");
	f.ok("--select", "18.10.3");
	expect(await readlink(join(f.root, "current"))).toBe("18.10.3");
});

test("check has no effect when absent, staged, selected, or corrupted and never downloads", async () => {
	const f = await fixture();
	let before = await snapshot(f.home);
	expect(f.run("--check").status).not.toBe(0);
	expect(await snapshot(f.home)).toEqual(before);
	f.ok("--stage");
	await rm(f.releases, { recursive: true });
	before = await snapshot(f.home);
	f.ok("--check");
	expect(await snapshot(f.home)).toEqual(before);
	f.ok("--select", "18.10.4");
	before = await snapshot(f.home);
	f.ok("--check");
	expect(await snapshot(f.home)).toEqual(before);
	await writeFile(join(f.root, "18.10.4", "omp"), "corrupted");
	before = await snapshot(f.home);
	const result = f.run("--check");
	expect(result.status).not.toBe(0);
	expect(result.stderr).toContain("checksum");
	expect(await snapshot(f.home)).toEqual(before);
});

test("rejects check plus activation before changing HOME", async () => {
	const f = await fixture();
	const before = await snapshot(f.home);
	expect(f.run("--check", "--activate-bin").status).not.toBe(0);
	expect(await snapshot(f.home)).toEqual(before);
});

test("refuses unstaged and corrupted selections and a rollback without a previous version", async () => {
	const f = await fixture();
	expect(f.run("--select", "18.10.4").status).not.toBe(0);
	expect(f.run("--rollback").status).not.toBe(0);
	f.ok();
	const before = await snapshot(f.home);
	expect(f.run("--select", "../outside").status).not.toBe(0);
	expect(f.run("--select", "18.10.3").status).not.toBe(0);
	expect(await snapshot(f.home)).toEqual(before);
	await writeFile(join(f.root, "18.10.4", "omp"), "corrupted");
	expect(f.run("--select", "18.10.4").status).not.toBe(0);
	expect(await readlink(join(f.root, "current"))).toBe("18.10.4");
});

test("activation backs up an existing regular launcher and atomically links the selected stock binary", async () => {
	const f = await fixture();
	const bin = join(f.home, ".local", "bin", "omp");
	await mkdir(dirname(bin), { recursive: true });
	await writeFile(bin, "existing launcher\n");
	await chmod(bin, 0o700);
	f.ok("--activate-bin");
	expect(await readlink(bin)).toBe(join(f.root, "current", "omp"));
	const backups = await readdir(join(f.root, "backup"));
	expect(backups).toHaveLength(1);
	expect(backups[0]).toMatch(/^omp\.\d{8}T\d{6}Z(?:\.\d+)?$/);
	expect(await readFile(join(f.root, "backup", backups[0]), "utf8")).toBe("existing launcher\n");
	expect((await lstat(join(f.root, "backup", backups[0]))).mode & 0o777).toBe(0o700);
	const before = await snapshot(f.home);
	f.ok("--select", "18.10.4", "--activate-bin");
	expect(await snapshot(f.home)).toEqual(before);
});

test("activation without a launcher needs no backup; replacing a symlink leaves its old target untouched", async () => {
	const fresh = await fixture();
	fresh.ok("--activate-bin");
	expect(await readlink(join(fresh.home, ".local", "bin", "omp"))).toBe(join(fresh.root, "current", "omp"));
	expect(await exists(join(fresh.root, "backup"))).toBe(false);
	const f = await fixture();
	const oldTarget = join(f.home, "old-launcher");
	const bin = join(f.home, ".local", "bin", "omp");
	await writeFile(oldTarget, "keep this target\n");
	await mkdir(dirname(bin), { recursive: true });
	await symlink(oldTarget, bin);
	f.ok("--activate-bin");
	expect(await readFile(oldTarget, "utf8")).toBe("keep this target\n");
	expect(await readlink(bin)).toBe(join(f.root, "current", "omp"));
	expect(await exists(join(f.root, "backup"))).toBe(false);
});
