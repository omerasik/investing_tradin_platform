# Development cost fragility evidence — 2026-10-23

Performance source: existing `trade_platform_research` ledger in `trade-platform-postgres-dev-research`, read in one READ ONLY / REPEATABLE READ transaction. No search, rerun, migration, acquisition, holdout read or state transition was performed. These are real persisted T2 development results, not fixture evidence or independent validation.

The source ledger is at `20261009_0062` and has no `strategy_lab_walk_forward_reports` table. The current comparison reader therefore cannot run end-to-end on this old schema. For this bounded inspection only, the absent walk-forward query was supplied with **no evidence** to the existing pure projection. This is not a production fallback or a successful runtime acceptance claim. Deployment needs the already-existing migrations through the normal rollout process; this task did not migrate that database. Walk-forward research is unavailable here.

The nine studies each have ten frozen candidates and a recorded Decimal rerun. All baseline thresholds below use their recorded 2,000,000 µs lag. ETH/SOL development windows are 2025-08-20..2026-08-18; BTC is 2025-11-28..2026-08-18. Dataset hashes and exclusive evaluation bounds remain in each comparison row. These different windows must not be treated as like-for-like rankings. Holdout boundary remains 2026-08-20.

## What can be assessed

`break_even_bps_per_side` is the recorded Decimal sum of gross step returns / turnover × 10,000, quantized by the authority rerun to 1e-18. It is not the break-even of compounded `total_return`. A positive threshold describes gross development headroom only. A proposed-slippage remainder is exactly recorded threshold minus the named bps-per-side scenario; it excludes account fees, observed spread and funding.

The names `conservative_medium_1bps`, `conservative_medium_2_5bps`, and `conservative_medium_5bps` label the **already prepared** 1 / 2.5 / 5 bps proposal in `docs/current-phase.yaml` at base revision `8f56062`, `prepared_packets.or6`. These labels and their source are explicit in the projection. They are not approved costs, estimates of execution, or acceptance criteria. Study-bound scenarios, when present, have a separate source label and also assert no approval.

| Study (full identity below) | Gross threshold min..max, bps/side | Positive / nonpositive | Above proposed 1 / 2.5 / 5 bps |
| --- | --- | --- | --- |
| mean_reversion_z on BTCUSDT 2025-11-28..2026-08-18 (`2b035df3`) | 0.228856157286292476 .. 1.021691301599761757 | 10 / 0 | 1 / 0 / 0 |
| mean_reversion_z on SOLUSDT 2025-08-20..2026-08-18 (`4b708ec9`) | 0.264621923069934905 .. 1.049229238123012234 | 10 / 0 | 1 / 0 / 0 |
| breakout_channel on ETHUSDT 2025-08-20..2026-08-18 (`566bdfb8`) | 0.689832864903562988 .. 6.541229107968154677 | 10 / 0 | 6 / 3 / 1 |
| trend_ma_cross on ETHUSDT 2025-08-20..2026-08-18 (`7bcff171`) | 0.383084638624606836 .. 6.326579663852785380 | 10 / 0 | 5 / 4 / 2 |
| breakout_channel on BTCUSDT 2025-11-28..2026-08-18 (`82527828`) | -4.129018632821303307 .. -0.674909154435919290 | 0 / 10 | 0 / 0 / 0 |
| breakout_channel on SOLUSDT 2025-08-20..2026-08-18 (`a71ebdd8`) | -2.665397069338928419 .. 0.372315020941645611 | 2 / 8 | 0 / 0 / 0 |
| trend_ma_cross on SOLUSDT 2025-08-20..2026-08-18 (`aa7239cb`) | 0.565516185377177652 .. 4.006746363283745518 | 10 / 0 | 8 / 4 / 0 |
| mean_reversion_z on ETHUSDT 2025-08-20..2026-08-18 (`bd0fd391`) | 0.195953506404384399 .. 1.005244641717962365 | 10 / 0 | 1 / 0 / 0 |
| trend_ma_cross on BTCUSDT 2025-11-28..2026-08-18 (`c2af1fd3`) | 0.170495091466013568 .. 6.735103397980238525 | 10 / 0 | 7 / 6 / 3 |

