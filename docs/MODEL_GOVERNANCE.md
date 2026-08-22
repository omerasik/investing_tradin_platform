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
## Regime and ensemble controls

Regime inference is a transparent, historical-prefix-only probability model, not a claim of market certainty. Every estimate records probabilities for bullish, bearish and sideways states plus derived uncertainty. Ensemble weights are based on documented regime-weighted expected scores and are rejected when they breach the configured maximum; they are never silently capped. Any replacement with a learned classifier requires versioning, chronological validation, calibration evidence, drift monitoring and independent approval.
