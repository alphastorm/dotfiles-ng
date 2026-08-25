import { createHash } from "node:crypto";
import {
	chmod,
	link,
	mkdir,
	mkdtemp,
	open,
	readdir,
	readFile,
	rename,
	rm,
} from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { isJsonObject } from "./json-object.ts";

export const ORACLE_PROGRAMMATIC_API_KEY = "omp.pi-oracle.programmatic.v1";
const TERMINAL_JOB_STATUSES: Record<string, true> = {
	complete: true,
	failed: true,
	cancelled: true,
};
const COLLECTOR_POLL_MS = 5_000;
const COLLECTOR_TIMEOUT_MS = 6 * 60 * 60 * 1_000;
const spawnedCollectors = new Set<string>();

interface ToolResult {
	content?: unknown;
	details?: unknown;
}

interface OracleProgrammaticApi {
	version: 1;
	preflight(ctx: ExtensionContext): Promise<ToolResult>;
	submit(
		ctx: ExtensionContext,
		params: { provider: "chatgpt"; preset: string; prompt: string; files: string[] },
	): Promise<ToolResult>;
}

interface OracleRequest {
	repositoryPath: string;
	subjectCommit: string;
	files: string[];
	prompt: string;
	promptSha256: string;
}

interface OracleShadow {
	schemaVersion: 1;
	reviewId: string;
	requestPath: string;
	dispatchPath: string;
	resultPath: string;
	datasetRecordPath: string;
	shadowId: string;
	modelFamily: string;
	correlationGroup: string;
	providerRoute: string;
	accessProfile: string;
	dataAllowlistKey: string;
	preset: string;
	executionMode: "pi_oracle_async";
	evidenceDelivery: "repository";
	selection: "selected" | "skipped" | "disabled";
	reasonCodes: string[];
	standing: "none";
	blocksClosure: false;
	receivesPeerOutput: false;
	request: OracleRequest | null;
}

interface DispatchEnvelope {
	schemaVersion: 2;
	reviewClass: "focused" | "initial" | "targeted-refuter";
	subjectDigest: string;
	receiptSha256: string;
	oracleShadow: OracleShadow | null;
}

interface PendingCollector {
	schemaVersion: 1;
	envelopePath: string;
	envelopeSha256: string;
	subjectDigest: string;
	requestSha256: string;
	dispatchPath: string;
	resultPath: string;
	datasetRecordPath: string;
	jobPath: string | null;
	createdAt: string;
}

function sha256(value: string | Uint8Array): string {
	return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value: unknown): string {
	return `${JSON.stringify(value, null, 2)}\n`;
}

function now(): string {
	return new Date().toISOString();
}

function agentDir(): string {
	return process.env.PI_CODING_AGENT_DIR || join(homedir(), ".omp", "agent");
}

function pendingDirectory(): string {
	return join(agentDir(), "critical-review-oracle-shadow", "pending");
}

async function atomicReplaceJson(path: string, value: unknown): Promise<void> {
	await mkdir(dirname(path), { recursive: true, mode: 0o700 });
	const temporary = `${path}.${process.pid}.${crypto.randomUUID()}.tmp`;

	await Bun.write(temporary, canonicalJson(value));
	await chmod(temporary, 0o600).catch(() => undefined);
	await rename(temporary, path);
	await chmod(path, 0o600).catch(() => undefined);
}

async function writeJsonOnce(path: string, value: unknown): Promise<boolean> {
	await mkdir(dirname(path), { recursive: true, mode: 0o700 });
	const temporary = `${path}.${process.pid}.${crypto.randomUUID()}.tmp`;
	let handle: Awaited<ReturnType<typeof open>> | undefined;
	try {
		handle = await open(temporary, "wx", 0o600);
		await handle.writeFile(canonicalJson(value), "utf8");
		await handle.sync();
		await handle.close();
		handle = undefined;
		await link(temporary, path);
		return true;
	} catch (error) {
		if (isJsonObject(error) && error.code === "EEXIST") return false;
		throw error;
	} finally {
		await handle?.close();
		await rm(temporary, { force: true });
	}
}

