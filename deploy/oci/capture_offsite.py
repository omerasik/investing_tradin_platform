"""Off-host backup of finalized capture partitions, and dead-man's-switch pings.

Standard library only, beside the unchanged capture CLI (it imports the same
``trade_platform`` archive code from the release). Public market data only: no
exchange, broker or account credential is involved anywhere here.

``backup`` (trade-capture-backup.timer, hourly at HH:25 UTC) uploads every
COMPLETE, already compacted partition under the configured roots as one
deterministic tar object to an OCI Object Storage bucket through a bucket-level
*write-only* pre-authenticated request (PAR). It reads the archive and never
writes it; a partition that is OPEN, PARTIAL or not yet compacted is never
uploaded (a COMPLETE one left uncompacted for hours is reported, exit 1). Each
PUT carries ``Content-MD5`` (the service verifies the body) and
``If-None-Match: *`` (an existing object is never overwritten). Uploaded objects
are recorded in an append-only ledger in the unit's state directory, so a run
only sends what is new, and at most ``--max-uploads`` objects.

``ping success|fail`` posts to a healthchecks.io-compatible URL (``<url>`` for
success, ``<url>/fail`` for failure) with stdin as a short text body. Silence
is the alert: if the VM, the timer or the network dies, no success ping arrives
and the check's grace period fires.

``restore-verify`` runs on another host: it downloads the objects through a
*read* PAR with object listing, extracts each into an empty root under the path
its name states, and fully replays it (:func:`verify_partition_v1`). A backup
nobody has restored is a hope, not a copy.

No URL ever reaches argv, a log line or an exception message: each is read from
a file and every error is reported as a status code or exception type only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trade_platform.first_party_capture_archive_v1 import (
    COMPACTION_FILE_NAME,
    MANIFEST_FILE_NAME,
    OPEN_MARKER_NAME,
    PARTITION_STATUS_COMPLETE,
    RECORDS_FILE_NAME,
    FirstPartyCaptureArchiveError,
    find_partitions_v1,
    read_partition_status_v1,
    verify_partition_v1,
)

LEDGER_NAME = "uploaded-v1.ndjson"
#: A COMPLETE partition still uncompressed this long after finalizing means
#: compaction is failing; it is reported (exit 1) rather than uploaded raw.
STALE_UNCOMPACTED_SECONDS = 2 * 3600
DEFAULT_MAX_UPLOADS = 500
PING_BODY_LIMIT = 10_000
#: Exactly the files a compacted partition holds (backup_partition_v1 copies the same).
PARTITION_FILES = frozenset(
    {"session.json", "lifecycle.ndjson", MANIFEST_FILE_NAME, "records.ndjson.gz", COMPACTION_FILE_NAME}
)


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
        """Create ``name``; ``False`` if it already exists (never overwritten)."""

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


@dataclass(frozen=True, slots=True)
class BackupReportV1:
    uploaded: tuple[str, ...]
    already_present: tuple[str, ...]
    skipped_not_complete: int
    pending_compaction: tuple[str, ...]
    stale_uncompacted: tuple[str, ...]
    failures: tuple[tuple[str, str], ...]
    deferred: int

    @property
    def ok(self) -> bool:
        return not self.failures and not self.stale_uncompacted


def _read_ledger(state_dir: Path) -> dict[str, str]:
    """Object name -> manifest hash of everything already sent (torn last line ignored)."""
    ledger: dict[str, str] = {}
    path = state_dir / LEDGER_NAME
    if not path.exists():
        return ledger
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            ledger[str(entry["object"])] = str(entry["manifest_content_hash"])
        except (ValueError, KeyError, TypeError):
            continue
    return ledger


def _append_ledger(state_dir: Path, entry: dict[str, object]) -> None:
    with (state_dir / LEDGER_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


def run_backup(
    roots: Sequence[tuple[str, Path]],
    store: ObjectStoreV1,
    state_dir: Path,
    *,
    max_uploads: int = DEFAULT_MAX_UPLOADS,
    now: float | None = None,
) -> BackupReportV1:
    """Upload every COMPLETE, compacted partition not yet in the ledger."""
    now = time.time() if now is None else now
    ledger = _read_ledger(state_dir)
    uploaded: list[str] = []
    present: list[str] = []
    pending: list[str] = []
    stale: list[str] = []
    failures: list[tuple[str, str]] = []
    skipped = deferred = 0
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
                if ledger[name] != manifest_hash:
                    failures.append((name, "ledger_holds_another_manifest_for_this_object"))
                continue
            if (directory / RECORDS_FILE_NAME).exists() or not (directory / COMPACTION_FILE_NAME).exists():
                finalized_at = (directory / MANIFEST_FILE_NAME).stat().st_mtime
                (stale if now - finalized_at > STALE_UNCOMPACTED_SECONDS else pending).append(name)
                continue
            status = read_partition_status_v1(directory)
            if status.status != PARTITION_STATUS_COMPLETE:
                skipped += 1
                continue
            if len(uploaded) + len(present) >= max_uploads:
                deferred += 1
                continue
            try:
                body = partition_tar_bytes(directory, relative)
                created = store.put_new(name, body)
            except OffsiteError as error:
                failures.append((name, str(error)))
                continue
            (uploaded if created else present).append(name)
            _append_ledger(
                state_dir,
                {
                    "object": name,
                    "manifest_content_hash": manifest_hash,
                    "tar_sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                    "outcome": "UPLOADED" if created else "ALREADY_PRESENT",
                    "at": int(now),
                },
            )
            ledger[name] = manifest_hash
    return BackupReportV1(
        uploaded=tuple(uploaded),
        already_present=tuple(present),
        skipped_not_complete=skipped,
        pending_compaction=tuple(pending),
        stale_uncompacted=tuple(stale),
        failures=tuple(failures),
        deferred=deferred,
    )


# -- restore ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestoreReportV1:
    verified: tuple[tuple[str, int], ...]
    failures: tuple[tuple[str, str], ...]


def _relative_of(name: str) -> tuple[str, str]:
    label, _, rest = name.partition("/")
    if not label or not rest.endswith(".tar"):
        raise OffsiteError("object_name_is_not_a_partition_tar")
    relative = rest[: -len(".tar")]
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts) or not parts[-1].startswith("session="):
        raise OffsiteError("object_name_is_not_a_partition_path")
    return label, relative


def extract_partition(body: bytes, name: str, destination: Path) -> Path:
    """Write one object's files under ``destination/<label>/<relative>``; refuse anything else."""
    label, relative = _relative_of(name)
    target = destination / label / relative
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
    target.mkdir(parents=True)
    for base, data in sorted(files.items()):
        (target / base).write_bytes(data)
    return target


