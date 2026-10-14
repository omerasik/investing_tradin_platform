"""Off-host backup of finalized capture partitions, and dead-man's-switch pings.

Standard library only, beside the unchanged capture CLI (it imports the same
``trade_platform`` archive code from the release). Public market data only: no
exchange, broker or account credential is involved anywhere here.

``backup`` (trade-capture-backup.timer, hourly at HH:25 UTC) uploads every
COMPLETE, already compacted partition under the configured roots as one
deterministic tar object to an OCI Object Storage bucket through a bucket-level
*write-only* pre-authenticated request (PAR). It reads the archive and never
writes it; a partition that is OPEN, PARTIAL or not yet compacted is never
uploaded (a COMPLETE one left uncompacted for hours is reported, exit 1), and
neither is one that does not sit at the canonical path of its own contract, day
and session under an approved root label (``r0`` holds only the production
contract, ``u2`` only the universe contracts). Each PUT carries ``Content-MD5``
(the service verifies the body) and ``If-None-Match: *`` (an existing object is
never overwritten). Outcomes go to an append-only ledger in the unit's state
directory, so a run only sends what is new, and at most ``--max-uploads``
objects.

Three states are kept apart and never promoted into one another:

* ``UPLOADED`` -- this client created the object and the service accepted its
  ``Content-MD5``. The bytes arrived; nobody has restored them yet.
* ``REMOTE_EXISTS_UNVERIFIED`` -- the PUT met an existing object (409/412). The
  write-only PAR cannot read it, so its content is unknown: it is never counted
  as backed up, it is not re-sent (idempotency), and the run reports ATTENTION
  while it is inside the budget window.
* ``RESTORE_VERIFIED`` -- ``accept-restore`` matched a ``restore-verify``
  receipt from another host: the object was downloaded, its SHA-256 equals the
  deterministic tar of the local partition, and it replayed in full. Only this
  state says the data is recoverable off-host.

The bucket's size is bounded without listing it: bytes recorded in the ledger
over the last ``--budget-window-days`` (the bucket's lifecycle age plus slack
for a late lifecycle run) estimate what the bucket still holds, and an upload
that would take that estimate past ``--budget-bytes`` is not attempted (exit 1).
A partition finalized more than ``--horizon-days`` ago is never uploaded, so a
lost ledger cannot re-send history the lifecycle already deleted.

``ping success|fail`` posts to a healthchecks.io-compatible URL (``<url>`` for
success, ``<url>/fail`` for failure) with stdin as a short text body. Silence
is the alert: if the VM, the timer or the network dies, no success ping arrives
and the check's grace period fires.

``restore-verify`` runs on another host: it downloads the objects through a
*read* PAR with object listing, refuses any name outside an approved label and
the canonical partition layout, extracts each into an empty root under the path
its name states, fully replays it (:func:`verify_partition_v1`) and requires the
replayed partition's own contract, day and session to name exactly that path.
With ``--ledger`` (a copy of the VM's ledger) each object's SHA-256 must also
equal the one the VM recorded, which is what catches a substituted object whose
internal hashes were recomputed. ``--receipt`` writes what was verified for
``accept-restore`` on the VM. A backup nobody has restored is a hope, not a copy.

No URL ever reaches argv, a log line or an exception message: each is read from
a file and every error is reported as a status code or exception type only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trade_platform.first_party_capture_archive_v1 import (
    ARCHIVE_LAYOUT_VERSION,
    COMPACTED_RECORDS_FILE_NAME,
    COMPACTION_FILE_NAME,
    LIFECYCLE_FILE_NAME,
    MANIFEST_FILE_NAME,
    OPEN_MARKER_NAME,
    PARTITION_STATUS_COMPLETE,
    RECORDS_FILE_NAME,
    SESSION_FILE_NAME,
    FirstPartyCaptureArchiveError,
    find_partitions_v1,
    partition_directory,
    read_partition_status_v1,
    verify_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
    first_party_bybit_universe_contracts_v1,
    resolve_first_party_capture_contract_v1,
)

LEDGER_NAME = "uploaded-v1.ndjson"
#: A COMPLETE partition still uncompressed this long after finalizing means
#: compaction is failing; it is reported (exit 1) rather than uploaded raw.
STALE_UNCOMPACTED_SECONDS = 2 * 3600
DEFAULT_MAX_UPLOADS = 500
PING_BODY_LIMIT = 10_000
DAY_SECONDS = 86_400
#: Exactly the files a compacted partition holds (backup_partition_v1 copies the same).
PARTITION_FILES = frozenset(
    {SESSION_FILE_NAME, LIFECYCLE_FILE_NAME, MANIFEST_FILE_NAME, COMPACTED_RECORDS_FILE_NAME, COMPACTION_FILE_NAME}
)

UPLOADED = "UPLOADED"
REMOTE_EXISTS_UNVERIFIED = "REMOTE_EXISTS_UNVERIFIED"
RESTORE_VERIFIED = "RESTORE_VERIFIED"


def approved_root_labels_v1() -> Mapping[str, frozenset[str]]:
    """Root label -> the source ids of the only contracts its partitions may carry.

    ``r0`` is the ``supervise`` recorder (production contract), ``u2`` the
    ``universe`` recorder. No other label is ever written or restored.
    """
    return {
        "r0": frozenset({str(first_party_bybit_capture_contract_v1().source_id)}),
        "u2": frozenset(str(contract.source_id) for contract in first_party_bybit_universe_contracts_v1()),
    }


_LAYOUT_KEYS = ("exchange", "instrument", "date", "session")
_LAYOUT_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class OffsiteError(RuntimeError):
    """A configuration or transport failure; its message never contains a URL."""


def read_url_file(path: Path, *, par: bool) -> str:
    """The single https URL in ``path``; a PAR must address a bucket's objects (``.../o/``)."""
    try:
        url = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise OffsiteError(f"url_file_unreadable:{type(error).__name__}") from None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise OffsiteError("url_must_be_a_plain_https_url")
    if par and not ("/p/" in parsed.path and parsed.path.endswith("/o/")):
        raise OffsiteError("par_must_be_a_bucket_level_object_par_ending_in_/o/")
    return url