async function readJson(path: string): Promise<Record<string, unknown>> {
	const parsed: unknown = JSON.parse(await readFile(path, "utf8"));
	if (!isJsonObject(parsed)) throw new Error(`${path} is not a JSON object`);
	return parsed;
}

function requireString(record: Record<string, unknown>, key: string): string {
	const value = record[key];
	if (typeof value !== "string" || value.length === 0) {
		throw new Error(`${key} must be a non-empty string`);
	}
	return value;
}

function requireStringArray(record: Record<string, unknown>, key: string): string[] {
	const value = record[key];
	if (!Array.isArray(value) || value.length === 0 || !value.every((item) => typeof item === "string" && item.length > 0)) {
		throw new Error(`${key} must be a non-empty string array`);
	}
	return value;
}

function parseRequest(value: unknown): OracleRequest | null {
	if (value === null) return null;
	if (!isJsonObject(value)) throw new Error("oracleShadow.request must be an object or null");
	const request: OracleRequest = {
		repositoryPath: requireString(value, "repositoryPath"),
		subjectCommit: requireString(value, "subjectCommit"),
		files: requireStringArray(value, "files"),
		prompt: requireString(value, "prompt"),
		promptSha256: requireString(value, "promptSha256"),
	};
	if (sha256(request.prompt) !== request.promptSha256) {
		throw new Error("oracleShadow request prompt digest does not match its bytes");
	}
	return request;
}

function parseShadow(value: unknown): OracleShadow | null {
	if (value === null) return null;
	if (!isJsonObject(value) || value.schemaVersion !== 1) {
		throw new Error("oracleShadow is not a version 1 object");
	}
	const selection = value.selection;
	if (selection !== "selected" && selection !== "skipped" && selection !== "disabled") {
		throw new Error("oracleShadow.selection is invalid");
	}
	if (!Array.isArray(value.reasonCodes) || !value.reasonCodes.every((item) => typeof item === "string")) {
		throw new Error("oracleShadow.reasonCodes must be a string array");
	}
	if (
		value.executionMode !== "pi_oracle_async" ||
		value.evidenceDelivery !== "repository" ||
		value.standing !== "none" ||
		value.blocksClosure !== false ||
		value.receivesPeerOutput !== false
	) {
		throw new Error("oracleShadow standing or execution boundary is invalid");
	}
	const request = parseRequest(value.request);
	if ((selection === "selected") !== (request !== null)) {
		throw new Error("only a selected oracleShadow may carry a request");
	}
	return {
		schemaVersion: 1,
		reviewId: requireString(value, "reviewId"),
		requestPath: requireString(value, "requestPath"),
		dispatchPath: requireString(value, "dispatchPath"),
		resultPath: requireString(value, "resultPath"),
		datasetRecordPath: requireString(value, "datasetRecordPath"),
		shadowId: requireString(value, "shadowId"),
		modelFamily: requireString(value, "modelFamily"),
		correlationGroup: requireString(value, "correlationGroup"),
		providerRoute: requireString(value, "providerRoute"),
		accessProfile: requireString(value, "accessProfile"),
		dataAllowlistKey: requireString(value, "dataAllowlistKey"),
		preset: requireString(value, "preset"),
		executionMode: "pi_oracle_async",
		evidenceDelivery: "repository",
		selection,
		reasonCodes: [...value.reasonCodes] as string[],
		standing: "none",
		blocksClosure: false,
		receivesPeerOutput: false,
		request,
	};
}

