"""Module 3J.2b.1 -- composite feature + tradable-bar research evidence bridge.

An immutable, in-memory-only bridge artifact combining exactly one
``SubjectAwareResearchFeatureBundle`` (3J.2a) with exactly one
``AuthoritativeTradableBarSeriesV2`` (this module's own dataset-bound
tradable-bar reader). It exists so a v1 crypto-perpetual strategy can consume
feature evidence and tradable-price evidence under one asserted, exactly
matching ``dataset_version_id`` -- never two separately-trusted evidence
sources that merely claim to agree.

Equality is exact UUID identity only, never string-label equivalence: the
bundle's ``dataset_version_id`` (a ``UUID``) must equal the bar series'
``dataset_version_id`` (also a ``UUID``) by ``==``, and the bundle's
``subject_id`` must equal the bar series' ``instrument_id`` by ``==``. No
cross-dataset evidence is permitted, and the feature bundle's subject must be
``FeatureSubjectType.INSTRUMENT`` -- ``FUTURES_SERIES`` is out of scope for
this v1 bridge (a crypto perpetual's feature subject and tradable instrument
are the same object; a futures series' roll/mapping authority does not exist
yet, per the 3J.2b proposal Sec 1).

No durable table is created and no financial value is copied a second time:
the content hash covers only canonical identities and upstream content
hashes, exactly as ``SubjectAwareResearchFeatureBundle.create()`` already
does for its own feature evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .strategy_feature_binding_v2 import FeatureSubjectType, SubjectAwareResearchFeatureBundle
from .tradable_bar_evidence_v2 import AuthoritativeTradableBarSeriesV2


class TradableResearchEvidenceV2Error(ValueError):
    """Raised when a feature bundle and a bar series cannot be combined safely."""


@dataclass(frozen=True, slots=True)
class SubjectAwareTradableResearchEvidenceV2:
    """One feature bundle plus one tradable-bar series, proven to share identity."""

    feature_bundle: SubjectAwareResearchFeatureBundle
    bar_series: AuthoritativeTradableBarSeriesV2
    content_hash: str

    @classmethod
    def create(
        cls,
        *,
        feature_bundle: SubjectAwareResearchFeatureBundle,
        bar_series: AuthoritativeTradableBarSeriesV2,
    ) -> SubjectAwareTradableResearchEvidenceV2:
        # Re-invoke each upstream artifact's OWN authoritative validation
        # first -- a directly-constructed, malformed bundle or series must not
        # be composable merely because its dataset/instrument strings happen
        # to match. This module never re-implements those checks; it only
        # calls them, so a change to either authority's invariants is picked
        # up here automatically.
        feature_bundle.validate()
        bar_series.validate()
        _validate_pairing(feature_bundle, bar_series)
        payload = {
            "feature_bundle_id": str(feature_bundle.bundle_id),
            "feature_bundle_content_hash": feature_bundle.content_hash,
            "dataset_version_id": str(feature_bundle.dataset_version_id),
            "subject_id": feature_bundle.subject_id,
            "instrument_id": bar_series.instrument_id,
            "interval": bar_series.interval,
            "bars": [
                {
                    # Canonical immutable provenance, not merely references:
                    # dataset_content_hash/raw_payload_sha256 tie each bar to
                    # the exact sealed evidence it was projected from, so the
                    # composite hash commits to the underlying financial
                    # content transitively (via the raw payload's own SHA-256)
                    # without copying any OHLCV value into this payload.
                    "dataset_content_hash": bar.dataset_content_hash,
                    "source_id": str(bar.source_id),
                    "normalized_observation_id": str(bar.normalized_observation_id),
                    "raw_observation_id": str(bar.raw_observation_id),
                    "raw_payload_sha256": bar.raw_payload_sha256,
                    "bar_open_at": bar.bar_open_at.isoformat(),
                    "bar_close_at": bar.bar_close_at.isoformat(),
                    "revision": bar.revision,
                }
                for bar in bar_series.bars
            ],
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(feature_bundle, bar_series, content_hash)


def _validate_pairing(
    feature_bundle: SubjectAwareResearchFeatureBundle,
    bar_series: AuthoritativeTradableBarSeriesV2,
) -> None:
    if feature_bundle.subject_type is not FeatureSubjectType.INSTRUMENT:
        raise TradableResearchEvidenceV2Error("tradable_research_evidence_requires_instrument_subject")
    if feature_bundle.dataset_version_id != bar_series.dataset_version_id:
        raise TradableResearchEvidenceV2Error("tradable_research_evidence_dataset_mismatch")
    if feature_bundle.subject_id != bar_series.instrument_id:
        raise TradableResearchEvidenceV2Error("tradable_research_evidence_instrument_mismatch")