# -- transport -------------------------------------------------------------


class ObjectStoreV1(Protocol):
    def put_new(self, name: str, body: bytes) -> bool:
        """Create ``name``; ``False`` if something already holds it (never overwritten).

        ``False`` says nothing about the existing object's content: with a
        write-only PAR it cannot be read, so the caller must not treat it as a copy.
        """

    def get(self, name: str) -> bytes: ...

    def list_names(self, prefix: str) -> list[str]: ...


def _open(request: urllib.request.Request, timeout: float) -> bytes:
    # The scheme was checked to be https in read_url_file.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return response.read()


class ParObjectStoreV1:
    """OCI Object Storage through a bucket-level pre-authenticated request."""

    def __init__(self, par_url: str, *, timeout: float = 120.0) -> None:
        self._base = par_url
        self._timeout = timeout

    def _url(self, name: str) -> str:
        return self._base + urllib.parse.quote(name, safe="/=")

    def put_new(self, name: str, body: bytes) -> bool:
        digest = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode()
        request = urllib.request.Request(
            self._url(name),
            data=body,
            method="PUT",
            headers={
                "Content-Type": "application/x-tar",
                "Content-MD5": digest,
                "If-None-Match": "*",
            },
        )
        try:
            _open(request, self._timeout)
        except urllib.error.HTTPError as error:
            if error.code in (409, 412):
                return False
            raise OffsiteError(f"put_failed:http_{error.code}") from None
        except (urllib.error.URLError, OSError) as error:
            raise OffsiteError(f"put_failed:{type(error).__name__}") from None
        return True

    def get(self, name: str) -> bytes:
        try:
            return _open(urllib.request.Request(self._url(name)), self._timeout)
        except urllib.error.HTTPError as error:
            raise OffsiteError(f"get_failed:http_{error.code}") from None
        except (urllib.error.URLError, OSError) as error:
            raise OffsiteError(f"get_failed:{type(error).__name__}") from None

    def list_names(self, prefix: str) -> list[str]:
        names: list[str] = []
        start: str | None = None
        while True:
            query = {"prefix": prefix, "fields": "name"}
            if start is not None:
                query["start"] = start
            url = self._base + "?" + urllib.parse.urlencode(query)
            try:
                page = json.loads(_open(urllib.request.Request(url), self._timeout))
            except urllib.error.HTTPError as error:
                raise OffsiteError(f"list_failed:http_{error.code}") from None
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise OffsiteError(f"list_failed:{type(error).__name__}") from None
            names.extend(str(item["name"]) for item in page.get("objects", ()))
            start = page.get("nextStartWith")
            if not start:
                return sorted(names)