function parseEnvelope(value: Record<string, unknown>): DispatchEnvelope {
	if (value.schemaVersion !== 2) throw new Error("Oracle shadow requires dispatch envelope schemaVersion 2");
	const reviewClass = value.reviewClass;
	if (reviewClass !== "focused" && reviewClass !== "initial" && reviewClass !== "targeted-refuter") {
		throw new Error("dispatch envelope reviewClass is invalid");
	}
	const shadow = parseShadow(value.oracleShadow);
	if ((reviewClass === "initial") !== (shadow !== null)) {
		throw new Error("only an initial full council may carry oracleShadow state");
	}
	return {
		schemaVersion: 2,
		reviewClass,
		subjectDigest: requireString(value, "subjectDigest"),
		receiptSha256: requireString(value, "receiptSha256"),
		oracleShadow: shadow,
	};
}

function laneMetadata(shadow: OracleShadow): Record<string, unknown> {
	return {
		shadowId: shadow.shadowId,
		modelFamily: shadow.modelFamily,
		correlationGroup: shadow.correlationGroup,
		providerRoute: shadow.providerRoute,
		accessProfile: shadow.accessProfile,
		dataAllowlistKey: shadow.dataAllowlistKey,
		preset: shadow.preset,
		executionMode: shadow.executionMode,
		evidenceDelivery: shadow.evidenceDelivery,
		standing: shadow.standing,
		blocksClosure: shadow.blocksClosure,
		receivesPeerOutput: shadow.receivesPeerOutput,
	};
}

async function persistDataset(
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	envelopePath: string,
	envelopeSha256: string,
	status: string,
	outcome: Record<string, unknown>,
): Promise<void> {
	await atomicReplaceJson(shadow.datasetRecordPath, {
		schemaVersion: 1,
		reviewId: shadow.reviewId,
		subjectDigest: envelope.subjectDigest,
		receiptSha256: envelope.receiptSha256,
		envelopePath,
		envelopeSha256,
		selection: shadow.selection,
		reasonCodes: shadow.reasonCodes,
		status,
		updatedAt: now(),
		lane: laneMetadata(shadow),
		requestPath: shadow.requestPath,
		dispatchPath: shadow.dispatchPath,
		resultPath: shadow.resultPath,
		outcome,
	});
}

function getOracleApi(): OracleProgrammaticApi | undefined {
	const value = (globalThis as Record<symbol, unknown>)[Symbol.for(ORACLE_PROGRAMMATIC_API_KEY)];
	if (!isJsonObject(value) || value.version !== 1 || typeof value.preflight !== "function" || typeof value.submit !== "function") {
		return undefined;
	}
	return value as unknown as OracleProgrammaticApi;
}

function resultDetails(value: ToolResult): Record<string, unknown> {
	if (!isJsonObject(value) || !isJsonObject(value.details)) return {};
	return value.details;
}

async function runGit(repositoryPath: string, args: string[]): Promise<void> {
	const child = Bun.spawn({
		cmd: ["git", "-C", repositoryPath, ...args],
		stdout: "pipe",
		stderr: "pipe",
	});
	const [exitCode, stderr] = await Promise.all([
		child.exited,
		new Response(child.stderr).text(),
	]);
	if (exitCode !== 0) {
		throw new Error(`git ${args[0]} failed (${exitCode}): ${stderr.trim().slice(0, 500)}`);
	}
}

function contextAt(ctx: ExtensionContext, cwd: string): ExtensionContext {
	return new Proxy(ctx, {
		get(target, property, receiver) {
			if (property === "cwd") return cwd;
			const value = Reflect.get(target, property, receiver);
			return typeof value === "function" ? value.bind(target) : value;
		},
	});
}

async function withFrozenWorktree<T>(
	repositoryPath: string,
	subjectCommit: string,
	operation: (worktreePath: string) => Promise<T>,
): Promise<T> {
	const temporaryRoot = await mkdtemp(join(tmpdir(), "critical-review-oracle-"));
	const worktreePath = join(temporaryRoot, "subject");
	await runGit(repositoryPath, ["-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach", worktreePath, subjectCommit]);
	try {
		return await operation(worktreePath);
	} finally {
		await runGit(repositoryPath, ["worktree", "remove", "--force", worktreePath]).catch(() => undefined);
		await rm(temporaryRoot, { recursive: true, force: true });
	}
}

