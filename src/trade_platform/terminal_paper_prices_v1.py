"""Server-installed, content-addressed price selections; read-only and without defaults.

``TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_PATH`` names a JSON policy and
``TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_HASH`` pins its canonical identity hash.
The identity has schema_version ``terminal-paper-price-selection-v1``, a nonempty
policy_reference, an aware declared_at, and selections. Each selection declares
cycle_id/study_id/trial_id, entry_fill_hash, symbol, source_id/session_id/instrument,
instant, max_age_micros and capture_max_unproven_nanos. No price is accepted.

The server installation instant is an additional knowledge bound: a policy file
cannot assert that it was available historically. Roots must be explicitly set
through TRADE_PLATFORM_RESEARCH_DATA_ROOT and
TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT. No configuration is created here.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from .strategy_lab_study_v1 import identity_hash_v1


def _instant(value: Any) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("PRICE_SELECTION_INSTANT_MUST_BE_AWARE")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class _UnavailablePriceV1:
    reasons: tuple[str, ...]
    status: str = "UNAVAILABLE"
    mark: None = None

    def payload(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons), "mark": None, "evidence": None}


class TerminalPaperPricesV1:
    """Pinned configuration, installed now; only the existing sealed resolver issues marks."""

    def __init__(self, identity: Mapping[str, Any], *, expected_hash: str,
                 research_root: Path | None, capture_root: Path | None) -> None:
        self._identity = json.dumps(dict(identity), sort_keys=True, separators=(",", ":"))
        self._expected_hash = expected_hash
        self._research_root = research_root
        self._capture_root = capture_root
        self.installed_at = datetime.now(UTC)

    def resolve(self, cursor: Any, *, cycle_id: str, study_id: str, trial_id: str,
                entry: Mapping[str, Any], as_of: datetime) -> Any:
        def refused(reason: str) -> Any:
            return _UnavailablePriceV1((reason,))

        try:
            policy = json.loads(self._identity)
            if identity_hash_v1(policy) != self._expected_hash:
                return refused("PRICE_SELECTION_POLICY_HASH_MISMATCH")
            if (policy["schema_version"] != "terminal-paper-price-selection-v1"
                    or not isinstance(policy["policy_reference"], str) or not policy["policy_reference"].strip()):
                return refused("PRICE_SELECTION_POLICY_PROVENANCE_MISSING")
            declared_at = _instant(policy["declared_at"])
            if max(declared_at, self.installed_at) > as_of:
                return refused("PRICE_SELECTION_POLICY_NOT_KNOWN_BY_AS_OF")
            selections = [item for item in policy["selections"]
                          if (item["cycle_id"], item["study_id"], item["trial_id"]) ==
                          (cycle_id, study_id, trial_id)]
            if len(selections) != 1:
                return refused("NO_UNIQUE_CANDIDATE_PRICE_SELECTION")
            selected = selections[0]
            entry_hash = identity_hash_v1(dict(entry))
            if ((entry["study_id"], entry["trial_id"], entry["symbol"]) !=
                    (study_id, trial_id, selected["symbol"]) or selected["entry_fill_hash"] != entry_hash):
                return refused("PRICE_SELECTION_STORED_ENTRY_MISMATCH")
            if (entry.get("evidence") or {}).get("session_id") != selected["session_id"]:
                return refused("PRICE_SELECTION_STORED_ENTRY_SESSION_MISMATCH")
            for name in ("max_age_micros", "capture_max_unproven_nanos"):
                if isinstance(selected[name], bool) or not isinstance(selected[name], int):
                    return refused(f"PRICE_SELECTION_{name.upper()}_MUST_BE_INTEGER")
            if self._research_root is None or self._capture_root is None:
                return refused("PROVEN_PRICE_SOURCE_NOT_CONFIGURED")
            from .paper_evidence_production_v1 import (
                EvidenceInputRefusedV1,
                PaperPriceDeclarationV1,
                PostgresPaperEvidenceSourcesV1,
            )
            from .research_data_plane_v1 import ResearchFrameStoreV1

            declaration = PaperPriceDeclarationV1(
                UUID(selected["source_id"]), UUID(selected["session_id"]), selected["instrument"],
                _instant(selected["instant"]), selected["max_age_micros"], selected["capture_max_unproven_nanos"])
            from .paper_evidence_store_v1 import _cycle_holdout_start, _registered_opening_end

            holdout_start = _cycle_holdout_start(cursor, cycle_id)
            holdout_end = _registered_opening_end(cursor, cycle_id, as_of)
            if declaration.instant >= holdout_start and (holdout_end is None or declaration.instant < holdout_end):
                return refused("EVIDENCE_TOUCHES_THE_UNOPENED_HOLDOUT" if holdout_end is None
                               else "EVIDENCE_TOUCHES_THE_OPENED_HOLDOUT_SPAN")

            def guard(catalogued: Any) -> None:
                if holdout_end is not None:
                    return  # an opening known by as_of is the existing permission to access holdout evidence
                identity = catalogued.identity
                try:
                    if identity_hash_v1(dict(identity)) != catalogued.content_hash:
                        raise ValueError("identity_hash")
                    # Replay can scan entire source partitions and a slice's parent.
                    # Host arrival alone proves no venue bound: require the seal's
                    # explicit venue-minus-host upper bound and knowledge envelope.
                    bound = identity["clock_evidence"]["max_venue_minus_host_upper_nanos"]
                    last = identity["knowledge_span"]["last_micros"]
                    boundary = (holdout_start - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)
                    if not isinstance(bound, int) or not isinstance(last, int) or last >= boundary:
                        raise ValueError("knowledge_span")
                    partitions = identity["partitions"]
                    if not partitions:
                        raise ValueError("partitions_missing")
                    for partition in partitions:
                        day_end = _instant(partition["utc_day"] + "T00:00:00+00:00") + timedelta(days=1)
                        end_nanos = ((day_end - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1)) * 1000
                        if end_nanos + max(0, bound) >= boundary * 1000:
                            raise ValueError("partition_reaches_holdout")
                    if "replay_slice_proof" in identity:
                        raise ValueError("slice_parent_raw_scope_not_independently_bounded")
                except (KeyError, TypeError, ValueError):
                    raise EvidenceInputRefusedV1("PRICE_REPLAY_SCOPE_TOUCHES_UNOPENED_HOLDOUT_OR_UNPROVEN") from None

            result = PostgresPaperEvidenceSourcesV1(
                cursor, frame_store=ResearchFrameStoreV1(self._research_root), capture_root=self._capture_root,
                funding_root=None, catalogue_seal_guard=guard).paper_trade_price(
                    symbol=entry["symbol"], declaration=declaration,
                    entry_bar_open_micros=entry["fill_bar_open_micros"], as_of=as_of)
            # Preserve resolver evidence unchanged (the mark hashes it), keeping policy provenance beside it.
            return result
        except ImportError:
            return refused("PROVEN_PRICE_RUNTIME_DEPENDENCIES_UNAVAILABLE")
        except (ValueError, TypeError, KeyError, AttributeError):
            return refused("PRICE_SELECTION_POLICY_MALFORMED")

    def provenance(self) -> dict[str, Any]:
        return {"policy_hash": self._expected_hash, "installed_at": self.installed_at.isoformat(),
                "identity": json.loads(self._identity)}


def terminal_paper_prices_from_environment_v1() -> TerminalPaperPricesV1 | None:
    """Optional pinned configuration; malformed configured files fail startup closed."""
    path = os.environ.get("TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_PATH")
    expected = os.environ.get("TRADE_PLATFORM_TERMINAL_PRICE_SELECTION_HASH")
    if not path and not expected:
        return None
    if not path or not expected:
        raise ValueError("PRICE_SELECTION_PATH_AND_HASH_REQUIRED")
    identity = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(identity, dict) or identity_hash_v1(identity) != expected:
        raise ValueError("PRICE_SELECTION_POLICY_HASH_MISMATCH")
    research = os.environ.get("TRADE_PLATFORM_RESEARCH_DATA_ROOT")
    capture = os.environ.get("TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT")
    return TerminalPaperPricesV1(identity, expected_hash=expected,
                                research_root=Path(research) if research else None,
                                capture_root=Path(capture) if capture else None)
