# Research Review Protocol — ChatGPT + Grok

Status: **ACTIVE**

Purpose: prevent post-hoc tuning and keep all research decisions reproducible.

## Required sequence for every new research step

1. **SPEC**
   - Write the hypothesis, data window, parameters, metrics, pass/fail rules and forbidden changes.
   - No evaluation data are inspected after the spec is drafted.

2. **FOR GROK REVIEW**
   - Prepare a short review packet containing:
     - current state
     - exact hypothesis
     - locked parameters
     - evaluation fold
     - pass/fail rules
     - unresolved implementation ambiguities
     - explicit list of things Grok may critique
     - explicit list of things that may not be changed after results are seen

3. **GROK REVIEW**
   - User sends the packet to Grok and returns Grok's response unchanged or substantially complete.
   - Grok feedback is treated as external peer review, not as authority.

4. **CHATGPT CONSISTENCY CHECK**
   - Compare Grok feedback against:
     - current spec
     - prior research boundaries
     - data contamination rules
     - implementation semantics
   - Classify each Grok suggestion:
     - ACCEPT BEFORE RUN
     - REJECT — conflicts with locked methodology
     - DEFER — new hypothesis requiring separate preregistration
     - CLARIFY — implementation detail that must be fixed before run

5. **LOCK**
   - Commit the final spec to `main`.
   - Record the commit hash.
   - No parameter or decision-rule changes after this point unless the run is invalid for a non-strategy reason such as missing market data.

6. **IMPLEMENT**
   - Implement exactly the locked spec.
   - Synthetic/smoke tests are allowed before touching official evaluation data.
   - Implementation may not add hidden filters, timing rules, fallback behavior or extra exits.

7. **RUN**
   - Run exactly once on the official evaluation fold unless the run is invalid before producing strategy metrics.
   - No grid.
   - No second attempt after seeing valid strategy results.

8. **RESULT**
   - Record PASS / FAIL / INSUFFICIENT_SAMPLE exactly as preregistered.
   - Store result artifact and close the branch of research.
   - Any next hypothesis starts again at step 1.

## Mandatory FOR GROK REVIEW format

```text
FOR GROK REVIEW

PROJECT STATE
...

CURRENT QUESTION
...

LOCKED SPEC
...

EVALUATION DATA
...

PASS / FAIL
...

IMPLEMENTATION DETAILS TO REVIEW
...

YOU MAY CHALLENGE
...

DO NOT PROPOSE AFTER RESULTS
...

REQUEST
Review this only for methodological or implementation flaws before the run.
Do not optimize parameters and do not infer thresholds from prior evaluation results.
```

## Standing rules

- Grok review happens before official evaluation whenever feasible.
- If Grok is consulted after a valid evaluation result exists, its suggestions cannot modify that experiment.
- Suggestions derived from a used evaluation fold are discovery only.
- A failed hypothesis is not repaired on the same fold.
- A router is never used to rescue two individually negative strategies.
- Production/paper deployment requires a strategy to survive its own cost-aware evaluation first.
