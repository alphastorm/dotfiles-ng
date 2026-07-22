import { spawn } from "node:child_process";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";

const SOUND = "/System/Library/Sounds/Glass.aiff";
const SOUND_DEDUP_WINDOW_MS = 1_700;
const ATTENTION_REMINDER_INTERVAL_MS = 30_000;
const pendingAttention = new Set<string>();
let lastSoundAt = Number.NEGATIVE_INFINITY;
let reminderTimer: Timer | undefined;

function playInputSound(): void {
	const now = Date.now();
	if (now - lastSoundAt < SOUND_DEDUP_WINDOW_MS) return;
	lastSoundAt = now;

	const player = spawn("/usr/bin/afplay", [SOUND], {
		detached: true,
		stdio: "ignore",
	});
	player.on("error", () => {});
	player.unref();
}

function requestAttention(key: string, ctx: ExtensionContext): void {
	if (pendingAttention.has(key)) return;

	pendingAttention.add(key);
	playInputSound();
	reminderTimer ??= ctx.setInterval(() => {
		if (pendingAttention.size > 0) playInputSound();
	}, ATTENTION_REMINDER_INTERVAL_MS);
}

function resolveAttention(key: string, ctx: ExtensionContext): void {
	pendingAttention.delete(key);
	if (pendingAttention.size > 0 || reminderTimer === undefined) return;

	ctx.clearTimer(reminderTimer);
	reminderTimer = undefined;
}

function resolveAllAttention(ctx: ExtensionContext): void {
	pendingAttention.clear();
	if (reminderTimer === undefined) return;

	ctx.clearTimer(reminderTimer);
	reminderTimer = undefined;
}

export default function inputSound(pi: ExtensionAPI): void {
	pi.on("tool_call", (event, ctx) => {
		if (event.toolName === "ask" && ctx.hasUI) requestAttention(`ask:${event.toolCallId}`, ctx);
	});

	pi.on("tool_result", (event, ctx) => {
		if (event.toolName === "ask") resolveAttention(`ask:${event.toolCallId}`, ctx);
	});

	pi.on("tool_approval_requested", (event, ctx) => {
		if (ctx.hasUI) requestAttention(`approval:${event.toolCallId}`, ctx);
	});

	pi.on("tool_approval_resolved", (event, ctx) => {
		resolveAttention(`approval:${event.toolCallId}`, ctx);
	});

	pi.on("agent_end", (event, ctx) => {
		resolveAllAttention(ctx);
		if (ctx.hasUI && !event.willContinue && !ctx.hasPendingMessages()) playInputSound();
	});

	pi.on("session_shutdown", (_event, ctx) => {
		resolveAllAttention(ctx);
	});
}
