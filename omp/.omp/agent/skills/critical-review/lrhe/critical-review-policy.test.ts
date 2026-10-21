// Focused tests for the critical-review policy extension. They live in the skill package rather
// than beside the extension because OMP auto-discovers every `.ts` under an agent `extensions/`
// directory and would otherwise load this test module as an extension at session start.
import { afterAll, afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import criticalReviewPolicy, {
	DISPATCH_MARKER_HEADER,
	type DispatchMarker,
	type LaunchOracleShadow,
	deepEqual,
	evaluateTaskDispatch,
	parseDispatchMarker,
	parseVerifierOutput,
	RESOLVER_RECEIPT_HEADER,
	VERIFIER_PYTHON,
	VERIFIER_SCRIPT,
	type VerifyTask,
} from "../../../extensions/critical-review-policy.ts";

const READ_ONLY_PROMPT = [
	"preamble",
	"seat contract CRITICAL_REVIEWER_READ_ONLY_V1 applies",
];
const FABLE_READ_ONLY_PROMPT = [
	"preamble",
	"seat contract FABLE_ARCHITECTURE_REVIEWER_READ_ONLY_V2 applies",
];
const INLINE_ISOLATED_PROMPT = [
	"preamble",
	"CRITICAL_REVIEWER_INLINE_ISOLATED_V1 seat",
];
const LEAD_PROMPT = ["you are the lead"];

const DEFAULT_DIRECTORY = mkdtempSync(join(tmpdir(), "critical-review-default-"));
const ENVELOPE_PATH = join(DEFAULT_DIRECTORY, "envelope-2f9c.json");
const ENVELOPE_SHA256 =
	"3f2a1b0c9d8e7f60514233445566778899aabbccddeeff00112233445566778a";
const MARKER = `${DISPATCH_MARKER_HEADER}\nenvelope_path=${ENVELOPE_PATH}\nenvelope_sha256=${ENVELOPE_SHA256}`;
const INTENT = "Dispatching resolved reviewers";

function receipt(seat: string): string {
	return `${RESOLVER_RECEIPT_HEADER}\nseat=${seat}\nreview_class=initial\nrole=initial-critic\n\nRender a verdict.`;
}

const CANONICAL_INPUT = {
	context: MARKER,
	i: INTENT,
	tasks: [
		{ agent: "review-claude-opus", task: receipt("review-claude-opus") },
		{ agent: "review-gemini-argon", task: receipt("review-gemini-argon") },
	],
};

const VERIFIER_STDOUT = JSON.stringify({ task_input: CANONICAL_INPUT });

const SINGLE_INPUT = {
	context: MARKER,
	i: INTENT,
	tasks: [
		{ agent: "review-glm-floor", task: receipt("review-glm-floor") },
	],
};
const SINGLE_VERIFIER_STDOUT = JSON.stringify({ task_input: SINGLE_INPUT });

/** Records what the gate asked for, so a test can prove the envelope came from the parsed marker. */
function approvingVerifier(
	stdout = VERIFIER_STDOUT,
): VerifyTask & { calls: DispatchMarker[] } {
	const calls: DispatchMarker[] = [];
	const verify = async (marker: DispatchMarker) => {
		calls.push(marker);
		return stdout;
	};
	return Object.assign(verify, { calls });
}

function rejectingVerifier(
	message: string,
): VerifyTask & { calls: DispatchMarker[] } {
	const calls: DispatchMarker[] = [];
	const verify = async (marker: DispatchMarker): Promise<string> => {
		calls.push(marker);
		throw new Error(message);
	};
	return Object.assign(verify, { calls });
}

type ToolCall = {
	(toolName: string, input?: unknown, toolCallId?: string): Promise<unknown>;
	update(toolCallId: string, progress: unknown[]): Promise<unknown>;
	result(
		toolName: string,
		toolCallId: string,
		isError: boolean,
		results?: unknown[],
	): Promise<unknown>;
};

function gate(
	promptParts: string[],
	verify: VerifyTask = approvingVerifier(),
	launchShadow: LaunchOracleShadow = async () => undefined,
): ToolCall {
	let toolCallHandler: ((event: unknown, ctx: unknown) => unknown) | undefined;
	let toolResultHandler: ((event: unknown, ctx: unknown) => unknown) | undefined;
	let toolUpdateHandler: ((event: unknown, ctx: unknown) => unknown) | undefined;
	const pi = {
		on: (
			event: string,
			registered: (event: unknown, ctx: unknown) => unknown,
		) => {
			if (event === "tool_call") toolCallHandler = registered;
			if (event === "tool_result") toolResultHandler = registered;
			if (event === "tool_execution_update") toolUpdateHandler = registered;
		},
	};
	criticalReviewPolicy(pi as unknown as ExtensionAPI, verify, launchShadow);
	const ctx = { getSystemPrompt: () => promptParts };
	let callCount = 0;
	const call = (async (toolName, input, toolCallId) => {
		callCount += 1;
		return toolCallHandler?.(
			{ input, toolCallId: toolCallId ?? [toolName, callCount].join("-"), toolName },
			ctx,
		);
	}) as ToolCall;
	call.update = async (toolCallId, progress) =>
		toolUpdateHandler?.({ toolName: "task", toolCallId, partialResult: { details: { progress } } }, ctx);
	call.result = async (toolName, toolCallId, isError, results = []) =>
		toolResultHandler?.(
			{ content: [], details: { results }, isError, toolCallId, toolName },
			ctx,
		);
	return call;
}

const envelopeDirectories: string[] = [];
afterEach(async () => {
	await rm(DEFAULT_DIRECTORY, { recursive: true, force: true });
	await Promise.all(
		envelopeDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })),
	);
});
afterAll(async () => { await rm(DEFAULT_DIRECTORY, { recursive: true, force: true }); });

