import { createHmac, randomBytes } from "node:crypto";
import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type {
	ExtensionAPI,
	ExtensionContext,
	ProviderPerformanceEvent,
} from "@oh-my-pi/pi-coding-agent";

const SCHEMA = "omp.performance.v1";
const ROOT = join(homedir(), ".omp", "agent", "performance-v1");
const LEDGER_PATH = join(ROOT, "ledger.jsonl");
const SALT_PATH = join(ROOT, "pseudonym.salt");
const DELETE_AFTER = "2026-08-20";
const RETRIEVAL_TOOLS: Record<string, "read" | "grep" | "glob"> = {
	read: "read",
	grep: "grep",
	glob: "glob",
};

interface RetrievalGroup {
	cohort: string;
	turnOrdinal: number;
	responseOrdinal: number;
	readCount: number;
	grepCount: number;
	globCount: number;
	otherToolCount: number;
}

interface PendingFork {
	cohort: string;
	forkOrdinal: number;
	handlerMs?: number;
}

function initializePrivateFile(path: string): void {
	if (!existsSync(path)) writeFileSync(path, "", { encoding: "utf8", mode: 0o600, flag: "wx" });
	chmodSync(path, 0o600);
}

function loadSalt(): Buffer {
	mkdirSync(ROOT, { recursive: true, mode: 0o700 });
	if (!existsSync(SALT_PATH)) {
		writeFileSync(SALT_PATH, randomBytes(32).toString("base64"), { encoding: "utf8", mode: 0o600, flag: "wx" });
	}
	chmodSync(SALT_PATH, 0o600);
	return Buffer.from(readFileSync(SALT_PATH, "utf8").trim(), "base64");
}

const salt = loadSalt();
initializePrivateFile(LEDGER_PATH);

function append(record: Record<string, unknown>): void {
	try {
		appendFileSync(
			LEDGER_PATH,
			`${JSON.stringify({ schema: SCHEMA, observedAtUnixMs: Date.now(), ...record })}\n`,
			{ encoding: "utf8", mode: 0o600 },
		);
	} catch {
		// Collection is observational and must never affect the agent lifecycle.
	}
}

function pseudonym(ctx: ExtensionContext): string {
	const sessionId = ctx.sessionManager.getSessionId();
	const leafId = ctx.sessionManager.getLeafId() ?? "none";
	return createHmac("sha256", salt)
		.update(SCHEMA)
		.update("\0")
		.update(sessionId)
		.update("\0")
		.update(leafId)
		.digest("hex")
		.slice(0, 32);
}

function safeProviderMetrics(event: ProviderPerformanceEvent): Record<string, unknown> {
	return {
		api: event.api,
		transport: event.transport,
		origin: event.origin,
		requestKind: event.requestKind,
		requestMode: event.requestMode,
		inputItemCount: event.inputItemCount,
		inputJsonBytes: event.inputJsonBytes,
		responseItemCount: event.responseItemCount,
		durationMs: event.durationMs,
		...(event.firstEventMs !== undefined && { firstEventMs: event.firstEventMs }),
		...(event.ttftMs !== undefined && { ttftMs: event.ttftMs }),
		retryCount: event.retryCount,
		baselineCommitted: event.baselineCommitted,
		inputTokens: event.inputTokens,
		outputTokens: event.outputTokens,
		cacheReadTokens: event.cacheReadTokens,
		...(event.premiumRequests !== undefined && { premiumRequests: event.premiumRequests }),
	};
}

export default function performanceM0(pi: ExtensionAPI): void {
	let turnOrdinal = 0;
	let responseOrdinal = 0;
	let group: RetrievalGroup | undefined;
	let forkStartedAt: number | undefined;
	let forkOrdinal = 0;
	let pendingFork: PendingFork | undefined;

	const flushRetrievalGroup = (): void => {
		if (!group) return;
		const retrievalCount = group.readCount + group.grepCount + group.globCount;
		if (retrievalCount > 0) {
			append({
				type: "assistant_retrieval_group",
				cohort: group.cohort,
				turnOrdinal: group.turnOrdinal,
				responseOrdinal: group.responseOrdinal,
				readCount: group.readCount,
				grepCount: group.grepCount,
				globCount: group.globCount,
				otherToolCount: group.otherToolCount,
				retrievalCount,
				nativeMultiTool: retrievalCount > 1,
			});
		}
		group = undefined;
	};

	append({ type: "collector_started", deleteAfter: DELETE_AFTER });

	pi.on("session_before_switch", event => {
		if (event.reason === "fork") forkStartedAt = performance.now();
	});

	pi.on("session_switch", (event, ctx) => {
		if (event.reason !== "fork") return;
		flushRetrievalGroup();
		forkOrdinal += 1;
		pendingFork = {
			cohort: pseudonym(ctx),
			forkOrdinal,
			...(forkStartedAt !== undefined && { handlerMs: performance.now() - forkStartedAt }),
		};
		append({ type: "fork_completed", ...pendingFork });
		forkStartedAt = undefined;
	});

	pi.on("turn_start", (event, ctx) => {
		flushRetrievalGroup();
		turnOrdinal = event.turnIndex;
		responseOrdinal = 0;
		group = {
			cohort: pseudonym(ctx),
			turnOrdinal,
			responseOrdinal,
			readCount: 0,
			grepCount: 0,
			globCount: 0,
			otherToolCount: 0,
		};
	});

	pi.on("message_start", (event, ctx) => {
		if (event.message.role !== "assistant") return;
		flushRetrievalGroup();
		responseOrdinal += 1;
		group = {
			cohort: pseudonym(ctx),
			turnOrdinal,
			responseOrdinal,
			readCount: 0,
			grepCount: 0,
			globCount: 0,
			otherToolCount: 0,
		};
	});

	pi.on("tool_execution_start", event => {
		if (!group) return;
		const retrieval = RETRIEVAL_TOOLS[event.toolName];
		if (retrieval === "read") group.readCount += 1;
		else if (retrieval === "grep") group.grepCount += 1;
		else if (retrieval === "glob") group.globCount += 1;
		else group.otherToolCount += 1;
	});

	pi.on("turn_end", () => flushRetrievalGroup());

	pi.on("provider_performance", (event, ctx) => {
		const cohort = pseudonym(ctx);
		const metrics = safeProviderMetrics(event);
		append({ type: "provider_completed", cohort, ...metrics });
		if (event.origin === "primary" && pendingFork) {
			append({
				type: "fork_first_provider",
				cohort: pendingFork.cohort,
				forkOrdinal: pendingFork.forkOrdinal,
				...(pendingFork.handlerMs !== undefined && { handlerMs: pendingFork.handlerMs }),
				...metrics,
			});
			pendingFork = undefined;
		}
	});
}
