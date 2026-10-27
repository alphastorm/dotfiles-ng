import { link, mkdir, open, readFile, rm } from "node:fs/promises";
import { dirname } from "node:path";

export function isJsonObject(
	value: unknown,
): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function writeJsonOnce(path: string, value: unknown): Promise<boolean> {
	await mkdir(dirname(path), { recursive: true, mode: 0o700 });
	const temporary = `${path}.${process.pid}.${crypto.randomUUID()}.tmp`;
	let handle: Awaited<ReturnType<typeof open>> | undefined;
	try {
		handle = await open(temporary, "wx", 0o600);
		await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
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

export async function readJson(path: string): Promise<Record<string, unknown>> {
	const parsed: unknown = JSON.parse(await readFile(path, "utf8"));
	if (!isJsonObject(parsed)) throw new Error(`${path} is not a JSON object`);
	return parsed;
}
