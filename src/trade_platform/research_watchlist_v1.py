"""Phase R8.2 -- the owner's research watch list (OR-9), recorded, never chosen here.

``RESEARCH_ONLY``. Which frozen candidates are watched live is owner decision
OR-9. Until now the only way to watch was to name one ESTABLISHED Decimal rerun
on the command line, which watched its whole top-k selection. A
:class:`ResearchWatchlistV1` records the owner's explicit choice instead: a
content-addressed list of ``(study, rerun, trial)`` entries, each of which must be
in the Decimal-authoritative selection of an ``ESTABLISHED`` rerun of that study
and must not carry a recorded R6 state (an incubating candidate is incubated, a
rejected one is never watched).

Nothing here has a default. An empty list is ``MISSING_OWNER_WATCH_SELECTION_OR_9``,
a list without ``approved_by``/``approved_on`` is ``MISSING_OWNER_APPROVAL_OR_9``,
and either keeps the version ``DRAFT``. Only an ``ACTIVE`` version is watched.
Versions are append-only (migration 20261009_0061); the latest ACTIVE version of
a watch list is the one in force. Watching never lifts authority: every signal
stays ``NOT_VALIDATED_RESEARCH_WATCH``, and no forward bar of the current cycle
is evaluated until its holdout is opened (OR-7).

Import-light on purpose (no numpy/pyarrow): the protected API records and reads
watch lists through this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final
from uuid import UUID

from .persistence import PostgresDatabase
from .strategy_lab_study_v1 import identity_hash_v1

WATCHLIST_SCHEMA_VERSION_V1: Final = "research-watchlist-v1"
STATUS_ACTIVE: Final = "ACTIVE"
STATUS_DRAFT: Final = "DRAFT"
UNRESOLVED_SELECTION: Final = "MISSING_OWNER_WATCH_SELECTION_OR_9"
UNRESOLVED_APPROVAL: Final = "MISSING_OWNER_APPROVAL_OR_9"
#: A small initial watch set is the point of OR-9; a larger list is refused, not truncated.
MAX_WATCH_ENTRIES_V1: Final = 30

_SLUG: Final = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_HASH: Final = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL: Final = re.compile(r"^[A-Z0-9]{2,20}$")


class ResearchWatchlistError(ValueError):
    """Raised when a watch list is malformed or names a candidate it may not watch."""


@dataclass(frozen=True, slots=True)
class WatchEntryV1:
    study_id: UUID
    rerun_hash: str
    trial_id: UUID
    symbol: str

    def __post_init__(self) -> None:
        if not isinstance(self.study_id, UUID) or not isinstance(self.trial_id, UUID):
            raise ResearchWatchlistError("watch_entry_ids_must_be_uuids")
        if not _HASH.match(self.rerun_hash):
            raise ResearchWatchlistError("watch_entry_rerun_hash_malformed")
        if not _SYMBOL.match(self.symbol):
            raise ResearchWatchlistError("watch_entry_symbol_malformed")

    def payload(self) -> dict[str, str]:
        return {"study_id": str(self.study_id), "rerun_hash": self.rerun_hash, "trial_id": str(self.trial_id),
                "symbol": self.symbol}

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> WatchEntryV1:
        try:
            return cls(UUID(str(raw["study_id"])), str(raw["rerun_hash"]), UUID(str(raw["trial_id"])),
                       str(raw["symbol"]))
        except (KeyError, ValueError, TypeError) as error:
            raise ResearchWatchlistError("watch_entry_malformed") from error


@dataclass(frozen=True)
class ResearchWatchlistV1:
    watchlist_id: str
    entries: tuple[WatchEntryV1, ...] = ()
    approved_by: str | None = None
    approved_on: str | None = None
    note: str = ""
    _sorted: tuple[WatchEntryV1, ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        if not _SLUG.match(self.watchlist_id):
            raise ResearchWatchlistError("watchlist_id_must_be_a_lowercase_slug")
        if not all(isinstance(entry, WatchEntryV1) for entry in self.entries):
            raise ResearchWatchlistError("watchlist_entries_must_be_watch_entries")
        keys = [(e.study_id, e.trial_id) for e in self.entries]
        if len(set(keys)) != len(keys):
            raise ResearchWatchlistError("watchlist_repeats_a_candidate")
        if len(self.entries) > MAX_WATCH_ENTRIES_V1:
            raise ResearchWatchlistError("watchlist_too_large_choose_a_small_initial_set")
        if self.approved_by is not None and not self.approved_by.strip():
            raise ResearchWatchlistError("approved_by_must_be_nonblank")
        if self.approved_on is not None:
            date.fromisoformat(self.approved_on)
        ordered = tuple(sorted(self.entries, key=lambda e: (str(e.study_id), str(e.trial_id))))
        object.__setattr__(self, "_sorted", ordered)

    @property
    def unresolved(self) -> tuple[str, ...]:
        reasons = []
        if not self.entries:
            reasons.append(UNRESOLVED_SELECTION)
        if not (self.approved_by or "").strip() or not self.approved_on:
            reasons.append(UNRESOLVED_APPROVAL)
        return tuple(reasons)

    @property
    def status(self) -> str:
        return STATUS_ACTIVE if not self.unresolved else STATUS_DRAFT

    def identity(self) -> dict[str, Any]:
        return {
            "schema_version": WATCHLIST_SCHEMA_VERSION_V1, "watchlist_id": self.watchlist_id,
            "entries": [entry.payload() for entry in self._sorted],
            "approved_by": None if self.approved_by is None else self.approved_by.strip(),
            "approved_on": self.approved_on, "note": self.note.strip(),
            "authority": "NOT_VALIDATED_RESEARCH_WATCH",
            "status": self.status, "unresolved": list(self.unresolved),
        }

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    @classmethod
    def from_identity(cls, identity: Mapping[str, Any]) -> ResearchWatchlistV1:
        watchlist = cls(str(identity["watchlist_id"]),
                        tuple(WatchEntryV1.from_payload(item) for item in identity["entries"]),
                        identity.get("approved_by"), identity.get("approved_on"), str(identity.get("note", "")))
        if watchlist.identity() != dict(identity):
            raise ResearchWatchlistError("stored_watchlist_does_not_reproduce")
        return watchlist


def _selected_trials(rerun_identity: Mapping[str, Any]) -> set[str]:
    selection = rerun_identity.get("authoritative_selection", {})
    return {str(item["trial_id"]) for item in selection.get("selected", [])}


def check_watch_entries_v1(cursor: Any, entries: Sequence[WatchEntryV1]) -> list[str]:
    """Why each entry may not be watched (empty when all may). Reads the R4.7/R6 authorities."""
    problems: list[str] = []
    for entry in entries:
        cursor.execute("SELECT study_id, selection_status, identity FROM strategy_lab_authority_reruns "
                       "WHERE rerun_hash=%s", (entry.rerun_hash,))
        row = cursor.fetchone()
        tag = f"{entry.study_id}:{entry.trial_id}"
        if row is None:
            problems.append(f"WATCH_RERUN_NOT_FOUND:{tag}")
            continue
        identity = row[2] if isinstance(row[2], dict) else json.loads(row[2])
        if row[0] != entry.study_id:
            problems.append(f"WATCH_RERUN_IS_NOT_FROM_THIS_STUDY:{tag}")
        elif str(row[1]) != "ESTABLISHED":
            problems.append(f"WATCH_RERUN_SELECTION_NOT_ESTABLISHED:{tag}")
        elif str(entry.trial_id) not in _selected_trials(identity):
            problems.append(f"WATCH_TRIAL_NOT_IN_THE_DECIMAL_SELECTION:{tag}")
        cursor.execute("SELECT state FROM strategy_lab_candidate_states WHERE study_id=%s AND trial_id=%s "
                       "ORDER BY recorded_at DESC LIMIT 1", (entry.study_id, entry.trial_id))
        state = cursor.fetchone()
        if state is not None:
            problems.append(f"WATCH_TRIAL_HAS_R6_STATE_{state[0]}:{tag}")
    return problems


class PostgresResearchWatchlistStoreV1:
    """Append-only watch list versions (migration 20261009_0061)."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, watchlist: ResearchWatchlistV1) -> bool:
        """Record a version after re-checking every entry against the authorities. Idempotent."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            problems = check_watch_entries_v1(cursor, watchlist.entries)
            if problems:
                raise ResearchWatchlistError("watchlist_entries_refused:" + ",".join(problems))
            cursor.execute(
                "INSERT INTO research_watchlist_versions (content_hash, watchlist_id, status, identity, recorded_at) "
                "VALUES (%s,%s,%s,%s::jsonb,%s) ON CONFLICT (content_hash) DO NOTHING RETURNING content_hash",
                (watchlist.content_hash, watchlist.watchlist_id, watchlist.status,
                 json.dumps(watchlist.identity(), sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def latest_active(self, watchlist_id: str) -> ResearchWatchlistV1 | None:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            return read_latest_active_watchlist_v1(cursor, watchlist_id)


def read_latest_active_watchlist_v1(cursor: Any, watchlist_id: str | None = None) -> ResearchWatchlistV1 | None:
    """The newest ACTIVE version (of one list, or of any), re-derived from its stored identity."""
    if watchlist_id is None:
        cursor.execute("SELECT content_hash, identity FROM research_watchlist_versions WHERE status='ACTIVE' "
                       "ORDER BY recorded_at DESC, content_hash LIMIT 1")
    else:
        cursor.execute("SELECT content_hash, identity FROM research_watchlist_versions WHERE status='ACTIVE' "
                       "AND watchlist_id=%s ORDER BY recorded_at DESC, content_hash LIMIT 1", (watchlist_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    identity = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    watchlist = ResearchWatchlistV1.from_identity(identity)
    if watchlist.content_hash != str(row[0]).strip():
        raise ResearchWatchlistError("stored_watchlist_hash_does_not_rederive")
    return watchlist


__all__ = [
    "MAX_WATCH_ENTRIES_V1",
    "STATUS_ACTIVE",
    "STATUS_DRAFT",
    "UNRESOLVED_APPROVAL",
    "UNRESOLVED_SELECTION",
    "PostgresResearchWatchlistStoreV1",
    "ResearchWatchlistError",
    "ResearchWatchlistV1",
    "WatchEntryV1",
    "check_watch_entries_v1",
    "read_latest_active_watchlist_v1",
]