Across these 90 frozen candidates: 72 have positive gross thresholds; 18 do not. Only 29 / 17 / 6 thresholds exceed the proposed 1 / 2.5 / 5 bps respectively. These counts are descriptive, not a ranking or a recommendation. No candidate here establishes verified net profitability.

## Source identities

| Study UUID | Frozen candidate set hash | Decimal rerun hash |
| --- | --- | --- |
| `2b035df3-f687-54b5-983a-2ef9feb1db01` | `2daddef62e558e49a1335e263411cc644434bf9bf05bd74dd64d253f8e56c83c` | `aa12d36a0ecf63407fae1fc77b8e831f413dc8fc2855427a4afb3163f50fd7c1` |
| `4b708ec9-71ba-58a0-b8f7-b5c5e06e2f00` | `c3cd83eeed18c7c28d33eb47edd84340d230df16e0c64cccc90b2271a9b3b0cf` | `d6886ef4999962508560c6cd699029c55eeb8fd7abcbe7f9a1d1e10b146d62de` |
| `566bdfb8-301b-544c-93ce-ea6c9cfb5d77` | `4a6983399d75c61667ba4e8d28106e9d31105c7c870fc521316b3d2e9ebff8de` | `ca12f358a53e7ef7dedf0a4648a0811cdd0916f22d22baebb3b40b0233fc87fd` |
| `7bcff171-17eb-5f32-a1d3-b7e93f406d20` | `a935009183707861a012d7e739d15475584146f1e16741a534e2bebb33594900` | `c6845c146295fe5df295775702552af39be2ff6fd973e368b2e05ebc23cae11a` |
| `82527828-78db-565a-91b5-473860de478f` | `4560eb39fe77f87eba4508b224ffe3ce0f9ab43bf943eb3f67da0eae51e2b014` | `778fe133892752a6728cbdab3642d79c1c6b562d3339b55d0c4984a2506e25c1` |
| `a71ebdd8-beca-5439-bb4f-fedafffb37d2` | `dad8de3a533177d12f71f4d290bd4772df188380d0ddc9c3ac0e31ce4a258acd` | `ed4e0c792d2e66868889be4955b8244f4b62840f6063b2f6b525eb96336e7c61` |
| `aa7239cb-ca25-54de-8ab5-52f46f2c639b` | `2fa53f9e81845fbd6aadfab09d8f6956326af4590062d1a2ee721e34970cd4a6` | `dca88ab184ffc9831a22f6f3bf2699be069371e6572a5d265110f01d8340491c` |
| `bd0fd391-454a-5b0a-a54a-9fd452e2303d` | `2fc250248d6128d969035b5f4f021d485b3da0625ee1e5da352e2bd5b261f788` | `4d17bf5ed07a3e53f10f9e54b5744e3d4e6879ef32c277318ca23668adb0cc32` |
| `c2af1fd3-efd6-50b1-b66a-f3309586f107` | `50100b9bcdb5e00949e06a5aa2eb528210d903645a423a9874445134c6947959` | `6a4e5230f092bd4203f6fd8f6aa986ce0325e496ccfb5b0aa5744062e8867bb4` |

Each API row retains full study/content, manifest, frozen set, trial/content and result hashes; the Decimal dimension retains the rerun hash and every recorded sweep. Latest recorded evidence is used, never an older favorable result. Missing, non-numeric, zero-turnover, costed, funded, non-Decimal, unestablished selection or unbounded development evidence produces `UNAVAILABLE`, with reasons. Search thresholds stay separately labelled non-authoritative.

## Reproduce from the authoritative ledger

Use the read-only comparison API on a normally migrated deployment, explicitly selecting the study UUIDs above and inspecting `comparison.rows[].cost_fragility`. The assessment is additive to the existing read model; there is no new endpoint. No default sort is introduced. The pure calculation is `research_cost_fragility_v1.cost_fragility_v1`; the existing comparison verifies source identity hashes before projecting.

The following SQL independently reproduces the baseline threshold and proposed-slippage arithmetic directly from recorded evidence without requiring the absent walk-forward table. Run only on the identified development ledger. Preserve the full UUIDs/hashes and exclusive bounds; the source-identity table above pins this snapshot if later records appear.

