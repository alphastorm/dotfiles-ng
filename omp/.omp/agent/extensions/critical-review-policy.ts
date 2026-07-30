import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const REVIEWER_MARKER = "CRITICAL_REVIEWER_READ_ONLY_V1";
const INLINE_ISOLATED_MARKER = "CRITICAL_REVIEWER_INLINE_ISOLATED_V1";
const ISOLATED_TOOLS: Record<string, true> = { yield: true };
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
		const prompt = ctx.getSystemPrompt();
		const isCriticalReviewer = prompt.some(part =>
			part.includes(REVIEWER_MARKER) || part.includes(INLINE_ISOLATED_MARKER),
		);
		const allowedTools = prompt.some(part => part.includes(INLINE_ISOLATED_MARKER))
			? ISOLATED_TOOLS
			: READ_ONLY_TOOLS;
		if (!isCriticalReviewer || allowedTools[event.toolName]) return;

		return {
			block: true,
			reason: `Critical reviewers are read-only and isolated; ${event.toolName} is not permitted.`,
		};
	});
}
