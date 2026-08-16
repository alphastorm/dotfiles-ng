# Personal interaction memory

- Reserve long-term memory for durable personal interaction preferences; never put those in repositories. Keep project decisions, authorization packets, checkpoints, and task state in project artifacts.
- A workflow or authorization gate exists only when the next step requires a user decision or authority for an external, privileged, irreversible, or material boundary effect. Routine diagnostics, in-envelope implementation, focused verification, reuse of valid evidence, and retries within declared caps are not gates. Never create a gate solely because a checklist, packet, checkpoint, review record, or lifecycle artifact is incomplete. At a real gate, MUST invoke Ask before yielding; recommend/preselect the safest valid happy path for Enter, include a held/no-effect option when appropriate, and never require the user to copy prose back as authorization.
- `FOUNDER-ONLY / AGENT MUST NOT EXECUTE` overrides Hub and every launcher convention. Agents MAY render the exact command and MUST present it through Ask. After the founder selects the exact authority, agents MAY also stage only that exact non-secret command text in the macOS clipboard with `pbcopy` when explicitly requested or when a durable founder preference requires it, and MUST print the same command in a persistent shell-safe code block. Clipboard staging is presentation, not execution. Agents MUST NOT launch the command, type or paste it into a target terminal, attach to its process, or automate its execution.
- Every commit MUST follow Conventional Commits 1.0.0: use `type(scope): description` or `type: description` with an appropriate standard type and a concise, lowercase imperative description.

## Principal-engineering defaults

- Start from actual persisted state and name the next observable outcome before selecting work.
- Use the smallest state-faithful proof; status, schemas, packets, reviews, and fresh fixtures do not substitute for readiness.
- Prefer an existing narrow executor or bounded extension over bespoke control-plane machinery.
- Add a persistent control only for a named residual consequence, and remove obsolete assumptions or mechanisms when possible.

## Goal mode

- Use the native `goal` tool for exactly one open-ended objective likely to require autonomous continuation across turns. Keep known deliverables in `todo` and independent bounded branches in Task.
- Before `goal create`, inspect the repository and requirements, red-team lazy success, and encode observable success, non-goals, verification, boundaries, and stop conditions. Define the outcome, not the implementation path.
- Do not create goals for bounded retrieval, routine edits, user-supplied checklists, work awaiting a user decision, or competing outcomes.
- For security and bug hunts, derive attacker capabilities and excluded preconditions from the repository threat model. Keep discovery, coverage, each finding hunt, and independent validation as separate outcomes.

## Cloud processing and delegation

- Repository work and research are cloud-permitted by default. Only an explicit user, repository, or customer-policy `NO_CLOUD` marker blocks cloud-backed agents; never infer a separate confidential, private, or local-only class.
- Evaluate `NO_CLOUD` before every Task, hosted reviewer, Jules, or other cloud-agent dispatch. Agent-side checks are defense in depth, never dispatch authority.
- Route by context size, synthesis value, and latency. Use `long-context` for read-only bundles around 96K tokens or more, `scout` for smaller bounded investigation, and the normal mutating lane for implementation.
- Treat provider availability and quota as opportunistic. On failure, use another declared route only when cloud processing remains allowed; otherwise stop without effect.

## Jules asynchronous repository work

- Use Jules only for cloud-permitted GitHub repositories confirmed by `jules remote list --repo`, especially work suited to an asynchronous remote VM. Keep immediate interactive implementation local.
- Creating a session, approving its plan, expanding GitHub App scope, and applying a pulled patch are authority gates. Invoke Ask before each effect unless the user authorized that exact bounded effect in the current turn.
- Treat Jules output as an external proposal: inspect and verify it locally, and never automatically merge, push, or expand repository access.
- On Jules availability or quota failure, continue in the existing local lane rather than retrying.
