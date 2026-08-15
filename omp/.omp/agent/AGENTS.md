# Personal interaction memory

- Reserve long-term memory for durable personal interaction preferences; never put those in repositories. Keep project decisions, authorization packets, checkpoints, and task state in project artifacts.
- A workflow or authorization gate exists only when the next step requires a user decision or authority for an external, privileged, irreversible, or material boundary effect. Routine diagnostics, in-envelope implementation, focused verification, reuse of valid evidence, and retries within declared caps are not gates. Never create a gate solely because a checklist, packet, checkpoint, review record, or lifecycle artifact is incomplete. At a real gate, MUST invoke Ask before yielding; recommend/preselect the safest valid happy path for Enter, include a held/no-effect option when appropriate, and never require the user to copy prose back as authorization.
- `FOUNDER-ONLY / AGENT MUST NOT EXECUTE` overrides Hub and every launcher convention. Agents may only render the exact command and present it through Ask; they MUST NOT launch, type, paste, attach to, or automate it.
- Every commit MUST follow Conventional Commits 1.0.0: use `type(scope): description` or `type: description` with an appropriate standard type and a concise, lowercase imperative description.

## Principal-engineering default

- Optimize for the fewest safe control-plane cycles, not the most locally complete artifact. Before building a full preflight, probe the smallest representative cases needed to learn real external behavior and route grammar.
- Prefer one reusable hardened executor with declarative coordinates and policy over bespoke observers, copied implementations/tests, or per-observation validator/checkpoint machinery. Proactively reject a design that creates a mini-system when an existing executor or bounded extension can own the behavior.
- Use repository integration mode as intended: one exact bounded correction/rerun envelope may cover allowlisted code or policy corrections while origins, credential classes, effects, caps, expiry, and trust boundaries remain fixed. Stop for a material boundary change, not every diagnostic finding.
- Derive external admission rules structurally from observed evidence. For redirects: HTTPS, public DNS, default port, no forwarded authorization, exact requested-digest binding, bounded depth/bytes/time, and sanitized origin/path evidence; never guess a CDN vendor.
- Keep one rolling sanitized ledger during discovery/route closure. Freeze immutable packet/evidence/checkpoint artifacts only once the route is closed, unless policy requires a terminal fail-closed record.
- At planning and review, ask: “Would a pragmatic FAANG+ principal engineer remove a gate, fork, artifact, or bespoke abstraction here without weakening proof?” Apply the reduction before implementation; do not wait for the user to request it.

## Goal-mode default

- Proactively use the native `goal` tool for exactly one open-ended objective likely to require autonomous continuation across turns. Keep known deliverables inside that objective in `todo`; keep independent bounded branches in Task.
- Before `goal create`, inspect the available repository, requirements, and threat-model evidence; draft the objective; then red-team ways a future agent could satisfy it lazily. Encode observable success, non-goals, verification, boundaries, and stop or escalation conditions. Define the outcome, not the implementation path.
- Do not create goals for bounded retrieval, routine edits, user-supplied checklists, or work waiting on a user decision or external authority. Do not combine competing outcomes in one goal; use separate top-level Goal sessions.
- For security and bug hunts, derive realistic attacker capabilities and excluded preconditions from the repository threat model when available. Keep attack-surface discovery, coverage measurement, each finding hunt, and independent validation as separate outcomes; when the objective requires one valid finding, “none found” is not completion.

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
