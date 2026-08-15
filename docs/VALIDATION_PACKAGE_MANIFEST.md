# Validation Package Manifest

Status: P0 integrity contract, manifest version validation-package-manifest-v1.

## Purpose

A validation package is an immutable, content-addressed research evidence
manifest. It may make a strategy eligible for human review, but it cannot
activate a strategy, change risk, create an order, or enable live trading.

PostgreSQL stores the exact canonical manifest text. Recovery must verify that
text; it must never reconstruct the hashed object from relational display
labels.

## Canonical serialization

Manifest v1 is UTF-8 JSON with:

- Unicode preserved rather than ASCII-escaped;
- object keys sorted lexicographically;
- no insignificant whitespace;
- UUID and Decimal values encoded as strings;
- timezone-aware datetimes encoded with ISO 8601;
- array order preserved.

The content hash is the lower-case hexadecimal SHA-256 digest of the exact
stored UTF-8 manifest text.

The package ID is a deterministic UUIDv5 over a SHA-256 semantic identity that
contains all manifest fields except package ID and evaluation time. Evaluation
time remains inside the hashed manifest. A retry must reuse the same package
object; rebuilding the same semantic package at another evaluation time is a
conflict, not a silently different proof.

## Required manifest fields

- manifest version and package ID;
- strategy definition ID, strategy-version ID, and semantic version;
- dataset ID, dataset-version ID, and semantic version;
- ordered feature versions;
- cost-model version;
- evidence type mapped to artifact ID and artifact content hash;
- limitations;
- timezone-aware evaluation timestamp;
- promotion eligibility state;
- versioned validation metadata.

Required evidence types are enforced by the quantitative validation domain.
Evidence IDs and hashes must have identical key sets, and every hash must match
the immutable PostgreSQL artifact row.

## Persistence invariants

- A VERIFIED row has a supported manifest version and canonical manifest.
- PostgreSQL verifies SHA-256(manifest) through a database constraint.
- Package rows and evidence-membership rows reject update and delete.
- Strategy, dataset, and version IDs must match explicit repository mappings.
- Relational projections must equal their canonical manifest fields.
- Reads verify canonical serialization, content hash, semantic package ID,
  artifact membership, artifact hashes, and relational projections.
- An identical duplicate package ID is idempotent.
- Any conflicting duplicate fails closed.
- Rows created before manifest v1 are LEGACY_UNVERIFIABLE and cannot be read as
  validated packages or used for promotion.

## Migration and threat boundary

The migration does not invent canonical bytes for legacy rows. This preserves
historical data without falsely certifying it.

SHA-256 plus immutable database triggers detects accidental corruption and
ordinary application mutation. It does not defend against an administrator who
can rewrite data, triggers, and hashes. If that threat enters scope, add a
separately controlled signature or WORM/object-lock copy; do not change the
canonical manifest format silently.

