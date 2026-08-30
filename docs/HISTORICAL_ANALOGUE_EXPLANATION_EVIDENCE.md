# Historical Analogue Explanation Evidence

Cycle 230 adds deterministic, point-in-time historical-analogue evidence for an
exact `REVIEW_ELIGIBLE` Cycle 224 binary-model evaluation. It is an explanatory
review artifact only: it never loads or invokes a model, creates a prediction,
changes a registry decision, emits a signal or constructs an order.

## Evidence contract

- A pre-approved immutable policy sets minimum similarity, selected-analogue
  bounds, regime/source-family diversity and maximum prediction-versus-realized-
  outcome divergence.
- The target binds the exact model, dataset and feature versions and supplies a
  normalized feature snapshot, predicted probability, confidence, timestamps
  and attributable source reference.
- Every candidate binds those same versions. Its observation, availability and
  outcome-availability timestamps must all precede the target availability, so
  future outcomes cannot enter the comparison.
- Feature names must exactly equal the evaluation report's importance graph.
  Weighted L1 distance uses that immutable importance evidence; similarity is
  `1 - distance`. Ranking is deterministic and capped by policy.
- All screened candidates are retained, including candidates below the
  similarity threshold. Selected membership and rank are content-addressed.

The four outcomes are `READY_FOR_REVIEW`, `DIVERGENCE_REVIEW_REQUIRED`,
`BLOCKED_INSUFFICIENT_EVIDENCE` and `BLOCKED_POLICY_DISABLED`. None is an
approval or readiness claim. Model-invocation, prediction and action authority
are database-enforced as `NONE`.

## Persistence and verification

Migration `20260830_0036` adds five immutable tables for policy versions,
targets, candidates, reports and complete report membership. Composite foreign
keys bind exact policy, evaluation, target and candidate content hashes. The
PostgreSQL store reloads registered evidence and re-derives the report before
insertion; restart reads verify both report and member-graph hashes.

Unit coverage includes deterministic ranking, below-threshold retention,
divergence escalation, disabled/insufficient fail-closed outcomes, version and
lookahead rejection, non-finite/tampered evidence rejection and input-order
invariance. A disposable PostgreSQL integration test covers migration, replay,
restart, exact reconstruction and immutable report/candidate enforcement.

## Limitations

Similarity over supplied normalized fixtures is neither a semantic nor causal
explanation. Historical outcomes do not establish future model quality,
economic value or production suitability. There is no trained baseline,
permutation/SHAP computation, production scoring, serving, external data or
trading authority in this cycle.
