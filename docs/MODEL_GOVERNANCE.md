# Model Governance

Models, features, data, prompts and configurations are versioned and approved separately. Predictions record horizon, confidence, calibration, explanation, uncertainty and expiry. Monitor data, feature, prediction, calibration, performance, regime and execution drift; drift may reduce or disable risk but never increase it.

## Chronological evaluation controls

Cycle 224 adds a provider-neutral binary-classifier evaluator for already-made
chronological holdout predictions. The evaluation policy must be content-hashed
and approved before the candidate's training cutoff. A candidate may advance
only one declared complexity tier beyond its predecessor; skipping directly to
a more complex model fails closed.

Every observation binds the candidate and predecessor probabilities, realized
binary outcome and return, source reference, observation time, explanation base
probability and per-feature local contributions. Contributions must reconcile
to the candidate probability. The evaluator rejects reused observation IDs,
non-chronological or training-overlapping records, inconsistent feature sets,
invalid content hashes, one-class holdouts and undersized samples.

The report records precision, recall, F1, ROC-AUC, PR-AUC, Brier score, log
loss, information coefficient, economic value after cost, calibration bins and
expected calibration error. It also measures first-half versus second-half
Brier stability and candidate improvements over the predecessor. Pre-approved
policy thresholds determine the result; post-hoc threshold relaxation is not
an evaluation path.

The only outcomes are `BLOCKED` and `REVIEW_ELIGIBLE`. A review-eligible report
may create validation evidence for the existing model registry, but a separate
human registry approval remains mandatory. Evaluation cannot execute or load a
model, write a prediction, serve an endpoint, create a signal/order, contact a
provider/broker or increase risk.

Migration 0030 retains immutable policy versions, content-addressed reports and
their exact observations. PostgreSQL restart reconstruction, idempotent replay,
tamper rejection and database-level immutability are CI-verified. Current tests
use an attributed deterministic fixture; this is engineering evidence, not a
trained-model, causal-explanation, empirical-alpha or production-monitoring
claim.

## Explanation-sensitivity and degradation controls

Cycle 227 consumes an exact immutable Cycle 224 `REVIEW_ELIGIBLE` evaluation;
the upstream report hash is revalidated before use. A monitoring policy approved
before that evaluation sets minimum scenario coverage, maximum probability
shift, maximum confidence degradation and separate normalized thresholds for
data, feature, prediction, calibration, performance, regime, execution and cost
drift.

Every supplied sensitivity scenario binds a model, evaluated feature,
perturbation, baseline/perturbed probability and confidence, observation time
and source reference. All Cycle 224 explanation features and all eight drift
dimensions are mandatory. Evidence identities and reports are deterministic
under input reordering; missing, cross-model, future or tampered evidence fails
closed.

Outcomes are limited to `NO_THRESHOLD_BREACH_OBSERVED` and
`DEGRADED_REVIEW_REQUIRED`. Migration 0033 retains immutable policy, report,
scenario and degradation rows bound to the exact policy and Cycle 224 report
hashes. Restart reconstruction re-hashes every child row. This layer does not
run/perturb a model, create predictions, calculate production drift, prove
causation or grant approval, risk-increase, signal, order or live authority.

## Research-agent retrieval controls

Cycle 225 adds deterministic point-in-time retrieval over retained internal
evidence chunks. A policy approved before the request allowlists source kinds,
sets role access, bounds result count and requires minimum query-term coverage
and distinct-source diversity. Future, invalidated, wrong-instrument,
wrong-role and disallowed-kind evidence is excluded.

Only a `COMPLETE` report can create the exact `retrieval:<report>:<chunk>` source
references accepted by the existing structured agent contract.
`INSUFFICIENT_EVIDENCE` cannot be converted into an agent request. Policies,
chunks, reports and ranked citations are immutable, content-addressed and
restart/restore verified. This lexical fixture does not verify source truth,
perform semantic/vector retrieval, invoke a model or connector, approve a
sensitive action, or expose any signal/order/risk authority.

## Research-agent answer evaluation controls

Cycle 226 adds a separate pre-approved evaluation policy for structured agent
answers bound to a `COMPLETE` Cycle 225 retrieval report. Workflow, instrument,
role and timestamps must align. Every fact and inference requires an exact
binding to one or more retrieved citations; unretrieved references or missing,
extra or duplicate claim bindings fail closed.

The deterministic evaluator records per-claim lexical overlap, aggregate claim
support, citation utilization, distinct sources, declared confidence and query
coverage. Confidence cannot exceed either the policy ceiling or retrieval
coverage. Partial query coverage requires an explicit missing-data disclosure,
and causal phrases are conservatively blocked because lexical evidence cannot
verify causation. Outcomes are limited to `BLOCKED` and `REVIEW_ELIGIBLE`.

Migration 0032 retains immutable policies, reports and per-claim evaluations.
Each report is relationally bound to the exact policy content hash and retrieval
report; restart reconstruction, idempotency, direct-mutation rejection and
fresh-restore comparison are hosted-CI verified. The fixture proves engineering
behavior only. It does not establish semantic factuality, source truth,
external-model quality, causal validity or production acceptance and grants no
model, tool, signal, order, risk, approval or live-trading authority.

## Regime and ensemble controls

Regime inference is a transparent, historical-prefix-only probability model, not a claim of market certainty. Every estimate records probabilities for bullish, bearish and sideways states plus derived uncertainty. Ensemble weights are based on documented regime-weighted expected scores and are rejected when they breach the configured maximum; they are never silently capped. Any replacement with a learned classifier requires versioning, chronological validation, calibration evidence, drift monitoring and independent approval.
