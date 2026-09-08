# Module 3J.0: Generalized Feature Subject Identity V2

Status: **identity infrastructure only**. No derivative feature family is
computed here (no curve slope, carry, curvature, contango/backwardation, roll
yield, funding feature, mark/index basis or open-interest change). No real
provider/network call is authorized, and no strategy, signal, opportunity,
order or risk authority is granted. This is roadmap **NEXT-03 phase 4**, a
prerequisite for closing the RQ-009 cross-asset/derivatives Feature Authority
gap.

## 1. Why

The durable Feature Authority (`feature_authority.py`) was instrument-centric:
`FeatureMaterialization.instrument_id`, and the table's uniqueness/read
indexes, assumed every feature was about one `ProfessionalInstrument`. That is
correct for equity/crypto instrument features, but the Module 3I.3 futures
term-structure artifact is naturally about a **futures series** (a product
root like GC), not one listed contract. Putting a series ID into
`instrument_id` would let a series collide with an unrelated instrument
sharing the same text; manufacturing a fake `ProfessionalInstrument` to
satisfy the old column would fabricate identity nothing ever registered.

3J.0 generalizes the **existing** authority in place. It does not create a
second feature-materialization table or store, and it does not implement 3I.3
term-structure features yet — that is 3J.1.

## 2. Subject model

A feature subject is the pair `(subject_type, subject_id)`. Two subject types
are supported:

- `INSTRUMENT` — `subject_id` resolves against `professional_instruments`.
- `FUTURES_SERIES` — `subject_id` resolves against `futures_contract_series`
  (the existing 3H.1 authority; no parallel subject registry).

Any other subject kind fails closed: `FeatureSubjectType("ACCOUNT")` raises
`ValueError` in Python, and the migration's CHECK constraint rejects it at the
database layer regardless of caller.

## 3. Migration (`20260908_0046`) and legacy compatibility

`feature_materializations` is altered, not replaced:

- `subject_type` and `subject_id` are added and backfilled for every existing
  row to `subject_type='INSTRUMENT'`, `subject_id=instrument_id` — no
  financial value or content hash is recomputed. The constant-valued columns
  backfill via `ADD COLUMN ... DEFAULT` (a Postgres 11+ metadata-only
  operation that does not fire per-row triggers); `subject_id`'s per-row copy
  of `instrument_id` genuinely is an `UPDATE`, so the table's own immutability
  trigger is disabled for that one statement only and re-enabled immediately
  after.
- `instrument_id` is retained, now nullable, as a coherence-checked legacy
  convenience column: a CHECK constraint requires it to equal `subject_id` for
  an `INSTRUMENT` subject and to be `NULL` for a `FUTURES_SERIES` subject.
  There is exactly one authoritative identity — the subject pair — and
  `instrument_id` can never independently disagree with it.
- Uniqueness moves from `(feature_id, instrument_id, dataset_version,
  event_at, effective_at, knowledge_at)` to `(feature_id, subject_type,
  subject_id, dataset_version, event_at, effective_at, knowledge_at)`. The old
  constraint is **dropped**, not kept alongside the new one — keeping both
  would have let two `FUTURES_SERIES` materializations (both `instrument_id
  IS NULL`) collide under the new key while looking distinct under the old
  one's NULL semantics.
- A deferred constraint trigger, `require_valid_feature_subject`, proves at
  COMMIT that an `INSTRUMENT` subject exists in `professional_instruments` and
  a `FUTURES_SERIES` subject exists in `futures_contract_series`.

## 4. Hash-version compatibility

`hash_version` (`V1` | `V2`) marks which formula produced a row's
`content_hash`:

- **V1** is the pre-3J.0 formula (keyed on a literal `instrument_id` field),
  produced by the unchanged `PostgresFeatureAuthority.materialize()` /
  `FeatureMaterialization.create()` path. Every existing V1 hash is preserved
  exactly; the formula is never touched.
- **V2** is the generalized formula (keyed on `subject_type` and `subject_id`
  explicitly, plus a `"hash_version": "V2"` payload key), produced only by the
  new `PostgresFeatureAuthority.materialize_subject()` /
  `FeatureMaterializationV2.create()` path.