# -- backup ----------------------------------------------------------------


def partition_tar_bytes(directory: Path, relative: str) -> bytes:
    """A deterministic tar of one partition: sorted members, zero times and owners."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            if path.name not in PARTITION_FILES:
                raise OffsiteError(f"unexpected_partition_file:{path.name}")
            data = path.read_bytes()
            info = tarfile.TarInfo(f"{relative}/{path.name}")
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def object_name(label: str, relative: str) -> str:
    return f"{label}/{relative}.tar"


def parse_object_name(name: str) -> tuple[str, str]:
    """``(label, relative)`` of a canonical object name, else :class:`OffsiteError`.

    The only accepted shape is
    ``<label>/v1/exchange=<x>/instrument=<y>/date=<YYYY-MM-DD>/session=<uuid>.tar``
    with an approved label. Names come from a bucket listing -- untrusted
    input -- and are checked before anything is fetched or written.
    """
    label, _, rest = name.partition("/")
    if label not in approved_root_labels_v1():
        raise OffsiteError("object_label_is_not_approved")
    if not rest.endswith(".tar"):
        raise OffsiteError("object_name_is_not_a_partition_tar")
    relative = rest[: -len(".tar")]
    parts = relative.split("/")
    if len(parts) != 1 + len(_LAYOUT_KEYS) or parts[0] != ARCHIVE_LAYOUT_VERSION:
        raise OffsiteError("object_name_is_not_a_canonical_partition_path")
    values: dict[str, str] = {}
    for key, part in zip(_LAYOUT_KEYS, parts[1:], strict=True):
        prefix, separator, value = part.partition("=")
        if prefix != key or not separator or not _LAYOUT_TOKEN.fullmatch(value):
            raise OffsiteError("object_name_is_not_a_canonical_partition_path")
        values[key] = value
    try:
        canonical = (date.fromisoformat(values["date"]).isoformat(), str(UUID(values["session"])))
    except ValueError:
        raise OffsiteError("object_name_is_not_a_canonical_partition_path") from None
    if canonical != (values["date"], values["session"]):
        raise OffsiteError("object_name_is_not_a_canonical_partition_path")
    return label, relative


def require_canonical_path(directory: Path, label: str, relative: str) -> None:
    """A COMPLETE partition must sit exactly where its own contract, day and session put it.

    Its contract must also be one ``label`` admits, so an ``r0`` partition
    filed under ``u2`` (or a renamed object) is refused rather than restored
    to a path that misstates what it holds.
    """
    try:
        manifest = json.loads((directory / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
        source_id = str(manifest["source_id"])
        day = date.fromisoformat(str(manifest["utc_day"]))
        session_id = UUID(str(manifest["session_id"]))
    except (OSError, ValueError, KeyError, TypeError):
        raise OffsiteError("partition_identity_is_unreadable") from None
    contract = resolve_first_party_capture_contract_v1(source_id)
    if contract is None or source_id not in approved_root_labels_v1().get(label, frozenset()):
        raise OffsiteError("partition_contract_is_not_approved_for_its_root_label")
    if partition_directory(Path(), contract, day, session_id).as_posix() != relative:
        raise OffsiteError("partition_is_not_at_its_canonical_path")


# -- ledger ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LedgerEntryV1:
    manifest_content_hash: str
    tar_sha256: str
    size: int
    first_at: int
    state: str


def read_ledger(path: Path) -> tuple[dict[str, LedgerEntryV1], tuple[str, ...]]:
    """Object -> its ledger state, plus the objects whose lines disagree.

    An object's first line fixes what was sent (a torn line is skipped). A
    later ``RESTORE_VERIFIED`` line upgrades it only when it names the same
    manifest and tar SHA-256. Any outcome this version does not know --
    including the pre-hardening ``ALREADY_PRESENT`` -- reads as
    ``REMOTE_EXISTS_UNVERIFIED``: never as backed up.
    """
    entries: dict[str, LedgerEntryV1] = {}
    inconsistent: set[str] = set()
    if not path.exists():
        return entries, ()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
            name = str(raw["object"])
            outcome = str(raw.get("outcome"))
            entry = LedgerEntryV1(
                manifest_content_hash=str(raw["manifest_content_hash"]),
                tar_sha256=str(raw["tar_sha256"]),
                size=int(raw["bytes"]),
                first_at=int(raw["at"]),
                state=outcome if outcome in (UPLOADED, RESTORE_VERIFIED) else REMOTE_EXISTS_UNVERIFIED,
            )
        except (ValueError, KeyError, TypeError):
            continue
        previous = entries.get(name)
        if previous is None:
            if entry.state == RESTORE_VERIFIED:
                inconsistent.add(name)  # a verification of something never sent
            else:
                entries[name] = entry
        elif (previous.manifest_content_hash, previous.tar_sha256) != (
            entry.manifest_content_hash,
            entry.tar_sha256,
        ):
            inconsistent.add(name)
        elif entry.state == RESTORE_VERIFIED:
            entries[name] = replace(previous, state=RESTORE_VERIFIED)
    return entries, tuple(sorted(inconsistent))


def _append_ledger(path: Path, entry: Mapping[str, object]) -> None:
    """Append one line durably; a torn previous line is closed off first."""
    line = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END):
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                line = b"\n" + line
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


# -- backup ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupReportV1:
    uploaded: tuple[str, ...]
    #: This run's PUTs that met an existing object: content unknown, not backed up.
    conflicts: tuple[str, ...]
    skipped_not_complete: int
    pending_compaction: tuple[str, ...]
    stale_uncompacted: tuple[str, ...]
    failures: tuple[tuple[str, str], ...]
    deferred: int
    beyond_horizon: int
    over_budget: int
    window_bytes: int
    budget_bytes: int
    ledger_states: Mapping[str, int]
    #: Every ledger object still inside the budget window whose remote content is unknown.
    unverified_in_window: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.failures or self.stale_uncompacted or self.unverified_in_window or self.over_budget)


def run_backup(
    roots: Sequence[tuple[str, Path]],
    store: ObjectStoreV1,
    state_dir: Path,
    *,
    horizon_days: int,
    budget_bytes: int,
    budget_window_days: int,
    max_uploads: int = DEFAULT_MAX_UPLOADS,
    now: float | None = None,
) -> BackupReportV1:
    """Upload every COMPLETE, compacted, canonically placed partition not yet in the ledger."""
    if horizon_days < 1 or budget_window_days < horizon_days or budget_bytes < 1:
        raise OffsiteError("need_horizon_days>=1_and_budget_window_days>=horizon_days_and_budget_bytes>=1")
    for label, _ in roots:
        if label not in approved_root_labels_v1():
            raise OffsiteError("root_label_is_not_approved")
    now = time.time() if now is None else now
    ledger_path = state_dir / LEDGER_NAME
    ledger, inconsistent = read_ledger(ledger_path)
    window_start = now - budget_window_days * DAY_SECONDS
    window_bytes = sum(entry.size for entry in ledger.values() if entry.first_at > window_start)
    uploaded: list[str] = []
    conflicts: list[str] = []
    pending: list[str] = []
    stale: list[str] = []
    failures: list[tuple[str, str]] = [(name, "ledger_lines_disagree") for name in inconsistent]
    skipped = deferred = beyond_horizon = over_budget = 0
    for label, root in roots:
        for directory in find_partitions_v1(root):
            relative = directory.relative_to(root).as_posix()
            name = object_name(label, relative)
            if (directory / OPEN_MARKER_NAME).exists() or not (directory / MANIFEST_FILE_NAME).exists():
                skipped += 1
                continue
            manifest = json.loads((directory / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
            manifest_hash = str(manifest.get("manifest_content_hash"))
            if name in ledger:
                if ledger[name].manifest_content_hash != manifest_hash:
                    failures.append((name, "ledger_holds_another_manifest_for_this_object"))
                continue
            if name in inconsistent:
                continue
            finalized_at = (directory / MANIFEST_FILE_NAME).stat().st_mtime
            if (directory / RECORDS_FILE_NAME).exists() or not (directory / COMPACTION_FILE_NAME).exists():
                (stale if now - finalized_at > STALE_UNCOMPACTED_SECONDS else pending).append(name)
                continue
            if now - finalized_at > horizon_days * DAY_SECONDS:
                beyond_horizon += 1
                continue
            if read_partition_status_v1(directory).status != PARTITION_STATUS_COMPLETE:
                skipped += 1
                continue
            if len(uploaded) + len(conflicts) >= max_uploads:
                deferred += 1
                continue
            if over_budget:
                over_budget += 1
                continue
            try:
                require_canonical_path(directory, label, relative)
                body = partition_tar_bytes(directory, relative)
                if window_bytes + len(body) > budget_bytes:
                    over_budget += 1
                    continue
                created = store.put_new(name, body)
            except OffsiteError as error:
                failures.append((name, str(error)))
                continue
            entry = LedgerEntryV1(
                manifest_content_hash=manifest_hash,
                tar_sha256=hashlib.sha256(body).hexdigest(),
                size=len(body),
                first_at=int(now),
                state=UPLOADED if created else REMOTE_EXISTS_UNVERIFIED,
            )
            _append_ledger(
                ledger_path,
                {
                    "object": name,
                    "manifest_content_hash": entry.manifest_content_hash,
                    "tar_sha256": entry.tar_sha256,
                    "bytes": entry.size,
                    "outcome": entry.state,
                    "at": entry.first_at,
                },
            )
            ledger[name] = entry
            window_bytes += entry.size  # an existing object occupies the bucket too
            (uploaded if created else conflicts).append(name)
    states = {state: 0 for state in (UPLOADED, RESTORE_VERIFIED, REMOTE_EXISTS_UNVERIFIED)}
    for entry in ledger.values():
        states[entry.state] += 1
    return BackupReportV1(
        uploaded=tuple(uploaded),
        conflicts=tuple(conflicts),
        skipped_not_complete=skipped,
        pending_compaction=tuple(pending),
        stale_uncompacted=tuple(stale),
        failures=tuple(failures),
        deferred=deferred,
        beyond_horizon=beyond_horizon,
        over_budget=over_budget,
        window_bytes=window_bytes,
        budget_bytes=budget_bytes,
        ledger_states=states,
        unverified_in_window=tuple(
            sorted(
                name
                for name, entry in ledger.items()
                if entry.state == REMOTE_EXISTS_UNVERIFIED and entry.first_at > window_start
            )
        ),
    )


@dataclass(frozen=True, slots=True)
class AcceptReportV1:
    accepted: tuple[str, ...]
    already_verified: int
    refused: tuple[tuple[str, str], ...]


def accept_restore_receipt(state_dir: Path, receipt: Path, *, now: float | None = None) -> AcceptReportV1:
    """Record ``RESTORE_VERIFIED`` for each receipt line whose SHA-256 the ledger already holds.

    The ledger's SHA-256 is that of the deterministic tar of the local
    partition, so a match proves the remote bytes are exactly the local
    partition -- which also resolves a ``REMOTE_EXISTS_UNVERIFIED`` object.
    A mismatch is refused and leaves the object unverified.
    """
    now = time.time() if now is None else now
    ledger_path = state_dir / LEDGER_NAME
    ledger, inconsistent = read_ledger(ledger_path)
    accepted: list[str] = []
    refused: list[tuple[str, str]] = []
    already = 0
    try:
        lines = receipt.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise OffsiteError(f"receipt_unreadable:{type(error).__name__}") from None
    for line in lines:
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            name, digest = str(raw["object"]), str(raw["tar_sha256"])
        except (ValueError, KeyError, TypeError):
            refused.append(("-", "receipt_line_is_unreadable"))
            continue
        entry = ledger.get(name)
        if entry is None or name in inconsistent:
            refused.append((name, "object_is_not_in_the_ledger"))
        elif entry.tar_sha256 != digest:
            refused.append((name, "receipt_bytes_differ_from_the_ledger"))
        elif entry.state == RESTORE_VERIFIED:
            already += 1
        else:
            _append_ledger(
                ledger_path,
                {
                    "object": name,
                    "manifest_content_hash": entry.manifest_content_hash,
                    "tar_sha256": entry.tar_sha256,
                    "bytes": entry.size,
                    "outcome": RESTORE_VERIFIED,
                    "at": int(now),
                },
            )
            ledger[name] = replace(entry, state=RESTORE_VERIFIED)
            accepted.append(name)
    return AcceptReportV1(tuple(accepted), already, tuple(refused))


# -- restore ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestoredObjectV1:
    name: str
    tar_sha256: str
    manifest_content_hash: str
    record_count: int
    #: True only when ``--ledger`` was given and the object's SHA-256 matched it.
    ledger_match: bool


@dataclass(frozen=True, slots=True)
class RestoreReportV1:
    verified: tuple[RestoredObjectV1, ...]
    failures: tuple[tuple[str, str], ...]


def extract_partition(body: bytes, name: str, destination: Path) -> Path:
    """Write one object's files under ``destination/<label>/<relative>``; refuse anything else.

    The object must hold exactly the files of one compacted partition, each a
    regular file directly under the path the (canonical) name states.
    """
    label, relative = parse_object_name(name)
    root = destination.resolve()
    target = root / label / relative
    if not target.resolve().is_relative_to(root):
        raise OffsiteError("restore_target_escapes_the_destination")
    if target.exists():
        raise OffsiteError("restore_target_already_exists")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as archive:
        for member in archive.getmembers():
            folder, _, base = member.name.rpartition("/")
            if not member.isreg() or folder != relative or base not in PARTITION_FILES or base in files:
                raise OffsiteError("tar_member_outside_its_partition")
            handle = archive.extractfile(member)
            if handle is None:
                raise OffsiteError("tar_member_unreadable")
            files[base] = handle.read()
    if set(files) != PARTITION_FILES:
        raise OffsiteError("tar_does_not_hold_exactly_one_compacted_partition")
    target.mkdir(parents=True)
    for base, data in sorted(files.items()):
        (target / base).write_bytes(data)
    return target


def run_restore_verify(
    store: ObjectStoreV1,
    destination: Path,
    *,
    prefix: str = "",
    limit: int | None = None,
    ledger: Mapping[str, LedgerEntryV1] | None = None,
) -> RestoreReportV1:
    """Download, extract into an empty root and fully replay each (last ``limit`` by name) object.

    Each object must be canonically named, replay COMPLETE, and name exactly
    the path of its own contract, day and session; with ``ledger`` its bytes
    must also hash to what the VM recorded for that name.
    """
    if destination.exists() and any(destination.iterdir()):
        raise OffsiteError("restore_destination_must_be_empty")
    destination.mkdir(parents=True, exist_ok=True)
    names = store.list_names(prefix)
    if limit is not None:
        names = names[-limit:]
    verified: list[RestoredObjectV1] = []
    failures: list[tuple[str, str]] = []
    for name in names:
        try:
            label, relative = parse_object_name(name)
            body = store.get(name)
            digest = hashlib.sha256(body).hexdigest()
            if ledger is not None:
                entry = ledger.get(name)
                if entry is None:
                    raise OffsiteError("object_is_not_in_the_backup_ledger")
                if entry.tar_sha256 != digest:
                    raise OffsiteError("object_bytes_differ_from_the_ledger")
            directory = extract_partition(body, name, destination)
            if read_partition_status_v1(directory).status != PARTITION_STATUS_COMPLETE:
                raise OffsiteError("restored_partition_is_not_complete")
            record_count = verify_partition_v1(directory).record_count
            require_canonical_path(directory, label, relative)
            manifest = json.loads((directory / MANIFEST_FILE_NAME).read_text(encoding="utf-8"))
            verified.append(
                RestoredObjectV1(
                    name=name,
                    tar_sha256=digest,
                    manifest_content_hash=str(manifest.get("manifest_content_hash")),
                    record_count=record_count,
                    ledger_match=ledger is not None,
                )
            )
        except (OffsiteError, FirstPartyCaptureArchiveError, tarfile.TarError) as error:
            failures.append((name, f"{type(error).__name__}:{error}"))
    return RestoreReportV1(tuple(verified), tuple(failures))


# -- ping ------------------------------------------------------------------


def send_ping(url: str, outcome: str, body: str, *, attempts: int = 3, timeout: float = 10.0) -> None:
    """POST ``body`` to the check's success or ``/fail`` URL, retrying briefly."""
    if outcome not in ("success", "fail"):
        raise OffsiteError("ping_outcome_must_be_success_or_fail")
    target = url.rstrip("/") + ("/fail" if outcome == "fail" else "")
    data = body.encode("utf-8", errors="replace")[:PING_BODY_LIMIT]
    reason = "no_attempt"
    for attempt in range(attempts):
        request = urllib.request.Request(
            target, data=data, method="POST", headers={"Content-Type": "text/plain; charset=utf-8"}
        )
        try:
            _open(request, timeout)
            return
        except urllib.error.HTTPError as error:
            reason = f"http_{error.code}"
        except (urllib.error.URLError, OSError) as error:
            reason = type(error).__name__
        if attempt + 1 < attempts:
            time.sleep(2.0 * (attempt + 1))
    raise OffsiteError(f"ping_failed:{reason}")