/** A canonical batch whose envelope sits in a fresh directory, so one test's attempt records never reach another. */
async function isolatedEnvelope() {
	const directory = await mkdtemp(join(tmpdir(), "critical-review-gate-"));
	envelopeDirectories.push(directory);
	const envelopePath = join(directory, "review-dispatch-envelope.json");
	const input = {
		...structuredClone(CANONICAL_INPUT),
		context: `${DISPATCH_MARKER_HEADER}\nenvelope_path=${envelopePath}\nenvelope_sha256=${ENVELOPE_SHA256}`,
	};
	return {
		directory,
		input,
		verify: approvingVerifier(JSON.stringify({ task_input: input })),
	};
}

function reasonOf(decision: unknown): string {
	expect(decision).toMatchObject({ block: true });
	return (decision as { reason: string }).reason;
}

describe("reviewer child boundary", () => {
	test("permits only read-only tools inside a reviewer seat", async () => {
		const call = gate(READ_ONLY_PROMPT);

		for (const toolName of [
			"read",
			"grep",
			"glob",
			"lsp",
			"ast_grep",
			"yield",
		]) {
			expect(await call(toolName, {})).toBeUndefined();
		}
		for (const toolName of ["edit", "write", "bash", "eval", "hub", "task"]) {
			expect(await call(toolName, {})).toEqual({
				block: true,
				reason: `Critical reviewers are read-only and isolated; ${toolName} is not permitted.`,
			});
		}
	});

	test("applies the same host clamp to the Fable reviewer marker", async () => {
		const call = gate(FABLE_READ_ONLY_PROMPT);

		expect(await call("read", {})).toBeUndefined();
		expect(await call("hub", {})).toEqual({
			block: true,
			reason:
				"Critical reviewers are read-only and isolated; hub is not permitted.",
		});
	});

	test("narrows an inline isolated reviewer to yield alone", async () => {
		const call = gate(INLINE_ISOLATED_PROMPT);

		expect(await call("yield", {})).toBeUndefined();
		expect(await call("read", {})).toEqual({
			block: true,
			reason:
				"Critical reviewers are read-only and isolated; read is not permitted.",
		});
	});

	test("stops a reviewer seat before the dispatch path can run", async () => {
		const verify = approvingVerifier();
		const call = gate(READ_ONLY_PROMPT, verify);

		expect(await call("task", structuredClone(CANONICAL_INPUT))).toEqual({
			block: true,
			reason:
				"Critical reviewers are read-only and isolated; task is not permitted.",
		});
		expect(verify.calls).toEqual([]);
	});
});

