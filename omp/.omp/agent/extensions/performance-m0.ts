import { createHmac, randomBytes } from "node:crypto";
import {
	appendFileSync,
	chmodSync,
	closeSync,
	existsSync,
	mkdirSync,
	openSync,
	readdirSync,
	readFileSync,
	renameSync,
	rmSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";

const SCHEMA = "omp.performance.v2";
const ROOT = join(homedir(), ".omp", "agent", "performance-v2");
const LEDGER_PATH = join(ROOT, "ledger.jsonl");
const SALT_PATH = join(ROOT, "pseudonym.salt");
const DELETE_AFTER = "2026-08-20";
const LEDGER_MAX_BYTES = 32 * 1024 * 1024;
const LEDGER_ARCHIVE_COUNT = 1;
const LEDGER_ROTATION_LOCK_PATH = join(ROOT, ".ledger-rotation.lock");
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

type FastModePolicy = "manual" | "auto";

interface PendingProviderRequest {
	requestedServiceTier: string;
	fastModePolicy?: FastModePolicy;
}

function initializePrivateFile(path: string): void {
	if (!existsSync(path)) writeFileSync(path, "", { encoding: "utf8", mode: 0o600, flag: "wx" });
	chmodSync(path, 0o600);
}

function rotateLedgerIfNeeded(): void {
	let lockFd: number | undefined;
	try {
		if (statSync(LEDGER_PATH).size < LEDGER_MAX_BYTES) return;
		lockFd = openSync(LEDGER_ROTATION_LOCK_PATH, "wx", 0o600);
		if (statSync(LEDGER_PATH).size < LEDGER_MAX_BYTES) return;
		const archivePath = join(ROOT, `ledger.${Date.now()}.${process.pid}.jsonl`);
		renameSync(LEDGER_PATH, archivePath);
		initializePrivateFile(LEDGER_PATH);
		const archives = readdirSync(ROOT)
			.filter(name => name.startsWith("ledger.") && name.endsWith(".jsonl") && name !== "ledger.jsonl")
			.sort()
			.reverse();
		for (const name of archives.slice(LEDGER_ARCHIVE_COUNT)) rmSync(join(ROOT, name), { force: true });
	} catch {
		// Rotation is best-effort; collection must remain available under contention.
	} finally {
		if (lockFd !== undefined) {
			closeSync(lockFd);
			rmSync(LEDGER_ROTATION_LOCK_PATH, { force: true });
		}
	}
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
		rotateLedgerIfNeeded();
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
	return createHmac("sha256", salt)
		.update(SCHEMA)
		.update("\0")
		.update(sessionId)
		.digest("hex")
		.slice(0, 32);
}

function asObject(value: unknown): Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}

