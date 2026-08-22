# Personal interaction memory

- Reserve long-term memory for durable personal interaction preferences; never put those in repositories. Keep project decisions, authorization packets, checkpoints, and task state in project artifacts.
- A workflow or authorization gate exists only when the next step requires a user decision or authority for an external, privileged, irreversible, or material boundary effect. Routine diagnostics, in-envelope implementation, focused verification, reuse of valid evidence, and retries within declared caps are not gates. Never create a gate solely because a checklist, packet, checkpoint, review record, or lifecycle artifact is incomplete. At a real gate, MUST invoke Ask before yielding; recommend/preselect the safest valid happy path for Enter, include a held/no-effect option when appropriate, and never require the user to copy prose back as authorization.
- `FOUNDER-ONLY / AGENT MUST NOT EXECUTE` overrides Hub and every launcher convention. Ask is the authorization surface only, never the delivery or copy surface: it MUST identify the exact bounded command/effect, but the founder MUST NOT be expected to copy a command from an Ask preview. Immediately after the founder selects a command-bearing option, the agent MUST, without waiting for a reminder, stage that exact non-secret command text in the macOS clipboard with `pbcopy` and print the identical command in the next normal response inside a persistent shell-safe code block. This applies especially to long or multiline commands even when Ask already previewed them; a held/no-effect selection stages nothing. Clipboard staging is presentation, not execution. Agents MUST NOT launch the command, type or paste it into a target terminal, attach to its process, or automate its execution.
- Every commit MUST follow Conventional Commits 1.0.0: use `type(scope): description` or `type: description` with an appropriate standard type and a concise, lowercase imperative description.

## Principal-engineering defaults

- Optimize externally verified completed outcomes, not activity proxies; verify the actual tool and route, and measure failure cost separately.
- Mechanize recurring steering-correction classes in config, tools, or tests; remove duplicate prompt prose instead of relying on reminders.

## Independent review loops

- Every reviewer/council dispatch follows `skill://critical-review`: self-check, verdict-only review, executable class closure/stop-rule, round budget, disposition exit, and subject freeze—never edit while reviewers read.

## Post-implementation bounded cleanse

- After an implementation work package is complete and committed in a repository exposing a bounded cleanse lane (`bun run cleanse:auto` / `scripts/run_cleanse_lane.sh`), launch that lane once, detached in the background, and report the receipt or `cleanse/auto-*` branch when it lands. This applies to every repository with a lane (omp-monorepo, alpha-founder, and future ones), so adoption is automatic, not manual.
- The lane is mechanical repair only — lint, typecheck, optionally bounded test fixes. It never substitutes for design, implementation ownership, critical review, or final verification, and merging a `cleanse/auto-*` branch stays a human decision.
- Do not launch it for research or Q&A turns, mid-implementation, on a dirty tree, under sealed markers, or from within a cleanse run; the lane also enforces these skips itself. One launch per completed package.

## Goal mode

- Use the native `goal` tool for exactly one open-ended objective likely to require autonomous continuation across turns. Keep known deliverables in `todo` and independent bounded branches in Task.
- Create goals unbounded by default by omitting `token_budget`; set a finite budget only when the user explicitly requests a cap. Never block or abandon `todo` phases because of an agent-selected budget.
- Before `goal create`, inspect the repository and requirements, red-team lazy success, and encode observable success, non-goals, verification, boundaries, and stop conditions. Define the outcome, not the implementation path.
- Do not create goals for bounded retrieval, routine edits, user-supplied checklists, work awaiting a user decision, or competing outcomes.
- For security and bug hunts, derive attacker capabilities and excluded preconditions from the repository threat model. Keep discovery, coverage, each finding hunt, and independent validation as separate outcomes.

## Cloud processing and delegation

- Repository work and research are cloud-permitted by default. Only an explicit user, repository, or customer-policy `NO_CLOUD` marker blocks cloud-backed agents; never infer a separate confidential, private, or local-only class.
- Evaluate `NO_CLOUD` before every Task, hosted reviewer, Jules, or other cloud-agent dispatch. Agent-side checks are defense in depth, never dispatch authority.
- Route by context size, synthesis value, and latency. Use `long-context` for read-only bundles around 96K tokens or more, `scout` for smaller bounded investigation, and the normal mutating lane for implementation.
- Keep the main session on its configured default so native Code Mode applies; use isolated roles or subagents for alternate models unless the user explicitly requests a bounded main-model experiment.
- Treat provider availability and quota as opportunistic. On failure, use another declared route only when cloud processing remains allowed; otherwise stop without effect.
- `local-librarian` is an additive read-only RTX 5090 lane (`@local-batch`) for bounded evidence packets — roughly 8K+ tokens, 5+ files, 1K+ log lines, or batch document extraction. Dispatch it explicitly and asynchronously when its source-linked packet can help without delaying the critical path; omit `blocking: true`. Never use it for mutation, security disposition, architecture ownership, external research, or final review, and never let it satisfy evidence where a missed fact or a leaked secret would matter. It has no cloud fallback: if the appliance is down a request hangs until your own timeout, so treat every packet as discardable. Qualification measured it below the automatic-dispatch floor, so it supplements your own reads rather than replacing them.

## Jules asynchronous repository work

- Use Jules only for cloud-permitted GitHub repositories confirmed by `jules remote list --repo`, especially work suited to an asynchronous remote VM. Keep immediate interactive implementation local.
- Creating a session, approving its plan, expanding GitHub App scope, and applying a pulled patch are authority gates. Invoke Ask before each effect unless the user authorized that exact bounded effect in the current turn.
- Treat Jules output as an external proposal: inspect and verify it locally, and never automatically merge, push, or expand repository access.
- On Jules availability or quota failure, continue in the existing local lane rather than retrying.
