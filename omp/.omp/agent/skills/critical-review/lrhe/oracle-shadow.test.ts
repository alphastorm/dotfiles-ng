import { afterEach, describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
	collectOracleShadow,
	launchOracleShadow,
	ORACLE_PROGRAMMATIC_API_KEY,
} from "./oracle-shadow.ts";

const originalAgentDir = process.env.PI_CODING_AGENT_DIR;
const apiSymbol = Symbol.for(ORACLE_PROGRAMMATIC_API_KEY);

function digest(value: string): string {
	return createHash("sha256").update(value).digest("hex");
}

async function run(command: string[], cwd: string): Promise<string> {
	const child = Bun.spawn({ cmd: command, cwd, stdout: "pipe", stderr: "pipe" });
	const [exitCode, stdout, stderr] = await Promise.all([
		child.exited,
		new Response(child.stdout).text(),
		new Response(child.stderr).text(),
	]);
	if (exitCode !== 0) throw new Error(`${command.join(" ")} failed: ${stderr}`);
	return stdout.trim();
}

async function createSelectedFixture(root: string, reviewId: string) {
	process.env.PI_CODING_AGENT_DIR = join(root, "agent");
	const repositoryPath = join(root, "repository");
	const reviewDirectory = join(root, "review");
	await mkdir(join(repositoryPath, "src"), { recursive: true });
	await mkdir(reviewDirectory, { recursive: true });
	await run(["git", "init"], repositoryPath);
	await writeFile(join(repositoryPath, "src", "subject.txt"), "frozen bytes\n");
	await run(["git", "add", "src/subject.txt"], repositoryPath);
	await run(
		[
			"git",
			"-c",
			"user.name=Oracle Test",
			"-c",
			"user.email=oracle@example.invalid",
			"commit",
			"-m",
			"test subject",
		],
		repositoryPath,
	);
	const subjectCommit = await run(["git", "rev-parse", "HEAD"], repositoryPath);
	const prompt = "CRITICAL_REVIEW_ORACLE_SHADOW_V1\nReturn strict JSON.\n";
	const requestPath = join(reviewDirectory, "request.json");
	const dispatchPath = join(reviewDirectory, "dispatch.json");
	const resultPath = join(reviewDirectory, "result.json");
	const datasetRecordPath = join(root, "lrhe-data", "oracle-shadow", "subject.json");
	const oracleShadow = {
		schemaVersion: 1,
		reviewId,
		requestPath,
		dispatchPath,
		resultPath,
		datasetRecordPath,
		shadowId: "oracle-chatgpt-pro-web",
		modelFamily: "gpt",
		correlationGroup: "openai-chatgpt-pro-web",
		providerRoute: "openai-chatgpt-web",
		accessProfile: "chatgpt-pro-web-asxst0rm",
		dataAllowlistKey: "openai",
		preset: "pro_extended",
		executionMode: "pi_oracle_async",
		evidenceDelivery: "repository",
		selection: "selected",
		reasonCodes: [] as string[],
		standing: "none",
		blocksClosure: false,
		receivesPeerOutput: false,
		request: {
			repositoryPath,
			subjectCommit,
			files: ["src/subject.txt"],
			prompt,
			promptSha256: digest(prompt),
		},
	};
	const envelope = {
		schemaVersion: 2,
		reviewClass: "initial",
		subjectDigest: "e".repeat(64),
		receiptSha256: "f".repeat(64),
		oracleShadow,
	};
	const envelopePath = join(reviewDirectory, "envelope.json");
	const envelopeText = JSON.stringify(envelope, null, 2) + "\n";
	await writeFile(envelopePath, envelopeText);
	return {
		repositoryPath,
		reviewDirectory,
		requestPath,
		dispatchPath,
		resultPath,
		datasetRecordPath,
		oracleShadow,
		envelope,
		envelopePath,
		envelopeText,
	};
}