describe("unprotected task calls", () => {
	test("passes batches with no reviewer item through untouched", async () => {
		const verify = approvingVerifier();
		const call = gate(LEAD_PROMPT, verify);

		expect(
			await call("task", {
				context: "shared background",
				i: "Mapping the dispatch flow",
				tasks: [
					{ agent: "scout", task: "map the dispatch flow" },
					{ task: "write the notes" },
				],
			}),
		).toBeUndefined();
		expect(
			await call("task", { agent: "scout", i: "Mapping", task: "map it" }),
		).toBeUndefined();
		expect(
			await call("task", { i: "Working", task: "no agent at all" }),
		).toBeUndefined();
		expect(verify.calls).toEqual([]);
	});

	test("leaves every non-task tool alone in a lead session", async () => {
		const call = gate(LEAD_PROMPT);

		expect(
			await call("bash", { command: "git status", i: "Checking status" }),
		).toBeUndefined();
		expect(await call("edit", { i: "Editing", input: "" })).toBeUndefined();
	});

	test("ignores a reviewer name that is not the agent field", async () => {
		const verify = approvingVerifier();
		const call = gate(LEAD_PROMPT, verify);

		expect(
			await call("task", {
				context: "shared background",
				i: "Spawning a scout",
				tasks: [
					{
						agent: "scout",
						name: "review-claude-opus",
						task: "not a reviewer",
					},
				],
			}),
		).toBeUndefined();
		expect(verify.calls).toEqual([]);
	});
});

