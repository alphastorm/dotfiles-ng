# Personal interaction memory

- Reserve long-term memory for durable personal interaction preferences; never put those in repositories. Keep project decisions, authorization packets, checkpoints, and task state in project artifacts.
- At every workflow or authorization gate, MUST invoke Ask before yielding. The safest valid happy path MUST be recommended/preselected for Enter. NEVER put an actionable “recommended next move” only in prose, and NEVER require the user to copy or resend prose to authorize it. If no effect is authorized, Ask MUST offer the held/no-effect option and any exact bounded next packet or action; prose may summarize only after Ask has resolved the gate or when no further choice exists.

## Gemini cloud delegation

- Route by context size, cross-document synthesis value, data classification, and latency—not by “all subagents.” Use the `long-context` task agent for read-only work when the relevant input is roughly 96K tokens or more, spans multiple large source files, is a large transcript/design/log/research packet, or would occupy the main agent while three or more short local tasks are ready.
- Current long-context qualification covers bundles through 1,025,629 characters. Do not rely on the advertised 1M-token limit: both qualified Gemini arms returned empty output at 3,271,394 characters, so split larger packets before dispatch.
- Use `cloud-scout` for cloud-eligible public or non-sensitive multi-file reconnaissance, log/document triage, and research below the long-context lane. Keep `scout`/Qwen for local-only or private data and tiny ordinary investigations.
- Keep tiny router outputs, titles, commit messages, deterministic classifications, default implementation, and unqualified mutating workflows on their existing models.
- Antigravity is a remote provider. NEVER use `long-context` or `cloud-scout` for repositories or data marked local-only, confidential-to-device, or prohibited from external processing. Resolve the boundary from existing project context first; if it remains unknowable and Antigravity is required, invoke Ask before transmitting content.
- Treat Antigravity quota as opportunistic. On an availability or quota failure, avoid retry loops: use local Qwen for local-only/read-only work or the existing Codex lane for cloud-eligible work.

## Jules asynchronous repository work

- Use Jules for cloud-eligible GitHub repository tasks that can run asynchronously in a remote VM, especially a long task that would otherwise block several short local requests. Keep immediate interactive implementation on the existing Codex lane.
- Dispatch only to repositories confirmed by `jules remote list --repo`. NEVER send local-only, confidential-to-device, or unlisted repository content to Jules.
- Creating a Jules session, approving its plan, expanding GitHub App scope, and applying a pulled patch are workflow/authorization gates. Invoke Ask before each effect unless the user explicitly authorized that exact bounded effect in the current turn.
- Treat Jules output as an external change proposal: inspect the plan and diff, verify in the target repository, and use `jules remote pull --apply` only after explicit approval. Do not automatically merge or push.
- Treat Jules availability and quota as opportunistic. On failure, keep the task local or use the existing cloud-eligible Codex lane; avoid retry loops.