V1 and V2 are structurally distinct JSON payload shapes, not merely different
field values, so a V2 `INSTRUMENT` row can never collide with a V1 row for the
same instrument even when every other field matches
(`test_v2_hash_never_collides_with_v1_hash_for_the_same_instrument`).

**The subject-existence trigger applies only to `hash_version = 'V2'` rows.**
The pre-existing V1 instrument-only path was never specified to check
instrument existence, and several already-established Postgres suites
(`FeatureAuthorityPostgresTests`, the `TrendResearchV2`/`RegimeEngineV2`
Postgres suites) materialize features for fixture instrument identifiers
(e.g. `fixture:SPY`) that were never registered in `professional_instruments`.
Retroactively enforcing existence on that path would have broken already
green, unrelated tests for a rule those callers never opted into. The new
invariant governs the new generalized entry point; it does not reach backward
into the old one.

## 5. API

- `PostgresFeatureAuthority.materialize(FeatureMaterialization)` — unchanged.
  Internally populates `subject_type='INSTRUMENT'`, `subject_id=instrument_id`,
  `hash_version='V1'`; every existing caller needs no change.
- `PostgresFeatureAuthority.latest_as_of(feature_id, instrument_id,
  dataset_version, decision_at)` — unchanged, including its SQL: `instrument_id`
  remains populated and coherent for every `INSTRUMENT` row (old or new), so
  the existing equality filter can never match a `FUTURES_SERIES` row (whose
  `instrument_id` is always `NULL`).
- `PostgresFeatureAuthority.materialize_subject(FeatureMaterializationV2)` —
  new. The canonical generalized write path for either subject type.
- `PostgresFeatureAuthority.latest_as_of_subject(feature_id, subject_type,
  subject_id, dataset_version, decision_at)` — new. The canonical generalized
  read path; PIT-gated identically to `latest_as_of` (no row whose `event_at`,
  `effective_at`, `knowledge_at` or `computed_at` is after `decision_at` can
  ever be returned), and returns every stored row for the subject regardless
  of which hash version produced it.

## 6. What 3J.1 will need from the definition contract

`FeatureDefinitionVersion.required_dataset_types` is a free-form tuple of
strings already (`("OHLCV",)`, `("FUNDAMENTALS",)`, `("MACRO",)`), so it can
describe `SETTLEMENT_PRICE`, `OPEN_INTEREST`, `FUNDING_RATE_REALIZED`,
`FUNDING_RATE_INDICATIVE`, `MARK_PRICE`, `INDEX_PRICE` or
`FUTURES_TERM_STRUCTURE` without a schema change — these are just new string
values, not a closed enum. `FeatureFamily`, however, **is** a closed
`StrEnum` (`PRICE_RETURNS`, `TREND`, `MOMENTUM`, `VOLATILITY`, `LIQUIDITY`,
`FUNDAMENTAL`, `MACRO`) with no derivatives-shaped member; 3J.1 will need at
least one new family (e.g. `DERIVATIVES_TERM_STRUCTURE` or similar) before it
can register a definition for a curve-slope or funding-basis feature. This is
deliberately **not** added in 3J.0, per instruction not to broaden
`FeatureFamily` speculatively.

## 7. Negative-test coverage

`tests/test_feature_subject_identity_postgres.py` proves, against real
PostgreSQL, all 20 required invariants: unchanged V1 read/write behaviour for
an unregistered fixture instrument; a valid `FUTURES_SERIES` materialization;
rejection of an unknown series, an unknown instrument, an unsupported subject
type (both Python-level and a raw-SQL CHECK violation), a series ID placed
into the legacy `instrument_id` column, and an `INSTRUMENT` subject whose
legacy `instrument_id` disagrees with `subject_id`; two subject types sharing
one textual `subject_id` do not collide and do not share a hash; two futures
series never collide despite both having `instrument_id IS NULL`; identical V2
replay is idempotent; a natural-key match with a different hash fails closed;
subject-type and subject-id changes both change V2 identity; a future-known
materialization cannot leak into an earlier PIT read; a raw-SQL insert with an
unregistered subject fails at COMMIT (proving the trigger directly, not just
the Python wrapper); `UPDATE`/`DELETE` remain rejected; a restart preserves
generalized subject identity; and exactly one `feature_material*` table exists
in the schema. `tests/test_feature_subject_identity.py` covers the dataclass
validation and hash-determinism logic without a database.
