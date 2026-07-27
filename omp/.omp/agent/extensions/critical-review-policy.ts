import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const REVIEWER_MARKER = "CRITICAL_REVIEWER_READ_ONLY_V1";
const READ_ONLY_TOOLS: Record<string, true> = {
	read: true,
	grep: true,
	glob: true,
	lsp: true,
	ast_grep: true,
	yield: true,
};

/** Enforce the reviewer boundary even when OMP injects coordination tools into subagents. */
export default function criticalReviewPolicy(pi: ExtensionAPI): void {
	pi.on("tool_call", (event, ctx) => {
		const isCriticalReviewer = ctx.getSystemPrompt().some(part => part.includes(REVIEWER_MARKER));
		if (!isCriticalReviewer || READ_ONLY_TOOLS[event.toolName]) return;

		return {
			block: true,
			reason: `Critical reviewers are read-only and isolated; ${event.toolName} is not permitted.`,
		};
	});
}