def run_restore_verify(
    store: ObjectStoreV1, destination: Path, *, prefix: str = "", limit: int | None = None
) -> RestoreReportV1:
    """Download, extract into an empty root and fully replay each (newest ``limit``) object."""
    if destination.exists() and any(destination.iterdir()):
        raise OffsiteError("restore_destination_must_be_empty")
    names = store.list_names(prefix)
    if limit is not None:
        names = names[-limit:]
    verified: list[tuple[str, int]] = []
    failures: list[tuple[str, str]] = []
    for name in names:
        try:
            directory = extract_partition(store.get(name), name, destination)
            if read_partition_status_v1(directory).status != PARTITION_STATUS_COMPLETE:
                raise OffsiteError("restored_partition_is_not_complete")
            verified.append((name, verify_partition_v1(directory).record_count))
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
        if not separator or not label or "/" in label:
            raise SystemExit(f"--root expects LABEL=PATH, got {value!r}")
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
    report = run_backup(_roots(args.root), store, state_dir, max_uploads=args.max_uploads)
    print(f"uploaded       {len(report.uploaded)}")
    print(f"already_there  {len(report.already_present)}")
    print(f"pending        {len(report.pending_compaction)} COMPLETE, not yet compacted")
    print(f"skipped        {report.skipped_not_complete} OPEN or not COMPLETE (never uploaded)")
    if report.deferred:
        print(f"deferred       {report.deferred} beyond --max-uploads; next run continues")
    for line in _lines(report.stale_uncompacted, "STALE          uncompacted > 2 h: "):
        print(line)
    for name, reason in report.failures:
        print(f"FAILED         {name} {reason}")
    print(f"backup         {'OK' if report.ok else 'ATTENTION'}")
    return 0 if report.ok else 1


def _restore_verify(args: argparse.Namespace) -> int:
    store = ParObjectStoreV1(read_url_file(Path(args.par_file), par=True))
    report = run_restore_verify(store, Path(args.destination), prefix=args.prefix, limit=args.limit)
    for name, count in report.verified:
        print(f"VERIFIED       {name} records={count}")
    for name, reason in report.failures:
        print(f"FAILED         {name} {reason}")
    ok = bool(report.verified) and not report.failures
    print(f"restore        {'OK' if ok else 'ATTENTION'}: {len(report.verified)} replayed, "
          f"{len(report.failures)} failed")
    return 0 if ok else 1


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
    backup.set_defaults(handler=_backup)

    restore = sub.add_parser("restore-verify", help="download, extract and fully replay objects")
    restore.add_argument("--par-file", required=True, help="file holding a read+list bucket PAR URL")
    restore.add_argument("--destination", required=True, help="empty directory to restore into")
    restore.add_argument("--prefix", default="", help="object name prefix, e.g. u2/")
    restore.add_argument("--limit", type=int, default=None, help="only the last N objects by name")
    restore.set_defaults(handler=_restore_verify)

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