describe("protected task calls", () => {
	test("launches one Oracle shadow only after a protected dispatch verifies", async () => {
		const launches: Array<{ envelopePath: string; envelopeSha256: string }> = [];
		const launchShadow: LaunchOracleShadow = async (
			envelopePath,
			envelopeSha256,
		) => {
			launches.push({ envelopePath, envelopeSha256 });
		};
		const call = gate(LEAD_PROMPT, approvingVerifier(), launchShadow);

		expect(
			await call("task", structuredClone(CANONICAL_INPUT), "verified-council"),
		).toEqual({ input: CANONICAL_INPUT });
		expect(launches).toEqual([
			{ envelopePath: ENVELOPE_PATH, envelopeSha256: ENVELOPE_SHA256 },
		]);

		const rejected = structuredClone(CANONICAL_INPUT);
		rejected.context = "unverified council";
		expect(await call("task", rejected, "unverified-council")).toMatchObject({
			block: true,
		});
		expect(launches).toHaveLength(1);
	});
	test("blocks a reviewer batch whose context carries no marker", async () => {
		const verify = approvingVerifier();
		const decision = await evaluateTaskDispatch(
			{
				...structuredClone(CANONICAL_INPUT),
				context: "Please review the diff carefully.",
			},
			verify,
		);

		expect(reasonOf(decision)).toContain(DISPATCH_MARKER_HEADER);
		expect(verify.calls).toEqual([]);
	});

	test("blocks every near-miss of the marker", async () => {
		const verify = approvingVerifier();
		const malformed = [
			`${MARKER}\n`,
			` ${MARKER}`,
			`${MARKER}\nextra=1`,
			`Dispatching now:\n${MARKER}`,
			`${DISPATCH_MARKER_HEADER}\nenvelope_path=${ENVELOPE_PATH}\nenvelope_sha256=${ENVELOPE_SHA256.toUpperCase()}`,
			`${DISPATCH_MARKER_HEADER}\nenvelope_path=relative/envelope.json\nenvelope_sha256=${ENVELOPE_SHA256}`,
			`${DISPATCH_MARKER_HEADER}\nenvelope_path=${ENVELOPE_PATH}\nenvelope_sha256=${ENVELOPE_SHA256.slice(1)}`,
			`${DISPATCH_MARKER_HEADER}\nenvelope_sha256=${ENVELOPE_SHA256}\nenvelope_path=${ENVELOPE_PATH}`,
			`CRITICAL_REVIEW_DISPATCH_V2\nenvelope_path=${ENVELOPE_PATH}\nenvelope_sha256=${ENVELOPE_SHA256}`,
		];

		for (const context of malformed) {
			expect(parseDispatchMarker(context)).toBeNull();
			const decision = await evaluateTaskDispatch(
				{ ...structuredClone(CANONICAL_INPUT), context },
				verify,
			);
			expect(decision).toMatchObject({ block: true });
		}
		expect(verify.calls).toEqual([]);
	});

	test("blocks a batch that mixes reviewer and non-reviewer items", async () => {
		const verify = approvingVerifier();
		const mixed = structuredClone(CANONICAL_INPUT);
		const decision = await evaluateTaskDispatch(
			{
				...mixed,
				tasks: [...mixed.tasks, { agent: "scout", task: "sneak in a worker" }],
			},
			verify,
		);

		expect(reasonOf(decision)).toContain("only reviewer items");
		expect(verify.calls).toEqual([]);
	});

	test("accepts the verifier-generated one-item reviewer batch", async () => {
		const verify = approvingVerifier(SINGLE_VERIFIER_STDOUT);
		const decision = await evaluateTaskDispatch(
			structuredClone(SINGLE_INPUT),
			verify,
		);

		expect(decision).toEqual({ input: SINGLE_INPUT, seats: [0], canonical: SINGLE_INPUT });
		expect(verify.calls).toEqual([
			{ envelopePath: ENVELOPE_PATH, envelopeSha256: ENVELOPE_SHA256 },
		]);
	});

	test("blocks a legacy flat reviewer spawn", async () => {
		const verify = approvingVerifier(SINGLE_VERIFIER_STDOUT);
		const decision = await evaluateTaskDispatch(
			{
				agent: "review-glm-floor",
				i: INTENT,
				task: receipt("review-glm-floor"),
			},
			verify,
		);

		expect(reasonOf(decision)).toContain("batch Task shape");
		expect(verify.calls).toEqual([]);
	});

	test("blocks when the verifier refuses, times out, or cannot run", async () => {
		const refusal = rejectingVerifier(
			"verifier exited with code 1: resolver receipt is stale",
		);
		expect(
			reasonOf(
				await evaluateTaskDispatch(structuredClone(CANONICAL_INPUT), refusal),
			),
		).toContain("resolver receipt is stale");
		expect(refusal.calls).toEqual([
			{ envelopePath: ENVELOPE_PATH, envelopeSha256: ENVELOPE_SHA256 },
		]);

		const timeout = rejectingVerifier("verifier exceeded its 30000ms budget");
		expect(
			reasonOf(
				await evaluateTaskDispatch(structuredClone(CANONICAL_INPUT), timeout),
			),
		).toContain("30000ms budget");

		const nonError: VerifyTask = async () => {
			throw "not an Error";
		};
		expect(
			await evaluateTaskDispatch(structuredClone(CANONICAL_INPUT), nonError),
		).toMatchObject({
			block: true,
		});
	});

	test("blocks when the verifier prints anything but a strict task_input", async () => {
		const item = CANONICAL_INPUT.tasks[0];
		const { context, i, tasks } = CANONICAL_INPUT;
		const rejected = [
			"",
			"ok",
			"null",
			"[]",
			JSON.stringify(CANONICAL_INPUT),
			JSON.stringify({ task_input: CANONICAL_INPUT, warnings: [] }),
			JSON.stringify({
				task_input: {
					agent: "review-glm-floor",
					i,
					task: receipt("review-glm-floor"),
				},
			}),
			JSON.stringify({ task_input: { context, tasks } }),
			JSON.stringify({ task_input: { context, i, tasks: [] } }),
			JSON.stringify({ task_input: { context, i: "  ", tasks } }),
			JSON.stringify({ task_input: { context, i, tasks, effort: "hi" } }),
			JSON.stringify({ task_input: { context: "free text", i, tasks } }),
			JSON.stringify({
				task_input: { context, i, tasks: [{ agent: "scout", task: "x" }] },
			}),
			JSON.stringify({
				task_input: {
					context,
					i,
					tasks: [{ agent: "review-gemini-argon", task: "no receipt" }],
				},
			}),
			JSON.stringify({
				task_input: { context, i, tasks: [{ ...item, name: "extra field" }] },
			}),
			`${VERIFIER_STDOUT}${VERIFIER_STDOUT}`,
		];

		for (const stdout of rejected) {
			expect(parseVerifierOutput(stdout)).toBeNull();
			const decision = await evaluateTaskDispatch(
				structuredClone(CANONICAL_INPUT),
				approvingVerifier(stdout),
			);
			expect(decision).toMatchObject({ block: true });
		}
		expect(parseVerifierOutput(SINGLE_VERIFIER_STDOUT)).toEqual(SINGLE_INPUT);
	});

	test("blocks a call that deviates from the canonical dispatch", async () => {
		const deviations = [
			{
				...structuredClone(CANONICAL_INPUT),
				tasks: [CANONICAL_INPUT.tasks[1], CANONICAL_INPUT.tasks[0]],
			},
			{
				...structuredClone(CANONICAL_INPUT),
				tasks: [
					{ agent: "review-grok", task: receipt("review-claude-opus") },
					CANONICAL_INPUT.tasks[1],
				],
			},
			{
				...structuredClone(CANONICAL_INPUT),
				tasks: CANONICAL_INPUT.tasks.map((item) => ({
					...item,
					name: item.agent,
				})),
			},
			{ ...structuredClone(CANONICAL_INPUT), i: "Dispatching reviewers" },
			{ ...structuredClone(CANONICAL_INPUT), effort: "hi" },
		];

		for (const input of deviations) {
			const verify = approvingVerifier();
			expect(reasonOf(await evaluateTaskDispatch(input, verify))).toContain(
				"canonical dispatch",
			);
			expect(verify.calls).toHaveLength(1);
		}
	});

	test("replaces an approved dispatch with the canonical input", async () => {
		const verify = approvingVerifier();
		const call = gate(LEAD_PROMPT, verify);

		// Key order differs from canonical: the contract is deep equality, not a byte comparison.
		const decision = await call("task", {
			tasks: CANONICAL_INPUT.tasks.map((item) => ({
				task: item.task,
				agent: item.agent,
			})),
			i: INTENT,
			context: MARKER,
		});

		expect(decision).toEqual({ input: CANONICAL_INPUT });
		expect(Object.keys((decision as { input: object }).input)).toEqual([
			"context",
			"i",
			"tasks",
		]);
		expect(verify.calls).toEqual([
			{ envelopePath: ENVELOPE_PATH, envelopeSha256: ENVELOPE_SHA256 },
		]);
	});

	test("admits the canonical dispatch after the harness strips its intent field", async () => {
		// omp 18.5.1 removes `i` from tool-call arguments before any tool_call hook runs.
		const launches: string[] = [];
		const call = gate(LEAD_PROMPT, approvingVerifier(), async (envelopePath) => {
			launches.push(envelopePath);
		});
		const { i: _intent, ...delivered } = structuredClone(CANONICAL_INPUT);

		expect(await call("task", delivered)).toEqual({ input: delivered });
		expect(launches).toEqual([ENVELOPE_PATH]);
	});

	test("blocks an intent-stripped call that deviates from the canonical dispatch", async () => {
		const { i: _intent, ...delivered } = structuredClone(CANONICAL_INPUT);
		const reordered = { ...delivered, tasks: [delivered.tasks[1], delivered.tasks[0]] };
		const verify = approvingVerifier();

		expect(reasonOf(await evaluateTaskDispatch(reordered, verify))).toContain(
			"canonical dispatch",
		);
		expect(verify.calls).toHaveLength(1);
	});

	test("blocks a redispatch while the envelope's call is unresolved", async () => {
		const call = gate(LEAD_PROMPT);

		expect(
			await call("task", structuredClone(CANONICAL_INPUT), "review-call-1"),
		).toEqual({ input: CANONICAL_INPUT });
		const unresolvedReason = reasonOf(
			await call("task", structuredClone(CANONICAL_INPUT), "review-call-2"),
		);
		expect(unresolvedReason).toContain("unresolved Task call");
		expect(unresolvedReason).toContain("background acknowledgement");
	});

	test("admits only the complete batch as an envelope's first dispatch", async () => {
		const call = gate(LEAD_PROMPT);
		const subset = { ...structuredClone(CANONICAL_INPUT), tasks: [CANONICAL_INPUT.tasks[1]] };

		expect(reasonOf(await call("task", subset, "review-call-1"))).toContain(
			"complete resolver batch",
		);
	});

	test("never reruns a completed seat, even through a byte-identical envelope copy", async () => {
		const { input } = await isolatedEnvelope();
		const verify: VerifyTask = async (marker) =>
			JSON.stringify({
				task_input: {
					...structuredClone(input),
					context: `${DISPATCH_MARKER_HEADER}\nenvelope_path=${marker.envelopePath}\nenvelope_sha256=${marker.envelopeSha256}`,
				},
			});
		const call = gate(LEAD_PROMPT, verify);

		expect(await call("task", structuredClone(input), "review-call-1")).toEqual({ input });
		await call.result("task", "review-call-1", false, [
			{ index: 0, exitCode: 0 },
			{ index: 1, exitCode: 0 },
		]);

		const copied = {
			...structuredClone(input),
			context: `${DISPATCH_MARKER_HEADER}\nenvelope_path=/tmp/copied-envelope.json\nenvelope_sha256=${ENVELOPE_SHA256}`,
		};
		for (const [redispatch, id] of [
			[structuredClone(input), "review-call-2"],
			[copied, "review-call-3"],
		] as const) {
			expect(reasonOf(await call("task", redispatch, id))).toContain(
				"only a failed seat may run again",
			);
		}
	});

	test("retries a seat that exited non-zero without a top-level error, alone and once", async () => {
		// omp's blocking fan-out sets the call's error flag only for a thrown spawn (2026-10-19: an
		// Argon 404 settled as exit 1 under a successful call, and its retry was refused).
		const { input, verify } = await isolatedEnvelope();
		const call = gate(LEAD_PROMPT, verify);
		const argon = { ...structuredClone(input), tasks: [input.tasks[1]] };

		expect(await call("task", structuredClone(input), "review-call-1")).toEqual({ input });
		await call.result("task", "review-call-1", false, [
			{ index: 0, exitCode: 0 },
			{ index: 1, exitCode: 1, error: "Cloud Code Assist API error (404)" },
		]);

		expect(reasonOf(await call("task", structuredClone(input), "review-call-2"))).toContain(
			"so review-claude-opus cannot",
		);
		expect(await call("task", structuredClone(argon), "review-call-3")).toEqual({ input: argon });
		// The retry's only row sits at position 0 of its own call, not at its canonical seat.
		await call.result("task", "review-call-3", false, [{ index: 0, exitCode: 1 }]);
		expect(reasonOf(await call("task", structuredClone(argon), "review-call-4"))).toContain(
			"so review-gemini-argon cannot",
		);
	});

	test("treats an aborted or missing seat as failed whatever the call's error flag", async () => {
		const { input, verify } = await isolatedEnvelope();
		const call = gate(LEAD_PROMPT, verify);

		await call("task", structuredClone(input), "review-call-1");
		await call.result("task", "review-call-1", true, [{ index: 0, exitCode: 0, aborted: true }]);

		expect(await call("task", structuredClone(input), "review-call-2")).toEqual({ input });
	});

	test("records each settled call so a restarted process retries only the failed seat", async () => {
		const { directory, input, verify } = await isolatedEnvelope();
		const first = gate(LEAD_PROMPT, verify);

		await first("task", structuredClone(input), "review-call-1");
		const settled = await first.result("task", "review-call-1", false, [
			{ index: 0, exitCode: 0 },
			{ index: 1, exitCode: 1 },
		]);
		const recordPath = join(directory, "review-dispatch-envelope.attempt-1.json");
		const record = JSON.parse(await readFile(recordPath, "utf8"));
		expect(record.retryTaskInput).toEqual({ ...input, tasks: [input.tasks[1]] });
		expect(record.result.details.results).toHaveLength(2);
		expect(JSON.stringify(settled)).toContain(recordPath);

		const restarted = gate(LEAD_PROMPT, verify);
		expect(reasonOf(await restarted("task", structuredClone(input), "review-call-2"))).toContain(
			"only a failed seat may run again",
		);
		expect(await restarted("task", record.retryTaskInput, "review-call-3")).toEqual({
			input: record.retryTaskInput,
		});
	});

	test("a native error with zero exit is failed, never a completed critic", async () => {
		const { input, verify } = await isolatedEnvelope();
		const call = gate(LEAD_PROMPT, verify);
		await call("task", input, "first");
		await call.result("task", "first", false, [
			{ index: 0, exitCode: 0, error: "transport failed" }, { index: 1, exitCode: 0 },
		]);
		const retry = { ...input, tasks: [input.tasks[0]] };
		expect(await call("task", retry, "retry")).toEqual({ input: retry });
	});

	test("refuses an envelope whose attempt record is unreadable", async () => {
		const { directory, input, verify } = await isolatedEnvelope();
		await writeFile(join(directory, "review-dispatch-envelope.attempt-1.json"), "{not JSON");

		expect(
			reasonOf(await gate(LEAD_PROMPT, verify)("task", structuredClone(input), "review-call-1")),
		).toContain("attempt history is unreadable");
	});

	test("parks an unknown admitted call across restart instead of replaying its critics", async () => {
		const { input, verify } = await isolatedEnvelope();
		expect(await gate(LEAD_PROMPT, verify)("task", input, "original")).toEqual({ input });
		const restarted = gate(LEAD_PROMPT, verify);
		expect(reasonOf(await restarted("task", input, "duplicate"))).toContain("outcome unknown");
	});

	test("records progress observation separately from unavailable native phase timestamps", async () => {
		const { directory, input, verify } = await isolatedEnvelope();
		const call = gate(LEAD_PROMPT, verify);
		await call("task", input, "observed");
		await call.update("observed", [{ index: 0, status: "running", durationMs: 20 }]);
		await call.update("observed", [{ index: 0, status: "completed", durationMs: 80 }]);
		// Progress does not settle the call or admit a second wave, even when a seat finished.
		expect(reasonOf(await call("task", input, "premature"))).toContain("unresolved Task call");
		await call.result("task", "observed", false, [
			{ index: 0, exitCode: 0, durationMs: 80 },
			{ index: 1, exitCode: 1, durationMs: 100 },
		]);
		const record = JSON.parse(await readFile(join(directory, "review-dispatch-envelope.attempt-1.json"), "utf8"));
		expect(record.seats[0]).toMatchObject({
			outcome: "completed",
			inputBytes: Buffer.byteLength(String(input.tasks[0].task)),
			timing: {
				firstProgressAt: expect.any(String),
				terminalProgressAt: expect.any(String),
				resultObservedAt: record.settledAt,
				elapsedMs: 80,
				queueWaitMs: null, startedAt: null, endedAt: null, firstTokenAt: null,
			},
		});
		expect(record.seats[1]).toMatchObject({
			outcome: "error",
			timing: { firstProgressAt: null, terminalProgressAt: null, elapsedMs: 100 },
		});
		expect(record.retryTaskInput.tasks).toEqual([input.tasks[1]]);
	});

	test("keeps a required-seat failure parked after its only retry", async () => {
		const { directory, input, verify } = await isolatedEnvelope();
		const call = gate(LEAD_PROMPT, verify);
		await call("task", input, "first");
		await call.result("task", "first", false, [
			{ index: 0, exitCode: 1 }, { index: 1, exitCode: 0 },
		]);
		const retry = { ...input, tasks: [input.tasks[0]] };
		await call("task", retry, "retry");
		await call.result("task", "retry", false, [{ index: 0, exitCode: 1 }]);
		const record = JSON.parse(await readFile(join(directory, "review-dispatch-envelope.attempt-2.json"), "utf8"));
		expect(record.seats[0]).toMatchObject({ failed: true, outcome: "error" });
		expect(record.retryTaskInput).toBeNull();
		expect(reasonOf(await gate(LEAD_PROMPT, verify)("task", retry, "third"))).toContain("only a failed seat");
	});

	test("settles required seats before supplemental and preserves both budgets across restart", async () => {
		const { directory, input } = await isolatedEnvelope();
		input.tasks = [
			...input.tasks.map((task) => ({
				...task, task: `${RESOLVER_RECEIPT_HEADER}\nreview_class=focused\nauthority=independent_evidence\n\nblind input`,
			})),
			{ agent: "review-grok", task: `${RESOLVER_RECEIPT_HEADER}\nreview_class=focused\nauthority=supplemental_evidence\n\nblind input` },
		];
		const verify = approvingVerifier(JSON.stringify({ task_input: input }));
		const supplemental = { ...input, tasks: input.tasks.slice(2) };
		const required = { ...input, tasks: input.tasks.slice(0, 2) };
		const call = gate(LEAD_PROMPT, verify);
		expect(reasonOf(await call("task", required, "out-of-order"))).toContain("supplemental");
		expect(await call("task", supplemental, "eval-grok")).toEqual({ input: supplemental });
		expect(await call("task", required, "direct-required")).toEqual({ input: required });
		expect(reasonOf(await call("task", required, "duplicate-required"))).toContain("unresolved");
		const verdict = { status: "valid", data: { summary: "done", evidence: [], unresolved: [] } };
		await call.result("task", "direct-required", false, [
			{ index: 0, exitCode: 0, structuredOutput: verdict },
			{ index: 1, exitCode: 0, structuredOutput: verdict },
		]);
		const requiredRecord = JSON.parse(await readFile(join(directory, "review-dispatch-envelope.attempt-2.json"), "utf8"));
		expect(requiredRecord.seats.map((seat: { outcome: string }) => seat.outcome)).toEqual(["completed", "completed"]);
		const restarted = gate(LEAD_PROMPT, verify);
		expect(reasonOf(await restarted("task", required, "replay-required"))).toContain("only a failed seat");
		expect(reasonOf(await restarted("task", supplemental, "replay-grok"))).toContain("outcome unknown");
		// Simulate release cutoff while the original supplemental call is still in flight.
		await writeFile(join(directory, "review-dispatch-envelope.cutoff.json"), JSON.stringify({ envelopeSha256: ENVELOPE_SHA256 }));
		await call.result("task", "eval-grok", false, [{
			index: 0, exitCode: 0, structuredOutput: {
				status: "valid",
				data: { summary: "late", evidence: ["R1|P1|claim=late defect"], unresolved: [] },
			},
		}]);
		const latePath = join(directory, "review-dispatch-envelope.attempt-1.late.json");
		const late = JSON.parse(await readFile(latePath, "utf8"));
		expect(late).toMatchObject({
			status: "reopened-defect", automaticProductionEffect: false,
			escalation: "urgent-lead-verification-and-risk-based-escalation",
		});
		expect(reasonOf(await gate(LEAD_PROMPT, verify)("task", supplemental, "third-grok"))).toContain("only a failed seat");
	});
});