function strictReviewerOutput(raw: string): { parsed: Record<string, unknown> | null; error: string | null } {
	let value: unknown;
	try {
		value = JSON.parse(raw.trim());
	} catch (error) {
		return { parsed: null, error: `response is not strict JSON: ${error instanceof Error ? error.message : String(error)}` };
	}
	if (!isJsonObject(value) || Object.keys(value).sort().join(",") !== "evidence,summary,unresolved") {
		return { parsed: null, error: "response must contain exactly summary, evidence, and unresolved" };
	}
	if (!Array.isArray(value.evidence) || value.evidence.length > 12 || !Array.isArray(value.unresolved)) {
		return { parsed: null, error: "evidence and unresolved must be arrays and evidence may contain at most 12 items" };
	}
	return { parsed: value, error: null };
}

function parsePendingCollector(value: Record<string, unknown>): PendingCollector {
	if (value.schemaVersion !== 1) throw new Error("pending collector is not a version 1 object");
	const jobPathValue = value.jobPath;
	if (jobPathValue !== null && (typeof jobPathValue !== "string" || jobPathValue.length === 0)) {
		throw new Error("jobPath must be a non-empty string or null");
	}
	const createdAt = requireString(value, "createdAt");
	if (!Number.isFinite(Date.parse(createdAt))) throw new Error("createdAt must be a valid timestamp");
	return {
		schemaVersion: 1,
		envelopePath: requireString(value, "envelopePath"),
		envelopeSha256: requireString(value, "envelopeSha256"),
		subjectDigest: requireString(value, "subjectDigest"),
		requestSha256: requireString(value, "requestSha256"),
		dispatchPath: requireString(value, "dispatchPath"),
		resultPath: requireString(value, "resultPath"),
		datasetRecordPath: requireString(value, "datasetRecordPath"),
		jobPath: jobPathValue as string | null,
		createdAt,
	};
}

async function readBoundEnvelope(pointer: PendingCollector): Promise<{ envelope: DispatchEnvelope; shadow: OracleShadow }> {
	const rawEnvelope = await readFile(pointer.envelopePath, "utf8");
	if (sha256(rawEnvelope) !== pointer.envelopeSha256) {
		throw new Error("dispatch envelope digest changed while collecting Oracle shadow");
	}
	const parsed: unknown = JSON.parse(rawEnvelope);
	if (!isJsonObject(parsed)) throw new Error("dispatch envelope is not a JSON object");
	const envelope = parseEnvelope(parsed);
	if (envelope.subjectDigest !== pointer.subjectDigest) {
		throw new Error("pending collector subject does not match its envelope");
	}
	if (!envelope.oracleShadow) throw new Error("pending collector envelope has no Oracle shadow");
	return { envelope, shadow: envelope.oracleShadow };
}

async function persistCanonicalOutcome(
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	envelopePath: string,
	envelopeSha256: string,
	resultPath: string,
	candidate: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	const created = await writeJsonOnce(resultPath, candidate);
	const canonical = created ? candidate : await readJson(resultPath);
	const status = requireString(canonical, "status");
	await persistDataset(shadow, envelope, envelopePath, envelopeSha256, status, canonical);
	return canonical;
}

async function persistProgress(
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	envelopePath: string,
	envelopeSha256: string,
	status: string,
	outcome: Record<string, unknown>,
): Promise<void> {
	await persistDataset(shadow, envelope, envelopePath, envelopeSha256, status, outcome);
	if (!(await Bun.file(shadow.resultPath).exists())) return;
	const canonical = await readJson(shadow.resultPath);
	await persistDataset(
		shadow,
		envelope,
		envelopePath,
		envelopeSha256,
		requireString(canonical, "status"),
		canonical,
	);
}

