import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { existsSync } from "node:fs";
import { readdir } from "node:fs/promises";
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

/**
 * Warn when a stowed configuration link no longer resolves.
 *
 * Moving a file between the public and private dotfiles packages leaves the old
 * package's symlink behind, aimed at a path that no longer exists. Nothing fails
 * loudly: OMP just does not load whatever the link was for. Three agent
 * definitions -- among them a council reviewer its own qualification file marked
 * `councilEnabled: true` -- sat dangling and unnoticed until an unrelated stow run
 * happened to abort weeks later.
 *
 * Session start is the right moment to check, because it is exactly when those
 * definitions would have been loaded.
 */
export default function stowHealth(pi: ExtensionAPI): void {
	pi.on("session_start", async (_event, ctx) => {
		const root = join(homedir(), ".omp");
		const broken: string[] = [];
		await collectDangling(root, 0, broken);
		if (broken.length === 0) return;

		const list = broken.map(path => `  ~/.omp/${relative(root, path)}`).join("\n");
		const one = broken.length === 1;
		ctx.ui.notify(
			`${broken.length} dangling config symlink${one ? "" : "s"} — ` +
				`whatever ${one ? "it points" : "they point"} at is not loading:\n${list}\n` +
				`Repair: cd ~/.dotfiles && ./setup.sh`,
			"warning",
		);
	});
}