describe("fixed verifier location", () => {
	test("resolves the script and interpreter as siblings of the extension", () => {
		const agentDir = import.meta.dir.replace(
			/\/skills\/critical-review\/lrhe$/u,
			"",
		);

		expect(agentDir).not.toBe(import.meta.dir);
		expect(VERIFIER_SCRIPT).toBe(
			`${agentDir}/skills/critical-review/lrhe/review_dispatch.py`,
		);
		expect(VERIFIER_PYTHON).toBe(
			`${agentDir}/skills/critical-review/lrhe/.venv/bin/python`,
		);
	});
});

describe("structural comparison", () => {
	test("ignores key order and never tolerates an extra or missing key", () => {
		expect(
			deepEqual({ a: 1, b: [2, { c: 3 }] }, { b: [2, { c: 3 }], a: 1 }),
		).toBe(true);
		expect(deepEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false);
		expect(deepEqual({ a: 1, b: 2 }, { a: 1 })).toBe(false);
		expect(deepEqual([1, 2], [2, 1])).toBe(false);
		expect(deepEqual([1, 2], [1, 2, 3])).toBe(false);
		expect(deepEqual({ a: "1" }, { a: 1 })).toBe(false);
		expect(deepEqual({ a: { b: null } }, { a: { b: null } })).toBe(true);
	});
});