async function persistCanonicalResult(
	pointer: PendingCollector,
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	candidate: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	return persistCanonicalOutcome(
		shadow,
		envelope,
		pointer.envelopePath,
		pointer.envelopeSha256,
		pointer.resultPath,
		candidate,
	);
}

async function finishCollector(pointerPath: string, pointer: PendingCollector): Promise<void> {
	if (!pointer.jobPath) throw new Error("cannot finish an Oracle shadow without durable job metadata");
	const job = await readJson(pointer.jobPath);
	const status = requireString(job, "status");
	const { envelope, shadow } = await readBoundEnvelope(pointer);

	let rawResponse: string | null = null;
	let parsedResponse: Record<string, unknown> | null = null;
	let validationError: string | null = null;
	if (status === "complete") {
		const responsePath = typeof job.responsePath === "string" ? job.responsePath : "";
		if (responsePath.length > 0) {
			rawResponse = await readFile(responsePath, "utf8").catch(() => null);
		}
		if (rawResponse === null) validationError = "complete Oracle job has no readable response";
		else ({ parsed: parsedResponse, error: validationError } = strictReviewerOutput(rawResponse));
	}
	const outcomeStatus = status === "complete" && validationError ? "schema_invalid" : status;
	const result = {
		schemaVersion: 1,
		completedAt: typeof job.completedAt === "string" ? job.completedAt : now(),
		status: outcomeStatus,
		oracleJobStatus: status,
		jobId: requireString(job, "id"),
		jobPath: pointer.jobPath,
		responsePath: typeof job.responsePath === "string" ? job.responsePath : null,
		responseSha256: rawResponse === null ? null : sha256(rawResponse),
		rawResponse,
		parsedResponse,
		validationError,
		error: typeof job.error === "string" ? job.error : null,
		requestedPreset: shadow.preset,
		servedModelEvidence: null,
		extensionProvenance: isJsonObject(job.extensionProvenance) ? job.extensionProvenance : null,
		requestSha256: pointer.requestSha256,
	};
	await persistCanonicalResult(pointer, shadow, envelope, result);
	await rm(pointerPath, { force: true });
}

export async function collectOracleShadow(pointerPath: string): Promise<void> {
	let pointer = parsePendingCollector(await readJson(pointerPath));
	const deadline = Date.parse(pointer.createdAt) + COLLECTOR_TIMEOUT_MS;
	while (Date.now() < deadline) {
		const latestValue = await readJson(pointerPath).catch(() => null);
		if (!latestValue) return;
		pointer = parsePendingCollector(latestValue);
		if (pointer.jobPath) {
			const job = await readJson(pointer.jobPath).catch(() => null);
			if (job && typeof job.status === "string" && TERMINAL_JOB_STATUSES[job.status]) {
				await finishCollector(pointerPath, pointer);
				return;
			}
		}
		await Bun.sleep(COLLECTOR_POLL_MS);
	}
	const latestValue = await readJson(pointerPath).catch(() => null);
	if (!latestValue) return;
	pointer = parsePendingCollector(latestValue);
	const { envelope, shadow } = await readBoundEnvelope(pointer);
	const result = {
		schemaVersion: 1,
		completedAt: now(),
		status: "collector_timeout",
		phase: pointer.jobPath ? "job" : "launch",
		jobPath: pointer.jobPath,
		requestSha256: pointer.requestSha256,
	};
	await persistCanonicalResult(pointer, shadow, envelope, result);
	await rm(pointerPath, { force: true });
}

function spawnCollector(pointerPath: string): void {
	if (spawnedCollectors.has(pointerPath)) return;
	spawnedCollectors.add(pointerPath);
	const scriptPath = fileURLToPath(import.meta.url);
	const processHandle = Bun.spawn({
		cmd: ["bun", scriptPath, "collect", pointerPath],
		stdin: "ignore",
		stdout: "ignore",
		stderr: "ignore",
		detached: true,
	});
	void processHandle.exited.finally(() => spawnedCollectors.delete(pointerPath));
	processHandle.unref();
}