```sql
BEGIN READ ONLY;
WITH frozen AS (
  SELECT DISTINCT ON (study_id) study_id, candidate_set_hash, identity
  FROM strategy_lab_candidate_sets
  ORDER BY study_id, recorded_at DESC, candidate_set_hash DESC
), reruns AS (
  SELECT DISTINCT ON (study_id, candidate_set_hash)
    study_id, candidate_set_hash, rerun_hash, identity
  FROM strategy_lab_authority_reruns
  ORDER BY study_id, candidate_set_hash, recorded_at DESC, rerun_hash DESC
)
SELECT s.study_id, s.content_hash, f.candidate_set_hash, r.rerun_hash,
  c->>'trial_id' AS trial_id, c->>'trial_content_hash' AS trial_content_hash,
  c->>'result_content_hash' AS result_content_hash,
  s.identity->>'evaluation_upper_bound_exclusive' AS evaluation_end_exclusive,
  r.identity->>'baseline_lag_micros' AS baseline_lag_micros,
  m->'metrics'->>'turnover' AS turnover,
  m->'metrics'->>'break_even_bps_per_side' AS recorded_threshold_bps_per_side,
  v.name AS proposed_scenario, v.bps AS proposed_slippage_bps_per_side,
  (m->'metrics'->>'break_even_bps_per_side')::numeric - v.bps AS remainder_excluding_fees_spread_funding
FROM frozen f JOIN strategy_lab_studies s USING (study_id)
JOIN reruns r USING (study_id, candidate_set_hash)
CROSS JOIN LATERAL jsonb_array_elements(f.identity->'candidates') c
CROSS JOIN LATERAL jsonb_array_elements(r.identity->'results') m
CROSS JOIN (VALUES ('conservative_medium_1bps', 1::numeric),
                   ('conservative_medium_2_5bps', 2.5::numeric),
                   ('conservative_medium_5bps', 5::numeric)) v(name, bps)
WHERE m->>'trial_id' = c->>'trial_id'
  AND m->>'lag_micros' = r.identity->>'baseline_lag_micros'
  AND m->'metrics'->>'numeric_tier' = 'DECIMAL_AUTHORITATIVE'
  AND m->'metrics'->>'cost_mode' = 'GROSS_NON_PROMOTABLE'
  AND m->'metrics'->>'funding_mode' IS NULL
  AND (m->'metrics'->>'turnover')::numeric > 0
  AND r.identity->'authoritative_selection'->>'status' = 'ESTABLISHED'
  AND (s.identity->>'evaluation_upper_bound_exclusive')::timestamptz <= '2026-08-20T00:00:00+00:00'::timestamptz
ORDER BY s.study_id, trial_id, v.bps;
COMMIT;
```

## Unresolved evidence and decisions

All nine stored study cost policies have `venue_fees: null` and no bound slippage scenarios. There are no stored preregistration packets in this ledger. Verified actual-account/VIP fees remain unavailable. The prepared envelope still needs its separate OR-6 decision; this projection does not approve it.

The historical L1 full-spread summary in `docs/current-phase.yaml` is diagnostic and comes from a different forward window. The comparison has no candidate-and-window-bound spread artifact, so it reports `UNAVAILABLE_NO_CANDIDATE_AND_WINDOW_BOUND_SPREAD_EVIDENCE`. It does not halve the full spread, combine it with slippage, or assume a fill cost. No spread or account-fee number is invented.

Forward funding settlement-price authority, admissible acquisition, fee/slippage approval, OR-7/9/11 and genuine forward continuity remain separate blockers. The existing paper economics and owner gates are unchanged; funding remains non-costable there, and net cannot be inferred. Holdout and paper promotion states are untouched.

Validation: 55 focused Python calculation/comparison/API tests; TypeScript, scoped Ruff/mypy and ESLint; terminal browser checks for exact decimal text, missing assessments, candidate identity, scenario separation and existing BTC/ETH/SOL navigation. PostgreSQL integration checks run in CI against its disposable service, never against this research ledger.


## Actual-account fee integration — 2026-10-23

The comparison now has a separate `account_fee_evidence` assessment. Existing
`FeeScheduleV1` payloads and OR-6 identities are unchanged. A schedule's venue,
product, tier, exact maker/taker bps, verifier, verification date and source
reference are reused, but a schedule alone is **not actual-account evidence**.
Neither an active paper account policy nor an authorized preregistration supplies
missing actual-account or historical fee coverage evidence.