function optionalNumber(value: unknown): number | undefined {
	return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function optionalString(value: unknown): string | undefined {
	return typeof value === "string" && value.length > 0 ? value : undefined;
}

function providerRequestServiceTier(value: unknown): string | undefined {
	const payload = asObject(value);
	return (
		optionalString(payload.service_tier) ??
		optionalString(payload.serviceTier) ??
		(payload.speed === "fast" ? "priority" : undefined)
	);
}

function latestFastModePolicy(ctx: ExtensionContext): FastModePolicy | undefined {
	const branch = ctx.sessionManager.getBranch();
	for (let index = branch.length - 1; index >= 0; index -= 1) {
		const entry = asObject(branch[index]);
		if (entry.type !== "service_tier_change") continue;
		if (entry.fastModePolicy === "manual" || entry.fastModePolicy === "auto") return entry.fastModePolicy;
	}
	return undefined;
}

function effectiveServiceTier(value: unknown, request: PendingProviderRequest | undefined): string | undefined {
	if (!request) return undefined;
	const message = asObject(value);
	const disabledFeatures = Array.isArray(message.disabledFeatures) ? message.disabledFeatures : [];
	if (disabledFeatures.includes("priority")) return "default";
	const premiumRequests = optionalNumber(asObject(message.usage).premiumRequests);
	if (premiumRequests !== undefined && premiumRequests > 0) return "priority";
	return request.requestedServiceTier;
}

function safeProviderMetrics(
	value: unknown,
	retryAttempt: number,
	request: PendingProviderRequest | undefined,
): Record<string, unknown> {
	const message = asObject(value);
	const usage = asObject(message.usage);
	const retryRecovery = asObject(message.retryRecovery);
	const api = optionalString(message.api);
	const provider = optionalString(message.provider);
	const model = optionalString(message.model);
	const stopReason = optionalString(message.stopReason);
	const durationMs = optionalNumber(message.duration);
	const ttftMs = optionalNumber(message.ttft);
	const inputTokens = optionalNumber(usage.input);
	const outputTokens = optionalNumber(usage.output);
	const cacheReadTokens = optionalNumber(usage.cacheRead);
	const cacheWriteTokens = optionalNumber(usage.cacheWrite);
	const reasoningTokens = optionalNumber(usage.reasoningTokens);
	const totalTokens = optionalNumber(usage.totalTokens);
	const cost = optionalNumber(usage.cost) ?? optionalNumber(asObject(usage.cost).total);
	const premiumRequests = optionalNumber(usage.premiumRequests);
	const errorStatus = optionalNumber(message.errorStatus);
	const requestedServiceTier = request?.requestedServiceTier;
	const realizedServiceTier = effectiveServiceTier(message, request);
	return {
		...(api !== undefined && { api }),
		...(provider !== undefined && { provider }),
		...(model !== undefined && { model }),
		requestKind: "agent",
		...(stopReason !== undefined && { stopReason }),
		...(durationMs !== undefined && { durationMs }),
		...(ttftMs !== undefined && { firstEventMs: ttftMs, ttftMs }),
		retryCount: Math.max(retryAttempt, optionalNumber(retryRecovery.attempt) ?? 0),
		baselineCommitted: stopReason !== "error" && stopReason !== "aborted",
		...(requestedServiceTier !== undefined && { requestedServiceTier }),
		...(realizedServiceTier !== undefined && { effectiveServiceTier: realizedServiceTier }),
		...(request?.fastModePolicy !== undefined && { fastModePolicy: request.fastModePolicy }),
		...(inputTokens !== undefined && { inputTokens }),
		...(outputTokens !== undefined && { outputTokens }),
		...(cacheReadTokens !== undefined && { cacheReadTokens }),
		...(cacheWriteTokens !== undefined && { cacheWriteTokens }),
		...(reasoningTokens !== undefined && { reasoningTokens }),
		...(totalTokens !== undefined && { totalTokens }),
		...(cost !== undefined && { cost }),
		...(premiumRequests !== undefined && { premiumRequests }),
		...(errorStatus !== undefined && { errorStatus }),
	};
}

export default function performanceM0(pi: ExtensionAPI): void {
	let turnOrdinal = 0;
	let responseOrdinal = 0;
	let group: RetrievalGroup | undefined;
	let forkStartedAt: number | undefined;
	let forkOrdinal = 0;
	let pendingFork: PendingFork | undefined;
	let activeRetryAttempt = 0;
	let pendingProviderRequest: PendingProviderRequest | undefined;

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
		activeRetryAttempt = 0;
		pendingProviderRequest = undefined;
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

	pi.on("before_provider_request", (event, ctx) => {
		const fastModePolicy = latestFastModePolicy(ctx);
		pendingProviderRequest = {
			requestedServiceTier: providerRequestServiceTier(event.payload) ?? "default",
			...(fastModePolicy !== undefined && { fastModePolicy }),
		};
	});

	pi.on("auto_retry_start", event => {
		activeRetryAttempt = event.attempt;
	});

	pi.on("auto_retry_end", () => {
		activeRetryAttempt = 0;
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

	pi.on("message_end", (event, ctx) => {
		const message = asObject(event.message);
		if (message.role !== "assistant") return;
		const cohort = pseudonym(ctx);
		const origin = ctx.sessionManager.getHeader().parentSession ? "descendant" : "primary";
		const metrics = safeProviderMetrics(message, activeRetryAttempt, pendingProviderRequest);
		pendingProviderRequest = undefined;
		append({ type: "provider_completed", cohort, origin, ...metrics });
		if (pendingFork) {
			append({
				type: "fork_first_provider",
				cohort: pendingFork.cohort,
				forkOrdinal: pendingFork.forkOrdinal,
				...(pendingFork.handlerMs !== undefined && { handlerMs: pendingFork.handlerMs }),
				origin,
				...metrics,
			});
			pendingFork = undefined;
		}
	});
}