export async function resumeOracleShadowCollectors(): Promise<void> {
	const directory = pendingDirectory();
	const names = await readdir(directory).catch(() => []);
	for (const name of names) {
		if (name.endsWith(".json")) spawnCollector(join(directory, name));
	}
}

async function recordNonselected(
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	envelopePath: string,
	envelopeSha256: string,
): Promise<void> {
	const result = {
		schemaVersion: 1,
		completedAt: now(),
		status: shadow.selection,
		reasonCodes: shadow.reasonCodes,
	};
	await persistCanonicalOutcome(shadow, envelope, envelopePath, envelopeSha256, shadow.resultPath, result);
}

async function recordLaunchFailure(
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	envelopePath: string,
	envelopeSha256: string,
	status: string,
	error: unknown,
): Promise<void> {
	const result = {
		schemaVersion: 1,
		completedAt: now(),
		status,
		error: error instanceof Error ? error.message : String(error),
	};
	await persistCanonicalOutcome(shadow, envelope, envelopePath, envelopeSha256, shadow.resultPath, result);
}

function validatePersistedRequest(
	value: Record<string, unknown>,
	shadow: OracleShadow,
	envelope: DispatchEnvelope,
	envelopePath: string,
	envelopeSha256: string,
): string {
	if (value.schemaVersion !== 1) throw new Error("Oracle shadow request is not a version 1 object");
	if (requireString(value, "envelopePath") !== envelopePath) throw new Error("Oracle shadow request envelope path changed");
	if (requireString(value, "envelopeSha256") !== envelopeSha256) throw new Error("Oracle shadow request envelope digest changed");
	if (requireString(value, "subjectDigest") !== envelope.subjectDigest) throw new Error("Oracle shadow request subject changed");
	if (requireString(value, "receiptSha256") !== envelope.receiptSha256) throw new Error("Oracle shadow request receipt changed");
	if (!isJsonObject(value.oracleShadow) || canonicalJson(value.oracleShadow) !== canonicalJson(laneMetadata(shadow))) {
		throw new Error("Oracle shadow request lane metadata changed");
	}
	if (!shadow.request || !isJsonObject(value.request) || canonicalJson(value.request) !== canonicalJson(shadow.request)) {
		throw new Error("Oracle shadow request payload changed");
	}
	const createdAt = requireString(value, "createdAt");
	if (!Number.isFinite(Date.parse(createdAt))) throw new Error("Oracle shadow request createdAt is invalid");
	return createdAt;
}

function assertPendingIdentity(
	pointer: PendingCollector,
	envelopePath: string,
	envelopeSha256: string,
	envelope: DispatchEnvelope,
	shadow: OracleShadow,
	requestSha256: string,
): void {
	if (
		pointer.envelopePath !== envelopePath ||
		pointer.envelopeSha256 !== envelopeSha256 ||
		pointer.subjectDigest !== envelope.subjectDigest ||
		pointer.requestSha256 !== requestSha256 ||
		pointer.dispatchPath !== shadow.dispatchPath ||
		pointer.resultPath !== shadow.resultPath ||
		pointer.datasetRecordPath !== shadow.datasetRecordPath
	) {
		throw new Error("pending Oracle collector does not match its immutable request");
	}
}