async function waitForFile(path: string, timeoutMs = 5_000): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		if (await Bun.file(path).exists()) return;
		await Bun.sleep(20);
	}
	throw new Error("timed out waiting for " + path);
}
afterEach(() => {
	if (originalAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
	else process.env.PI_CODING_AGENT_DIR = originalAgentDir;
	Reflect.deleteProperty(globalThis, apiSymbol);
});

describe("Oracle full-council shadow", () => {
	test("persists an authorization skip without calling Pi Oracle", async () => {
		const root = await mkdtemp(join(tmpdir(), "oracle-shadow-skip-"));
		process.env.PI_CODING_AGENT_DIR = join(root, "agent");
		const reviewDirectory = join(root, "review");
		const datasetDirectory = join(root, "lrhe-data", "oracle-shadow");
		await mkdir(reviewDirectory, { recursive: true });
		const resultPath = join(reviewDirectory, "result.json");
		const datasetRecordPath = join(datasetDirectory, "subject.json");
		const envelope = {
			schemaVersion: 2,
			reviewClass: "initial",
			subjectDigest: "a".repeat(64),
			receiptSha256: "b".repeat(64),
			oracleShadow: {
				schemaVersion: 1,
				reviewId: "CR-shadow-skip",
				requestPath: join(reviewDirectory, "request.json"),
				dispatchPath: join(reviewDirectory, "dispatch.json"),
				resultPath,
				datasetRecordPath,
				shadowId: "oracle-chatgpt-pro-web",
				modelFamily: "gpt",
				correlationGroup: "openai-chatgpt-pro-web",
				providerRoute: "openai-chatgpt-web",
				accessProfile: "chatgpt-pro-web-asxst0rm",
				dataAllowlistKey: "openai",
				preset: "pro_extended",
				executionMode: "pi_oracle_async",
				evidenceDelivery: "repository",
				selection: "skipped",
				reasonCodes: ["access_profile_not_authorized"],
				standing: "none",
				blocksClosure: false,
				receivesPeerOutput: false,
				request: null,
			},
		};
		const envelopePath = join(reviewDirectory, "envelope.json");
		const envelopeText = `${JSON.stringify(envelope, null, 2)}\n`;
		await writeFile(envelopePath, envelopeText);

		await launchOracleShadow(
			envelopePath,
			digest(envelopeText),
			{} as ExtensionContext,
		);

		const result = JSON.parse(await readFile(resultPath, "utf8"));
		const dataset = JSON.parse(await readFile(datasetRecordPath, "utf8"));
		expect(result).toMatchObject({
			status: "skipped",
			reasonCodes: ["access_profile_not_authorized"],
		});
		expect(dataset).toMatchObject({
			reviewId: "CR-shadow-skip",
			selection: "skipped",
			status: "skipped",
			lane: { standing: "none", blocksClosure: false, receivesPeerOutput: false },
		});
		await rm(root, { recursive: true, force: true });
	});

	test("archives the frozen commit and collects strict reviewer output", async () => {
		const root = await mkdtemp(join(tmpdir(), "oracle-shadow-selected-"));
		process.env.PI_CODING_AGENT_DIR = join(root, "agent");
		const repositoryPath = join(root, "repository");
		const reviewDirectory = join(root, "review");
		const datasetDirectory = join(root, "lrhe-data", "oracle-shadow");
		const jobDirectory = join(root, "oracle-job");
		await mkdir(join(repositoryPath, "src"), { recursive: true });
		await mkdir(reviewDirectory, { recursive: true });
		await mkdir(jobDirectory, { recursive: true });
		await run(["git", "init"], repositoryPath);
		await writeFile(join(repositoryPath, "src", "subject.txt"), "frozen bytes\n");
		await run(["git", "add", "src/subject.txt"], repositoryPath);
		await run(
			[
				"git",
				"-c",
				"user.name=Oracle Test",
				"-c",
				"user.email=oracle@example.invalid",
				"commit",
				"-m",
				"test subject",
			],
			repositoryPath,
		);
		const subjectCommit = await run(["git", "rev-parse", "HEAD"], repositoryPath);
		const hookMarker = join(root, "post-checkout-ran");
		const postCheckoutHook = join(repositoryPath, ".git", "hooks", "post-checkout");
		await writeFile(postCheckoutHook, `#!/bin/sh\nprintf hook > "${hookMarker}"\n`);
		await chmod(postCheckoutHook, 0o755);
		await writeFile(join(repositoryPath, "src", "subject.txt"), "mutable successor bytes\n");

		const prompt = "CRITICAL_REVIEW_ORACLE_SHADOW_V1\nReturn strict JSON.\n";
		const resultPath = join(reviewDirectory, "result.json");
		const datasetRecordPath = join(datasetDirectory, "subject.json");
		const envelope = {
			schemaVersion: 2,
			reviewClass: "initial",
			subjectDigest: "c".repeat(64),
			receiptSha256: "d".repeat(64),
			oracleShadow: {
				schemaVersion: 1,
				reviewId: "CR-shadow-selected",
				requestPath: join(reviewDirectory, "request.json"),
				dispatchPath: join(reviewDirectory, "dispatch.json"),
				resultPath,
				datasetRecordPath,
				shadowId: "oracle-chatgpt-pro-web",
				modelFamily: "gpt",
				correlationGroup: "openai-chatgpt-pro-web",
				providerRoute: "openai-chatgpt-web",
				accessProfile: "chatgpt-pro-web-asxst0rm",
				dataAllowlistKey: "openai",
				preset: "pro_extended",
				executionMode: "pi_oracle_async",
				evidenceDelivery: "repository",
				selection: "selected",
				reasonCodes: [],
				standing: "none",
				blocksClosure: false,
				receivesPeerOutput: false,
				request: {
					repositoryPath,
					subjectCommit,
					files: ["src/subject.txt"],
					prompt,
					promptSha256: digest(prompt),
				},
			},
		};
		const envelopePath = join(reviewDirectory, "envelope.json");
		const envelopeText = `${JSON.stringify(envelope, null, 2)}\n`;
		await writeFile(envelopePath, envelopeText);

		let submittedCwd = "";
		let submittedBytes = "";
		let submissionCount = 0;
		const pendingPointerPaths: string[] = [];
		Object.defineProperty(globalThis, apiSymbol, {
			configurable: true,
			value: {
				version: 1,
				preflight: async () => {
					const launchPointer = JSON.parse(await readFile(pendingPointerPaths[0], "utf8"));
					expect(launchPointer.jobPath).toBeNull();
					return { details: { ready: true } };
				},
				submit: async (
					ctx: ExtensionContext,
					params: { preset: string; files: string[] },
				) => {
					submissionCount += 1;
					submittedCwd = ctx.cwd;
					submittedBytes = await readFile(join(ctx.cwd, params.files[0]), "utf8");
					expect(params.preset).toBe("pro_extended");
					const responsePath = join(jobDirectory, "response.md");
					const promptPath = join(jobDirectory, "prompt.md");
					await writeFile(
						responsePath,
						JSON.stringify({ summary: "clean", evidence: [], unresolved: [] }),
					);
					await writeFile(promptPath, prompt);
					await writeFile(
						join(jobDirectory, "job.json"),
						`${JSON.stringify({
							id: "oracle-job-test",
							status: "complete",
							completedAt: new Date().toISOString(),
							responsePath,
							extensionProvenance: { packageName: "pi-oracle", packageVersion: "test" },
						}, null, 2)}\n`,
					);
					return { details: { job: { id: "oracle-job-test", promptPath } } };
				},
			},
		});

		const launch = () =>
			launchOracleShadow(
				envelopePath,
				digest(envelopeText),
				{ cwd: repositoryPath } as ExtensionContext,
				(pointerPath) => {
					pendingPointerPaths.push(pointerPath);
				},
			);
		await Promise.all([launch(), launch()]);
		await launch();
		expect(submissionCount).toBe(1);
		expect(new Set(pendingPointerPaths).size).toBe(1);
		const pendingPointerPath = pendingPointerPaths[0];
		expect(pendingPointerPath).toBeDefined();
		const requestText = await readFile(envelope.oracleShadow.requestPath, "utf8");
		const pendingPointerText = await readFile(pendingPointerPath, "utf8");
		await collectOracleShadow(pendingPointerPath);

		const result = JSON.parse(await readFile(resultPath, "utf8"));
		const dataset = JSON.parse(await readFile(datasetRecordPath, "utf8"));
		expect(submittedCwd).not.toBe(repositoryPath);
		expect(submittedBytes).toBe("frozen bytes\n");
		expect(await readFile(join(repositoryPath, "src", "subject.txt"), "utf8")).toBe(
			"mutable successor bytes\n",
		);
		expect(result).toMatchObject({
			status: "complete",
			jobId: "oracle-job-test",
			parsedResponse: { summary: "clean", evidence: [], unresolved: [] },
			servedModelEvidence: null,
			requestSha256: digest(requestText),
		});
		expect(await Bun.file(hookMarker).exists()).toBe(false);
		expect(dataset).toMatchObject({
			status: "complete",
			selection: "selected",
			outcome: { requestedPreset: "pro_extended", servedModelEvidence: null },
		});

		await writeFile(
			join(jobDirectory, "job.json"),
			JSON.stringify({ id: "oracle-job-test", status: "failed", error: "late conflicting status" }),
		);
		await writeFile(pendingPointerPath, pendingPointerText);
		await collectOracleShadow(pendingPointerPath);
		const canonicalDataset = JSON.parse(await readFile(datasetRecordPath, "utf8"));
		expect(canonicalDataset.status).toBe("complete");
		expect(canonicalDataset.outcome).toEqual(result);
		expect(JSON.parse(await readFile(resultPath, "utf8"))).toEqual(result);
		await writeFile(datasetRecordPath, JSON.stringify({ status: "stale-progress" }));
		await launch();
		const repairedDataset = JSON.parse(await readFile(datasetRecordPath, "utf8"));
		expect(repairedDataset.status).toBe("complete");
		expect(repairedDataset.outcome).toEqual(result);
		expect(submissionCount).toBe(1);
		await rm(root, { recursive: true, force: true });
	});

	test("records an uncertain submit outcome once and never resubmits it", async () => {
		const root = await mkdtemp(join(tmpdir(), "oracle-shadow-submit-unknown-"));
		const fixture = await createSelectedFixture(root, "CR-shadow-submit-unknown");
		let submissionCount = 0;
		Object.defineProperty(globalThis, apiSymbol, {
			configurable: true,
			value: {
				version: 1,
				preflight: async () => ({ details: { ready: true } }),
				submit: async () => {
					submissionCount += 1;
					throw new Error("transport ended after submit began");
				},
			},
		});
		const pointerPaths: string[] = [];
		const launch = () => launchOracleShadow(
			fixture.envelopePath,
			digest(fixture.envelopeText),
			{ cwd: fixture.repositoryPath } as ExtensionContext,
			(pointerPath) => pointerPaths.push(pointerPath),
		);

		await launch();
		const result = JSON.parse(await readFile(fixture.resultPath, "utf8"));
		const dataset = JSON.parse(await readFile(fixture.datasetRecordPath, "utf8"));
		expect(submissionCount).toBe(1);
		expect(result.status).toBe("launch_outcome_unknown");
		expect(dataset.status).toBe("launch_outcome_unknown");
		expect(dataset.outcome).toEqual(result);
		expect(await Bun.file(pointerPaths[0]).exists()).toBe(false);

		await launch();
		expect(submissionCount).toBe(1);
		await rm(root, { recursive: true, force: true });
	});

	test("recovers a stranded launch without resubmitting and closes it after the original deadline", async () => {
		const root = await mkdtemp(join(tmpdir(), "oracle-shadow-recovery-"));
		const fixture = await createSelectedFixture(root, "CR-shadow-recovery");
		const createdAt = new Date(Date.now() - 7 * 60 * 60 * 1_000).toISOString();
		const shadow = fixture.oracleShadow;
		const requestDocument = {
			schemaVersion: 1,
			createdAt,
			envelopePath: fixture.envelopePath,
			envelopeSha256: digest(fixture.envelopeText),
			subjectDigest: fixture.envelope.subjectDigest,
			receiptSha256: fixture.envelope.receiptSha256,
			oracleShadow: {
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
			},
			request: shadow.request,
		};
		const requestText = JSON.stringify(requestDocument, null, 2) + "\n";
		await writeFile(fixture.requestPath, requestText);

		let preflightCount = 0;
		let submissionCount = 0;
		Object.defineProperty(globalThis, apiSymbol, {
			configurable: true,
			value: {
				version: 1,
				preflight: async () => {
					preflightCount += 1;
					return { details: { ready: true } };
				},
				submit: async () => {
					submissionCount += 1;
					return { details: {} };
				},
			},
		});

		const pendingPointerPaths: string[] = [];
		const recover = () => launchOracleShadow(
			fixture.envelopePath,
			digest(fixture.envelopeText),
			{ cwd: fixture.repositoryPath } as ExtensionContext,
			(pointerPath) => pendingPointerPaths.push(pointerPath),
		);
		await recover();
		await recover();
		expect(preflightCount).toBe(0);
		expect(submissionCount).toBe(0);
		expect(new Set(pendingPointerPaths).size).toBe(1);
		const pendingPointerPath = pendingPointerPaths[0];
		const pointer = JSON.parse(await readFile(pendingPointerPath, "utf8"));
		expect(pointer).toMatchObject({ createdAt, jobPath: null, requestSha256: digest(requestText) });
		const pendingDataset = JSON.parse(await readFile(fixture.datasetRecordPath, "utf8"));
		expect(pendingDataset.status).toBe("launch_outcome_unknown");

		const launcherPath = join(root, "resume-collector.ts");
		const oracleModulePath = join(import.meta.dir, "oracle-shadow.ts");
		await writeFile(
			launcherPath,
			`import { resumeOracleShadowCollectors } from ${JSON.stringify(oracleModulePath)};\nawait resumeOracleShadowCollectors();\n`,
		);
		const parent = Bun.spawn({
			cmd: ["bun", launcherPath],
			env: { ...process.env, PI_CODING_AGENT_DIR: process.env.PI_CODING_AGENT_DIR! },
			stdout: "ignore",
			stderr: "pipe",
		});
		const [exitCode, stderr] = await Promise.all([
			parent.exited,
			new Response(parent.stderr).text(),
		]);
		expect(exitCode, stderr).toBe(0);
		await waitForFile(fixture.resultPath);

		const result = JSON.parse(await readFile(fixture.resultPath, "utf8"));
		const dataset = JSON.parse(await readFile(fixture.datasetRecordPath, "utf8"));
		expect(result).toMatchObject({
			status: "collector_timeout",
			phase: "launch",
			jobPath: null,
			requestSha256: digest(requestText),
		});
		expect(dataset.status).toBe("collector_timeout");
		expect(dataset.outcome).toEqual(result);
		expect(await Bun.file(pendingPointerPath).exists()).toBe(false);

		await recover();
		expect(preflightCount).toBe(0);
		expect(submissionCount).toBe(0);
		await rm(root, { recursive: true, force: true });
	});
});
