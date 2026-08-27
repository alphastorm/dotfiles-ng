# Personal interaction memory

- Reserve long-term memory for durable personal interaction preferences; never put those in repositories. Keep project decisions, authorization packets, checkpoints, and task state in project artifacts.
- When retaining durable cross-repository workstation, computer/appliance, SSH, or shared OMP-topology facts, set the memory item scope to `global` even when the fact was learned in dotfiles or dotfiles-private. Keep raw transcripts and repo-specific facts project-scoped.
- A workflow or authorization gate exists only when the next step requires a user decision or authority for an external, privileged, irreversible, or material boundary effect. Routine diagnostics, in-envelope implementation, focused verification, reuse of valid evidence, and retries within declared caps are not gates. Never create a gate solely because a checklist, packet, checkpoint, review record, or lifecycle artifact is incomplete. At a real gate, MUST invoke Ask before yielding; recommend/preselect the safest valid happy path for Enter, include a held/no-effect option when appropriate, and never require the user to copy prose back as authorization.
- `FOUNDER-ONLY / AGENT MUST NOT EXECUTE` overrides Hub and every launcher convention. Ask is the authorization surface only, never the delivery or copy surface: it MUST identify the exact bounded command/effect, but the founder MUST NOT be expected to copy a command from an Ask preview. Immediately after the founder selects a command-bearing option, the agent MUST, without waiting for a reminder, stage that exact non-secret command text in the macOS clipboard with `pbcopy` and print the identical command in the next normal response inside a persistent shell-safe code block. This applies especially to long or multiline commands even when Ask already previewed them; a held/no-effect selection stages nothing. Clipboard staging is presentation, not execution. Agents MUST NOT launch the command, type or paste it into a target terminal, attach to its process, or automate its execution.
- Every commit MUST follow Conventional Commits 1.0.0: use `type(scope): description` or `type: description` with an appropriate standard type and a concise, lowercase imperative description.

## Pragmatic principal engineering

- Optimize externally verified completed outcomes, not activity proxies; verify the actual tool and route, and measure failure cost separately.
- Implementation choice order: reuse existing code or patterns → standard library → native platform capability → already-installed dependency → minimum new code; stop at the first option that fully satisfies the contract.
- On explicit simplification reviews, classify findings as `delete` | `stdlib` | `native` | `yagni` | `shrink`; each finding names the exact location, replacement, preserved behavior, and evidence. Never use net line count as the objective.
- Mechanize recurring steering-correction classes in config, tools, or tests; remove duplicate prompt prose instead of relying on reminders.

## RepoPrompt MCP planning lane

- Use RepoPrompt only through the native `RepoPromptCE` MCP tools; never route RepoPrompt work through `rpce-cli`, shell wrappers, or Computer Use.
- Keep the lane bounded to an explicit RepoPrompt skill or user request, or repository workflow guidance that requests Context Builder. RepoPrompt does not replace OMP's implementation, authority, or acceptance paths.
- OMP may announce late-loaded RepoPrompt tools as `xd://mcp__repopromptce_*` devices. Read the device URI for its schema, then write the JSON arguments object to the same URI; an absent key in `Object.keys(tool)` does not mean the announced XDev device is unavailable.
- Before a RepoPrompt call, check `bind_context op="status"`; if routing is ambiguous, use `bind_context op="list"` then bind the intended `context_id`. Confirm the target root with `get_file_tree` rather than inferring it from a window title.
- For preparation, call `context_builder` with `response_type="plan"` and `export_response=true`. Describe the objective and uncertainty, then use the returned `chat_id`, `oracle_export_path`, and `oracle_export_instruction` as the handoff contract.
- If the native tool is unavailable, retry once only after checking server health and routing. Then mark the lane unavailable and continue without it; never fall back to the CLI or rerun an unresolved long request.

### Diagnostic discovery before freeze

- A subject remains **mutable discovery work** until the complete named state-faithful diagnostic reaches its intended operator-visible outcome.
- Fixing an intermediate blocker means: **“this stage is green; the diagnostic advanced.”** It does not make the candidate ready or indicate regression when a later stage fails.
- While the diagnostic is red, permit only: inspect the first failure, run one bounded hypothesis probe, apply the nearest fix, run narrow proof, and resume the diagnostic.
- Prohibit candidate declaration, formal freeze, gate admission, full-suite reruns, review, checkpoint updates, and receipt preparation during this discovery lane.
- A check that does not exercise the failing lifecycle is supporting evidence only and MUST NOT trigger lifecycle advancement.
- Freeze once, only after the complete diagnostic—and any required fault matrix—passes unchanged on the same subject.
- Report sequential unmasking as three distinct fields: **Fixed:** the intermediate blocker; **Advanced to:** the next observed stage; **Candidate status:** still diagnostic-red; not frozen. Do not call a later-stage failure a regression.

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