export async function launchOracleShadow(
	envelopePath: string,
	envelopeSha256: string,
	ctx: ExtensionContext,
	startCollector: (pointerPath: string) => void = spawnCollector,
): Promise<void> {
	let shadow: OracleShadow | null = null;
	let envelope: DispatchEnvelope | null = null;
	let pointerPath: string | null = null;
	let ownsLaunchPointer = false;
	let durableJobPointer = false;
	let submissionStarted = false;
	let requestSha256: string | null = null;
	try {
		const rawEnvelope = await readFile(envelopePath, "utf8");
		if (sha256(rawEnvelope) !== envelopeSha256) throw new Error("dispatch envelope digest changed after verification");
		const parsedEnvelope: unknown = JSON.parse(rawEnvelope);
		if (!isJsonObject(parsedEnvelope)) throw new Error("dispatch envelope is not a JSON object");
		envelope = parseEnvelope(parsedEnvelope);
		shadow = envelope.oracleShadow;
		if (!shadow) return;
		if (shadow.selection !== "selected") {
			await recordNonselected(shadow, envelope, envelopePath, envelopeSha256);
			return;
		}
		const request = shadow.request;
		if (!request) throw new Error("selected Oracle shadow has no request");
		const collectorKey = sha256(shadow.reviewId + "\0" + envelope.receiptSha256);
		pointerPath = join(pendingDirectory(), collectorKey + ".json");
		const requestDocument = {
			schemaVersion: 1,
			createdAt: now(),
			envelopePath,
			envelopeSha256,
			subjectDigest: envelope.subjectDigest,
			receiptSha256: envelope.receiptSha256,
			oracleShadow: laneMetadata(shadow),
			request,
		};
		const requestText = canonicalJson(requestDocument);
		const requestCreated = await writeJsonOnce(shadow.requestPath, requestDocument);
		let requestCreatedAt = requestDocument.createdAt;
		if (requestCreated) {
			requestSha256 = sha256(requestText);
		} else {
			const persistedRequestText = await readFile(shadow.requestPath, "utf8");
			const persistedRequest: unknown = JSON.parse(persistedRequestText);
			if (!isJsonObject(persistedRequest)) throw new Error("persisted Oracle shadow request is not a JSON object");
			requestCreatedAt = validatePersistedRequest(persistedRequest, shadow, envelope, envelopePath, envelopeSha256);
			requestSha256 = sha256(persistedRequestText);
		}
		if (await Bun.file(shadow.resultPath).exists()) {
			const canonical = await readJson(shadow.resultPath);
			await persistDataset(
				shadow,
				envelope,
				envelopePath,
				envelopeSha256,
				requireString(canonical, "status"),
				canonical,
			);
			await rm(pointerPath, { force: true });
			return;
		}

		const pointerBase = {
			schemaVersion: 1 as const,
			envelopePath,
			envelopeSha256,
			subjectDigest: envelope.subjectDigest,
			requestSha256,
			dispatchPath: shadow.dispatchPath,
			resultPath: shadow.resultPath,
			datasetRecordPath: shadow.datasetRecordPath,
			createdAt: requestCreatedAt,
		};

		if (await Bun.file(shadow.dispatchPath).exists()) {
			const dispatch = await readJson(shadow.dispatchPath);
			const persistedPointerPath = requireString(dispatch, "pendingPointerPath");
			if (persistedPointerPath !== pointerPath) throw new Error("Oracle shadow dispatch pointer path changed");
			if (requireString(dispatch, "requestSha256") !== requestSha256) throw new Error("Oracle shadow dispatch request digest changed");
			const jobPath = requireString(dispatch, "jobPath");
			const existingPointer = await Bun.file(pointerPath).exists()
				? parsePendingCollector(await readJson(pointerPath))
				: null;
			if (existingPointer) assertPendingIdentity(existingPointer, envelopePath, envelopeSha256, envelope, shadow, requestSha256);
			await atomicReplaceJson(pointerPath, {
				...pointerBase,
				jobPath,
				createdAt: existingPointer?.createdAt ?? requestCreatedAt,
			});
			startCollector(pointerPath);
			return;
		}

		if (!requestCreated) {
			if (await Bun.file(pointerPath).exists()) {
				const existingPointer = parsePendingCollector(await readJson(pointerPath));
				assertPendingIdentity(existingPointer, envelopePath, envelopeSha256, envelope, shadow, requestSha256);
				startCollector(pointerPath);
				return;
			}
			await atomicReplaceJson(pointerPath, { ...pointerBase, jobPath: null });
			startCollector(pointerPath);
			await persistProgress(shadow, envelope, envelopePath, envelopeSha256, "launch_outcome_unknown", {
				phase: "launch",
				requestSha256,
			});
			return;
		}

		await atomicReplaceJson(pointerPath, { ...pointerBase, jobPath: null });
		ownsLaunchPointer = true;
		startCollector(pointerPath);
		await persistProgress(shadow, envelope, envelopePath, envelopeSha256, "pending", { requestSha256 });

		const api = getOracleApi();
		if (!api) throw new Error("Pi Oracle programmatic API is not registered");
		const preflight = resultDetails(await api.preflight(ctx));
		if (preflight.ready !== true) {
			throw new Error("Pi Oracle preflight blocked: " + JSON.stringify(preflight.error ?? preflight));
		}
		const submission = await withFrozenWorktree(
			request.repositoryPath,
			request.subjectCommit,
			async (worktreePath) => {
				submissionStarted = true;
				return api.submit(contextAt(ctx, worktreePath), {
					provider: "chatgpt",
					preset: shadow.preset,
					prompt: request.prompt,
					files: request.files,
				});
			},
		);
		const submissionDetails = resultDetails(submission);
		if (isJsonObject(submissionDetails.error)) {
			throw new Error("Pi Oracle submission failed: " + String(submissionDetails.error.message ?? submissionDetails.error.code ?? "unknown error"));
		}
		const job = submissionDetails.job;
		if (!isJsonObject(job)) throw new Error("Pi Oracle submission returned no durable job metadata");
		const jobId = requireString(job, "id");
		const promptPath = requireString(job, "promptPath");
		const jobPath = join(dirname(promptPath), "job.json");
		await atomicReplaceJson(pointerPath, { ...pointerBase, jobPath });
		durableJobPointer = true;
		ownsLaunchPointer = false;
		startCollector(pointerPath);
		const dispatchDocument = {
			schemaVersion: 1,
			dispatchedAt: now(),
			envelope: {
				schemaVersion: envelope.schemaVersion,
				reviewClass: envelope.reviewClass,
				subjectDigest: envelope.subjectDigest,
				receiptSha256: envelope.receiptSha256,
				oracleShadow: shadow,
			},
			oracleShadow: shadow,
			requestPath: shadow.requestPath,
			requestSha256,
			jobId,
			jobPath,
			job,
			pendingPointerPath: pointerPath,
		};
		const dispatchCreated = await writeJsonOnce(shadow.dispatchPath, dispatchDocument);
		if (!dispatchCreated) {
			const persistedDispatch = await readJson(shadow.dispatchPath);
			if (
				requireString(persistedDispatch, "requestSha256") !== requestSha256 ||
				requireString(persistedDispatch, "jobPath") !== jobPath ||
				requireString(persistedDispatch, "pendingPointerPath") !== pointerPath
			) {
				throw new Error("persisted Oracle shadow dispatch conflicts with the submitted job");
			}
		}
		await persistProgress(shadow, envelope, envelopePath, envelopeSha256, "submitted", { jobId, jobPath, requestSha256 });
	} catch (error) {
		if (shadow && envelope) {
			if (durableJobPointer && pointerPath) {
				startCollector(pointerPath);
				await persistProgress(shadow, envelope, envelopePath, envelopeSha256, "submitted_recovery", {
					requestSha256,
					error: error instanceof Error ? error.message : String(error),
				}).catch(() => undefined);
			} else {
				if (ownsLaunchPointer && pointerPath) await rm(pointerPath, { force: true }).catch(() => undefined);
				const status = submissionStarted ? "launch_outcome_unknown" : "launch_failed";
				await recordLaunchFailure(shadow, envelope, envelopePath, envelopeSha256, status, error).catch(() => undefined);
			}
		}
	}
}

if (fileURLToPath(import.meta.url) === process.argv[1] && process.argv[2] === "collect" && process.argv[3]) {
	await collectOracleShadow(process.argv[3]);
}