The reusable server integration is `DevelopmentFeeBindingV1` in
`research_account_fee_evidence_v1.py`. Install an explicitly reviewed binding via
`PostgresOperatorDashboardQueries(..., terminal_fee_bindings={study_content_hash: binding})`. The existing
`read_terminal_comparison_v1` and `read_candidate_comparison_v1` readers also
accept `fee_bindings`. Pure callers can set `StudyComparisonEvidenceV1.fee_binding`.
There is no browser/API input for asserting verification, provider acquisition,
new evidence store, migration or default account selection.

Each binding declares the intended opaque account identifier, exact venue,
product and VIP/tier independently of the fee statement, plus the study hash,
frozen candidate set hash, all dataset hashes, full observation start/exclusive
end and an explicit assessment instant. Terminal reads use their server-side `as_of`
as the assessment instant, so an installed binding cannot confer future verification. The operator must establish the full
observation interval from those exact source datasets when preparing the binding;
there is no inferred first bar or fee coverage from a study's upper bound alone.
These are retrospective development assessments, not a claim that the fee was
known during the historical period or is applicable to execution today.

The content-addressed evidence envelope is canonical JSON with:

- `schema_version: actual-account-fee-evidence-v1`;
- `account_id`, `verification_status: VERIFIED` and
  `verification_source: ACTUAL_ACCOUNT_FEE_STATEMENT`;
- timezone-aware `valid_from` and `valid_until_exclusive` covering the **entire**
  declared observation interval;
- `fee_schedule`: the existing `FeeScheduleV1.payload()`, including a nonblank
  statement source reference, verifier and ISO verification date.

`evidence_hash` must equal `identity_hash_v1(evidence)`. This checks identity and
binding; the operator remains responsible for the truth of the verification
attestation and statement source. Public tables, hypothetical fees and fixtures
are never a substitute. No fixture in the tests constitutes financial evidence.
The projection rejects mismatched account/venue/product/tier or source hashes,
invalid/unverified evidence, incomplete coverage, expired coverage relative to
the observation period, future verification relative to the assessment, and
missing or malformed verification metadata. It retains the evidence identity,
source, verifier, date, coverage interval and intended context in the read model.
A fixed input produces a fixed comparison output hash.

The original gross threshold and proposed-slippage-only remainders stay intact.
Two additional exact Decimal fields provide:

