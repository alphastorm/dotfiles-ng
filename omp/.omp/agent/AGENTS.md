# Personal interaction memory

- Reserve long-term memory for durable personal interaction preferences; never put those in repositories. Keep project decisions, authorization packets, checkpoints, and task state in project artifacts.
- At every workflow or authorization gate, MUST invoke Ask before yielding. The safest valid happy path MUST be recommended/preselected for Enter. NEVER put an actionable “recommended next move” only in prose, and NEVER require the user to copy or resend prose to authorize it. If no effect is authorized, Ask MUST offer the held/no-effect option and any exact bounded next packet or action; prose may summarize only after Ask has resolved the gate or when no further choice exists.
- `FOUNDER-ONLY / AGENT MUST NOT EXECUTE` overrides Hub and every launcher convention. Agents may only render the exact command and present it through Ask; they MUST NOT launch, type, paste, attach to, or automate it.
- Every commit MUST follow Conventional Commits 1.0.0: use `type(scope): description` or `type: description` with an appropriate standard type and a concise, lowercase imperative description.

## Cloud processing and delegation

- Repository work and research are permitted to use cloud models by default. A task is blocked from cloud processing only when the user, repository, or applicable customer policy explicitly marks it `NO_CLOUD`; OMP MUST NOT infer a separate confidential, private, or local-only class. For `NO_CLOUD` work, do not dispatch any cloud-backed agent or read marked content into a cloud-backed context; stop before transmission and invoke Ask for an approved non-cloud path.
- `NO_CLOUD` is a parent-side pre-dispatch gate: evaluate it before every Task, hosted reviewer, Jules, or other cloud-agent call. Agent-side `NO_CLOUD` checks are defense in depth, never authorization to dispatch first and refuse later.
- Route by context size, cross-document synthesis value, and latency—not by “all subagents” or inferred data sensitivity. Use the `long-context` task agent for read-only work when the relevant input is roughly 96K tokens or more, spans multiple large source files, is a large transcript/design/log/research packet, or would occupy the main agent while three or more short local tasks are ready.
- Current long-context qualification covers bundles through 1,025,629 characters. Do not rely on the advertised 1M-token limit: both qualified Gemini arms returned empty output at 3,271,394 characters, so split larger packets before dispatch.
- Use `scout` for bounded repository investigation, short or mid-sized multi-file reconnaissance, log/document triage, and web research below the long-context lane. Its ordered Luna/Terra route includes LSP and web search. Repository work and research need no separate cloud-eligibility classification unless explicitly marked `NO_CLOUD`.
- Keep tiny router outputs, titles, commit messages, deterministic classifications, default implementation, and unqualified mutating workflows on their existing models.
- `long-context` uses Antigravity; `scout` uses OpenAI Codex. An explicit `NO_CLOUD` marker blocks both and every other cloud-backed subagent.
- Treat cloud-provider quota as opportunistic. On availability or quota failure, avoid retry loops and use only another declared cloud route when the task is not `NO_CLOUD`; otherwise stop without effect.

## Jules asynchronous repository work

- Use Jules for cloud-permitted GitHub repository tasks that can run asynchronously in a remote VM, especially a long task that would otherwise block several short local requests. Keep immediate interactive implementation on the existing Codex lane.
- Dispatch only to repositories confirmed by `jules remote list --repo`. NEVER send an unlisted repository or a task explicitly marked `NO_CLOUD` to Jules.
- Creating a Jules session, approving its plan, expanding GitHub App scope, and applying a pulled patch are workflow/authorization gates. Invoke Ask before each effect unless the user explicitly authorized that exact bounded effect in the current turn.
- Treat Jules output as an external change proposal: inspect the plan and diff, verify in the target repository, and use `jules remote pull --apply` only after explicit approval. Do not automatically merge or push.
- Treat Jules availability and quota as opportunistic. On failure, keep the task in the existing Codex lane; avoid retry loops.
