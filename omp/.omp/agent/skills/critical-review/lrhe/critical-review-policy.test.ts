// Focused tests for the critical-review policy extension. They live in the skill package rather
// than beside the extension because OMP auto-discovers every `.ts` under an agent `extensions/`
// directory and would otherwise load this test module as an extension at session start.
import { describe, expect, test } from "bun:test";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import criticalReviewPolicy, {
	DISPATCH_MARKER_HEADER,
	type DispatchMarker,
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
const INLINE_ISOLATED_PROMPT = [
	"preamble",
	"CRITICAL_REVIEWER_INLINE_ISOLATED_V1 seat",
];
const LEAD_PROMPT = ["you are the lead"];

const ENVELOPE_PATH =
	"/Users/lead/.omp/critical-review/dispatch/envelope-2f9c.json";
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
		{ agent: "review-gemini", task: receipt("review-gemini") },
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
	result(toolName: string, toolCallId: string, isError: boolean): Promise<unknown>;
};

function gate(
	promptParts: string[],
	verify: VerifyTask = approvingVerifier(),
): ToolCall {
	let toolCallHandler: ((event: unknown, ctx: unknown) => unknown) | undefined;
	let toolResultHandler: ((event: unknown, ctx: unknown) => unknown) | undefined;
	const pi = {
		on: (
			event: string,
			registered: (event: unknown, ctx: unknown) => unknown,
		) => {
			if (event === "tool_call") toolCallHandler = registered;
			if (event === "tool_result") toolResultHandler = registered;
		},
	};
	criticalReviewPolicy(pi as unknown as ExtensionAPI, verify);
	const ctx = { getSystemPrompt: () => promptParts };
	let callCount = 0;
	const call = (async (toolName, input, toolCallId) => {
		callCount += 1;
		return toolCallHandler?.(
			{ input, toolCallId: toolCallId ?? [toolName, callCount].join("-"), toolName },
			ctx,
		);
	}) as ToolCall;
	call.result = async (toolName, toolCallId, isError) =>
		toolResultHandler?.({ isError, toolCallId, toolName }, ctx);
	return call;
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

		expect(decision).toEqual({ input: SINGLE_INPUT });
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
					tasks: [{ agent: "review-gemini", task: "no receipt" }],
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

	test("blocks unresolved and completed envelope redispatches", async () => {
		const call = gate(LEAD_PROMPT);

		expect(
			await call("task", structuredClone(CANONICAL_INPUT), "review-call-1"),
		).toEqual({ input: CANONICAL_INPUT });
		const unresolved = await call(
			"task",
			structuredClone(CANONICAL_INPUT),
			"review-call-2",
		);
		const unresolvedReason = reasonOf(unresolved);
		expect(unresolvedReason).toContain("unresolved Task call");
		expect(unresolvedReason).toContain("background acknowledgement");

		await call.result("task", "review-call-1", false);
		expect(
			reasonOf(
				await call(
					"task",
					structuredClone(CANONICAL_INPUT),
					"review-call-3",
				),
			),
		).toContain("already completed");
	});

	test("allows one retry only after a terminal Task error", async () => {
		const call = gate(LEAD_PROMPT);

		expect(
			await call("task", structuredClone(CANONICAL_INPUT), "review-call-1"),
		).toEqual({ input: CANONICAL_INPUT });
		await call.result("task", "review-call-1", true);
		expect(
			await call("task", structuredClone(CANONICAL_INPUT), "review-call-2"),
		).toEqual({ input: CANONICAL_INPUT });
		await call.result("task", "review-call-2", true);

		expect(
			reasonOf(
				await call(
					"task",
					structuredClone(CANONICAL_INPUT),
					"review-call-3",
				),
			),
		).toContain("consumed its one transport retry");
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