- `remaining_after_fee_bps_per_side = recorded gross threshold - verified taker fee`;
- each scenario's `remaining_after_verified_fee_and_scenario_bps_per_side = recorded
  gross threshold - verified taker fee - that named proposed slippage`.

No browser floating-point arithmetic, compounded net-return calculation or
spread conversion is used. Missing gross authority still blocks every remainder,
even when fee evidence is verified. Missing fee evidence returns `UNAVAILABLE`
with specific missing requirements while preserving available gross development
and proposed-slippage-only results. The terminal displays fee-only and
fee-plus-proposed-slippage fields separately, with verification provenance and
no owner approval assertion. Paper economics, funding costability, risk,
preregistration, promotion and execution authority remain unchanged.

No matching real actual-account evidence was found in the repository's existing
contracts or documented ledger assessment. The historical gross figures above
remain as recorded; all fee-adjusted figures for that assessment remain
**UNAVAILABLE** until independently verified account/period evidence is installed.
The 1 / 2.5 / 5 bps slippage rows remain proposals. This integration makes no net
profitability or independent validation claim and does not resolve owner gates.


Focused validation for the integration: 105 deterministic calculation, comparison,
terminal API/read-model, OR-6 policy and paper economics tests; TypeScript and
scoped ESLint/Ruff; both existing terminal browser scripts including verified and
unavailable fee provenance rendering, exact decimal text and BTC/ETH/SOL navigation.
The three calculation/evidence/comparison modules pass scoped mypy. Broader local
mypy also reports two pre-existing literal-type errors in unrelated read-model
branches; those lines are unchanged. Broad checks and disposable PostgreSQL
integration are delegated to the repository CI.

## Bounded robustness subset and execution disposition — 2026-10-23

Rechecked at base `dfd4bc5` using a READ ONLY / REPEATABLE READ transaction
against the named development ledger: nine studies, nine frozen sets and nine
reruns. All 90 full study/content, frozen-set, trial/content/result and rerun
identities and all 360 baseline/5s/30s/60s thresholds match the assessment above. No market bars or
holdout were queried. The exact development dataset metadata files for ETH and
BTC exist locally; their Parquet bytes were not reverified in this inspection.

A deterministic **research triage rule**, chosen after inspecting development
results, retains every frozen candidate whose recorded 2-second Decimal gross
threshold is strictly above the existing **proposed** 5 bps/side scenario. It
retains six of 90, ordered by study UUID then trial UUID, with no top-N selection.
This is an exploratory subset subject to selection bias, not a profitability
ranking, cost approval or preregistration. Fees, spread and funding are excluded.
Keep all six, including the lag failure below; do not reselect after seeing folds.
Study/frozen-set/rerun hashes are pinned in the source table above.

| Study UUID | Trial UUID | Baseline bps/side | 5s / 30s / 60s bps/side |
| --- | --- | --- | --- |
| `566bdfb8-301b-544c-93ce-ea6c9cfb5d77` | `e275f278-35dc-5553-926f-b727ea45ed48` | 6.541229107968154677 | 6.541229107968154677 / 6.541229107968154677 / 7.378693862343957352 |
| `7bcff171-17eb-5f32-a1d3-b7e93f406d20` | `01e0595f-8d61-5966-888f-7ab5b10d8b3d` | 6.326579663852785380 | 6.326579663852785380 / 6.326579663852785380 / 5.444871635558185191 |
| `7bcff171-17eb-5f32-a1d3-b7e93f406d20` | `1c68e620-60ec-54e0-8628-c0c20c2afce3` | 5.325382362125764218 | 5.325382362125764218 / 5.325382362125764218 / 4.851656303942644375 |
| `c2af1fd3-efd6-50b1-b66a-f3309586f107` | `71473077-062a-5ab7-a716-aa1b0d90a8fd` | 6.621815776814796620 | 6.621815776814796620 / 6.621815776814796620 / 5.794619185282964714 |
| `c2af1fd3-efd6-50b1-b66a-f3309586f107` | `ba88bde8-b2e9-57cf-a3d7-201cf4d27cbc` | 6.735103397980238525 | 6.735103397980238525 / 6.735103397980238525 / 6.911843853348793386 |
| `c2af1fd3-efd6-50b1-b66a-f3309586f107` | `da93b287-fe0c-5d9f-a370-7197f0a360d0` | 6.476926043049263241 | 6.476926043049263241 / 6.476926043049263241 / 7.449786152921295759 |

The ETH trend trial `1c68e620-60ec-54e0-8628-c0c20c2afce3` drops below
5 bps at 60 seconds (4.851656303942644375). Five of the six remain above 5
across all four recorded lags; this is timing sensitivity, not OOS stability.
There are no fold results, fold exclusions or train-selected identities to report
because no walk-forward run occurred. No favorable fold was substituted.

The other 84 candidates remain visible: 18 nonpositive gross thresholds; 55
positive thresholds at or below 2.5 bps; 11 above 2.5 and at or below 5 bps.
All mean-reversion families top out near 1.05 bps. BTC breakout is entirely
negative; SOL breakout has eight nonpositive and two small positive thresholds.
SOL trend has four above 2.5 but none above 5; its absence from the six is the
explicit rule's result, not a conclusion that SOL cannot work. Development
windows differ by symbol and do not support cross-symbol performance ranking.

Reproduce the subset with the SQL above, retaining only
`proposed_scenario = 'conservative_medium_5bps'` and
`recorded_threshold_bps_per_side::numeric > 5`, ordered by study/trial UUID.
For lag sensitivity join **all** results of each same pinned rerun/trial at
`lag_micros IN (2000000,5000000,30000000,60000000)`; preserve absent or refused
results as unavailable, never as zero. Do not replace the pinned source snapshot
with a later favorable rerun.

### Exact walk-forward blocker and reproducible development-only path

The read-only recheck returned `alembic_version = 20261009_0062` and
`to_regclass('strategy_lab_walk_forward_reports') = NULL`. The existing authority
requires migration `20261016_0063`. This is a schema blocker before execution;
no execution was attempted, no existing database was migrated, and no fixture
was substituted. Available host RAM was about 2.27 GiB at inspection; that is
not proof a full study fits beside capture. No new database or host service was
started. An authorized, migrated development clone with an explicit resource
budget was not established in this sprint.

Use a separate development host/isolated clone, never the paper authority or an
in-place upgrade of this ledger. The operator must create the clone by the
existing backup/restore procedure, verify the pinned study/set/rerun identities,
and apply existing migrations there under the deployment process. Before
`python -m alembic current` / `python -m alembic upgrade head`, ensure
`POSTGRES_TEST_DSN`, `POSTGRES_DSN`, `DATABASE_URL` resolve only to that disposable
clone: Alembic prefers them in that order and does **not** read
`TRADE_PLATFORM_RESEARCH_DSN`. Do not print their values. Check resulting revision
against `python -m alembic heads` and confirm the reports table exists.

Install `.[dev,analytics]` in an isolated environment; copy only the bound
pre-holdout dataset objects/manifests with identity verification. The exact ETH
window is `da5bca46-8e14-506e-96db-44c039cfd7f2`, content hash
`9d465533eb2e7e39ce15fac9d774b663f8f98f360bda8bb9e7ebf68cd2a373d2`,
2025-08-20..2026-08-18 (524,160 bars). BTC uses
`b97e3889-f21e-5098-be63-b29fde3e69ad`, hash
`a2679be654469b451a6ee42c222bbfbee2b9804359ce073a6de51935d5f6bfcc`,
2025-11-28..2026-08-18 (380,160 bars). Both declare exclusive knowledge bound
`2026-08-19T00:01:00.000001+00:00`; study end is
`2026-08-20T00:00:00+00:00`. Verify bytes with the existing dataset loader before
execution; stop if missing or changed. Do not acquire missing holdout days.

One bounded first execution is the **existing ETH breakout study**, 84 planned
trials, two non-overlapping seven-day test folds, 30-day rolling training and
one-day embargo. These are recorded research experiment choices, not financial
owner decisions. The selection metric is train-side gross `total_return`, higher
is better, with trial-content-hash ascending ties. The existing implementation
purges label availability at the embargo edge, censors unavailable test labels,
and clips input by knowledge time before computing targets. It selects from
all original 84 trials, **not just the six exploratory candidates**; changing the
study to those six would change identity and the multiple-testing denominator.
Thus this tests the existing family's selection procedure, not independent
validation of the selected six. No new family or parameter sweep is introduced.

With the clone DSN injected as `TRADE_PLATFORM_RESEARCH_DSN`, run from the reviewed
repository root (PowerShell; replace only the verified development data root):

```powershell
python scripts/strategy_lab.py walk-forward --family breakout_channel --dataset da5bca46-8e14-506e-96db-44c039cfd7f2 --data-root <VERIFIED_DEV_DATA_ROOT> --mode ROLLING --train-duration 30d --test-duration 7d --step 7d --embargo 1d --first-test-start 2026-07-01T00:00:00Z --evaluation-end 2026-07-15T00:00:00Z --selection-metric total_return --selection-direction HIGHER_IS_BETTER
```

This CLI runs walk-forward serially; `--workers` does not bound this path. Budget
one process with an external wall-clock/RAM limit on the isolated host; retain a
resource-limit failure as unavailable and stop rather than expand the campaign.
`build_study` must reproduce `566bdfb8-301b-544c-93ce-ea6c9cfb5d77`; the existing
ledger check rejects a different declaration. Save stdout/stderr/exit code and
read the persisted full report by its returned `report_hash`: split/observation
hashes, plan/rule, all fold statuses, selected trial identities, purge/censor
counts, ineligible trials, day-gap flags, ties and every test metric. CLI output
alone omits some identity details. Report both folds even if negative or
`EXCLUDED_NO_ELIGIBLE_TRIAL_ON_TRAIN`; a refused empty split is a failed run,
not a fold to silently remove. Do not aggregate only favorable folds.

Every result remains `WALK_FORWARD_DEVELOPMENT`, `SEARCH_NON_AUTHORITATIVE`,
gross and development-only. It is neither Decimal net returns nor independent
validation. Holdout opening and paper promotion remain unauthorized.

### Read-only local export without a database clone

The existing plan above can now run with `walk-forward --read-only --export-dir
<LOCAL_EXPORT_DIRECTORY> --workers 1` added to that exact command. Inject the
existing research connection via `TRADE_PLATFORM_RESEARCH_DSN`. This alternative
does not need migration 0063: it reads the registered study ID/content hash/trial
count in a server-enforced READ ONLY / REPEATABLE READ transaction and never
accesses `strategy_lab_walk_forward_reports`. No clone, migration, registration,
queue transition or database write is performed. The normal persisted command
is unchanged and still needs its documented migrated disposable environment.

Study construction and the existing dataset loader reverify the dataset UUID,
identity hash, manifest, object bytes and logical rows. Read-only export repeats
the byte proof even if the SDK has cached bars. The existing `run_walk_forward_v1`
is called unchanged for every planned trial; no exploratory candidate filtering
or test-side reselection is added.

The output directory receives `<export_hash>.json`, containing the canonical
`strategy-lab-walk-forward-export-v1` envelope. It retains the complete original
`report_identity` and `report_hash` unchanged, the study identity, verified dataset
identity, declared plan/rule and registration status `NOT_REGISTERED`. It is
`WALK_FORWARD_DEVELOPMENT` / `SEARCH_NON_AUTHORITATIVE` / `GROSS_NON_PROMOTABLE`,
with `promotable: false`. A content hash proves reproducibility, not PostgreSQL
registration or financial authority. All original folds, exclusions, selected
identities, ties, purge/censor counts and test metrics are retained.

Independently reverify with the existing helpers:

```python
import json
from pathlib import Path
from trade_platform.strategy_lab_study_v1 import canonical_identity_json_v1, identity_hash_v1