# -- CLI -------------------------------------------------------------------


def _roots(values: Iterable[str]) -> list[tuple[str, Path]]:
    roots = []
    for value in values:
        label, separator, path = value.partition("=")
        if not separator or label not in approved_root_labels_v1():
            raise SystemExit(f"--root expects LABEL=PATH with LABEL in {sorted(approved_root_labels_v1())}")
        roots.append((label, Path(path)))
    if len({label for label, _ in roots}) != len(roots):
        raise SystemExit("--root labels must be distinct")
    return roots


def _lines(items: Iterable[str], prefix: str) -> Iterator[str]:
    for item in items:
        yield f"{prefix}{item}"


def _backup(args: argparse.Namespace) -> int:
    store = ParObjectStoreV1(read_url_file(Path(args.par_file), par=True))
    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    report = run_backup(
        _roots(args.root),
        store,
        state_dir,
        horizon_days=args.horizon_days,
        budget_bytes=args.budget_bytes,
        budget_window_days=args.budget_window_days,
        max_uploads=args.max_uploads,
    )
    states = report.ledger_states
    print(f"uploaded       {len(report.uploaded)} created this run (service checked Content-MD5)")
    print(f"conflict       {len(report.conflicts)} met an existing object this run (content NOT verified)")
    print(f"pending        {len(report.pending_compaction)} COMPLETE, not yet compacted")
    print(f"skipped        {report.skipped_not_complete} OPEN or not COMPLETE (never uploaded)")
    if report.beyond_horizon:
        print(f"horizon        {report.beyond_horizon} finalized > {args.horizon_days} d ago, never sent")
    if report.deferred:
        print(f"deferred       {report.deferred} beyond --max-uploads; next run continues")
    print(f"budget         {report.window_bytes} of {report.budget_bytes} bytes sent in the last "
          f"{args.budget_window_days} d (ledger estimate of the bucket)")
    if report.over_budget:
        print(f"OVER_BUDGET    {report.over_budget} withheld: would pass --budget-bytes")
    print(f"ledger         {states[UPLOADED]} {UPLOADED}, {states[RESTORE_VERIFIED]} {RESTORE_VERIFIED}, "
          f"{states[REMOTE_EXISTS_UNVERIFIED]} {REMOTE_EXISTS_UNVERIFIED}")
    for line in _lines(report.unverified_in_window, "UNVERIFIED     "):
        print(line)
    for line in _lines(report.stale_uncompacted, "STALE          uncompacted > 2 h: "):
        print(line)
    for name, reason in report.failures:
        print(f"FAILED         {name} {reason}")
    # OK means the uploads were accepted; only RESTORE_VERIFIED says they can be restored.
    print(f"backup         {'OK' if report.ok else 'ATTENTION'}")
    return 0 if report.ok else 1


