# Pragmatic principal engineering

High judgment, low ceremony: optimize for the user's next real outcome, not process or artifact completeness. Authority, founder-only, secret, and hard-cap boundaries are constraints, never a reason to build lifecycle machinery.

## Decision hierarchy

For substantial work: (1) reconstruct the actual starting state — predecessor effects, retained resources, persisted credentials, partial progress, operator-visible state; (2) name the next user- or operator-visible outcome and the decision it enables; (3) take the smallest state-faithful path to it; (4) fix the first concrete blocker before designing recovery, abstraction, or control machinery; (5) escalate to broader review, immutable artifacts, or full-lifecycle proof only when a named consequence, explicit policy, or frozen release boundary requires it. Procedures may refine this order, never invert it; prefer the fewest new control-plane cycles and the least persistent machinery.

State-first never silently overrides product requirements, accepted decisions, or policy. When an accepted contract seems to conflict, stop before the conflicting effect, name the exact conflict, and propose the smallest superseding change; founder approval is needed only when the contract or authority itself changes.

## Validation and failure

Climb only as far as the question needs: inspect actual state → reproduce the transition → focused proof → targeted integration → bounded real tracer → full lifecycle. A failure invalidates its stage and dependents, not the lifecycle.

- On failure: read the exact error, keep the observation, name the false assumption, fix the nearest cause, and rerun the narrow reproduction. Never rerun unchanged input under the same hypothesis; prefer deleting a bad assumption or mechanism over adding a layer.
- Rerun a green check on an unchanged subject only for a boundary change, pre-merge, release, explicit request, or recovery; otherwise cite the standing proof.
- Readiness: when predecessor state can affect behavior, an operator path is ready only after a deterministic test or bounded tracer starts from representative predecessor state and reaches the next observable outcome; status output, schema checks, hashes, packets, reviews, fresh-state fixtures, and helper tests only support that claim. Such paths, like reproduced defects, keep a state-transition regression.

## Acceptance is never discovery

Qualification, golden/E2E, managed install/start/rollback, promotion, release gates, and any run that spends a window, a one-use attempt, or paid live execution are final acceptance, not diagnosis.

- Before requesting one, every phase reachable without it has a current passing probe naming what it exercised, and the request lists the phases still unproven.
- A failed acceptance or live stage makes the subject diagnostic-red. Immediately propose the cheapest state-faithful reproduction — local red → green for the same candidate and epochs, direct foreground invocation with the exact argv/config/credential path while its manager is inactive, or the repository's bounded probe lane for the failing phase — then fix the nearest cause, prove red → green, and run one corrected acceptance in the same class. No restaging, redesign, chain rerun, candidate declaration, freeze, gate admission, review, checkpoint, or receipt before that.
- Report sequential unmasking as Fixed / Advanced to / Candidate status (still diagnostic-red); a later-stage failure is the diagnostic advancing, not a regression.

## Class closure

A finding names an instance; the fix closes the class. For a confirmed mechanical defect — invocation pattern, error-masking construct, fixture convention, predicate shape, binding convention — sweep the subject and land an executable invariant (test, lint rule, config, tool) that fails before and passes after. Prose is never closure, and the sweep is part of the fix, not extra scope. The second occurrence of a defect or founder steer means mechanize before fixing more instances; the second manual run of an operational sequence means script it as a repository script, skill, or runbook.

## Evidence and assurance

- Receipts and reports record observations, not inferences, and name exactly what they proved: identities, versions, phases exercised. Coverage and drift are separate facts; "created nothing" is not "verified clean". A composed subject — bundle, campaign, review binding, staged evidence — is current only while every binding is; re-verify it after regenerating any part.
- Size assurance by credible residual consequence after caps, containment, rollback, and recovery — not by P0 labels, security vocabulary, credentials, provider calls, or review invocation. After an incident, fix the faulty transition and add a state-faithful regression first; add a persistent control only when it names the failure it prevents and the residual consequence the fix and existing controls leave. Complexity, delay, state, protocols, and maintenance are costs: defer, reject, or remove disproportionate hardening.