path = Path("<LOCAL_EXPORT_DIRECTORY>/<export_hash>.json")
raw = path.read_bytes()
envelope = json.loads(raw)
assert raw == canonical_identity_json_v1(envelope).encode("ascii")
assert identity_hash_v1(envelope) == path.stem
if envelope["outcome"] == "CALCULATED":
    assert identity_hash_v1(envelope["report_identity"]) == envelope["report_hash"]
```

Missing/mismatched evidence or unavailable splits export `UNAVAILABLE`; a Python
`MemoryError` exports `RESOURCE_LIMITED`. Both have null report/hash and no partial
total; CLI exit is nonzero. Failure during initial study construction/connection
or an external process termination may leave no export: retain stderr/exit code
and the preflight receipt, never treat file absence as success. Existing files
are byte-compared, never silently overwritten. Use one fresh process under an
external RAM and wall-clock limit; `--workers 1` does not itself enforce either.

On 2026-10-24 (Europe/Brussels), a read-only source query reconfirmed schema 0062,
the exact study `566bdfb8-301b-544c-93ce-ea6c9cfb5d77`, hash
`451c1ac7c536e8c1c39cd3a97d64777498555487849e511e1c40ba8df8538d8e`
and 84 planned trials. The streaming existing loader reverified the exact ETH
dataset/hash above, 524,160 rows and manifest
`19051e6cb15f524a0e517a60f0af94fe541211836d11e55abeee2c0d65e81c6a`
in 14.422 seconds. All source days precede the untouched holdout boundary.

No real evaluation was started: only 2,448 MiB RAM was available, with Windows
commit 27,647,725,568 / 32,830,644,224 bytes (84%); WSL/Docker and browser workloads
were competing. The eager full-window loader retains Python rows, Decimal OHLC
and NumPy arrays, then the engine constructs observation/split objects. A
conservative first-run admission budget is a 2 GiB process cap plus at least
1 GiB free host reserve (3 GiB available RAM), at least 4 GiB commit headroom and
an external 30-minute wall-clock cap. This is an engineering safety budget, not
a measured peak or a guarantee the run fits. Current RAM fails that budget;
no unsafe attempt, favorable subset or repeated retry was made. Fold outcomes
and selected trials therefore remain unavailable. The old schema no longer
blocks this read-only path; resource headroom remains the execution prerequisite.