def _restore_verify(args: argparse.Namespace) -> int:
    ledger = None
    if args.ledger:
        ledger, inconsistent = read_ledger(Path(args.ledger))
        if not ledger or inconsistent:
            raise OffsiteError("ledger_is_empty_or_inconsistent")
    receipt = Path(args.receipt) if args.receipt else None
    if receipt is not None and receipt.exists():
        raise OffsiteError("receipt_file_already_exists")
    store = ParObjectStoreV1(read_url_file(Path(args.par_file), par=True))
    report = run_restore_verify(store, Path(args.destination), prefix=args.prefix, limit=args.limit, ledger=ledger)
    if receipt is not None:
        verified_at = int(time.time())
        try:
            with receipt.open("x", encoding="utf-8") as handle:
                handle.writelines(
                    json.dumps(
                        {
                            "object": item.name,
                            "tar_sha256": item.tar_sha256,
                            "manifest_content_hash": item.manifest_content_hash,
                            "records": item.record_count,
                            "ledger_match": item.ledger_match,
                            "verified_at": verified_at,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                    for item in report.verified
                )
        except OSError as error:
            raise OffsiteError(f"receipt_not_written:{type(error).__name__}") from None
    for item in report.verified:
        print(f"VERIFIED       {item.name} records={item.record_count} "
              f"ledger={'match' if item.ledger_match else 'not_checked'}")
    for name, reason in report.failures:
        print(f"FAILED         {name} {reason}")
    ok = bool(report.verified) and not report.failures
    print(f"restore        {'OK' if ok else 'ATTENTION'}: {len(report.verified)} replayed, "
          f"{len(report.failures)} failed")
    return 0 if ok else 1


def _accept_restore(args: argparse.Namespace) -> int:
    report = accept_restore_receipt(Path(args.state_dir), Path(args.receipt))
    print(f"accepted       {len(report.accepted)} now {RESTORE_VERIFIED}")
    print(f"already        {report.already_verified}")
    for name, reason in report.refused:
        print(f"REFUSED        {name} {reason}")
    return 0 if (report.accepted or report.already_verified) and not report.refused else 1


def _ping(args: argparse.Namespace) -> int:
    body = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    if args.context:
        body = f"{args.context}\n{body}"
    send_ping(read_url_file(Path(args.url_file), par=False), args.outcome, body)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="upload new COMPLETE compacted partitions")
    backup.add_argument("--root", action="append", required=True, help="LABEL=PATH (repeatable)")
    backup.add_argument("--par-file", required=True, help="file holding the write-only bucket PAR URL")
    backup.add_argument("--state-dir", required=True, help="where the upload ledger lives")
    backup.add_argument("--max-uploads", type=int, default=DEFAULT_MAX_UPLOADS)
    # No defaults: the storage budget is the owner's, stated in the unit (README 7a).
    backup.add_argument("--horizon-days", type=int, required=True,
                        help="never send a partition finalized longer ago (< bucket lifecycle days)")
    backup.add_argument("--budget-window-days", type=int, required=True,
                        help="bucket lifecycle days plus slack for a late lifecycle run")
    backup.add_argument("--budget-bytes", type=int, required=True,
                        help="most bytes the ledger may show sent within the window")
    backup.set_defaults(handler=_backup)

    restore = sub.add_parser("restore-verify", help="download, extract and fully replay objects")
    restore.add_argument("--par-file", required=True, help="file holding a read+list bucket PAR URL")
    restore.add_argument("--destination", required=True, help="empty directory to restore into")
    restore.add_argument("--prefix", default="", help="object name prefix, e.g. u2/")
    restore.add_argument("--limit", type=int, default=None, help="only the last N objects by name")
    restore.add_argument("--ledger", default="", help="copy of the VM's ledger: each object's SHA-256 must match")
    restore.add_argument("--receipt", default="", help="new file listing what was verified (for accept-restore)")
    restore.set_defaults(handler=_restore_verify)

    accept = sub.add_parser("accept-restore", help="mark ledger objects a restore receipt verified")
    accept.add_argument("--state-dir", required=True, help="where the upload ledger lives")
    accept.add_argument("--receipt", required=True, help="receipt written by restore-verify --receipt")
    accept.set_defaults(handler=_accept_restore)

    ping = sub.add_parser("ping", help="dead-man's-switch ping (body from stdin)")
    ping.add_argument("outcome", choices=("success", "fail"))
    ping.add_argument("--url-file", required=True)
    ping.add_argument("--context", default="")
    ping.set_defaults(handler=_ping)

    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except OffsiteError as error:
        print(f"error          {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
