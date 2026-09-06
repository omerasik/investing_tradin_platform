"""OpenFIGI current-identity enrichment for the professional instrument master.

Module 3G.1f.1. OpenFIGI is a free, anonymous, zero-cost open-standard
identifier service (https://www.openfigi.com). It answers "what FIGI does
this instrument have right now" -- it has no as-of-date parameter and no
historical-ticker-lineage concept. This module therefore only ever appends
new ``IdentifierMapping`` rows to an instrument that is already registered
in :class:`~trade_platform.professional_instruments.PostgresProfessionalInstrumentMaster`;
it never registers a new instrument, never writes ``SymbolMapping`` rows, and
never alters or backdates any existing mapping.

Scope is deliberately narrow: only US common stock resolved via
``idType=TICKER`` + ``exchCode=US`` + ``marketSecDes=Equity`` is supported.
That combination was empirically verified (two bounded, owner-authorized,
anonymous probes against AAPL/XNAS on 2026-09-07) to be the one OpenFIGI
actually resolves unambiguously; querying by MIC (``micCode=XNAS``) returned
zero candidates for the identical instrument even though XNAS is a valid
enumerated micCode value, and no MIC-based query has since been probed or
authorized. Extending this module to another venue, country, or asset class
requires a fresh, explicitly-reviewed job specification -- never silent
inference from ``instrument_type`` or ``exchange_name``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .audit import AuditStore
from .domain import AssetClass
from .professional_instruments import (
    IdentifierMapping,
    IdentifierSourceKind,
    InstrumentType,
    PostgresProfessionalInstrumentMaster,
    ProfessionalInstrument,
)

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_FIGI_NAMESPACE = "OPENFIGI:FIGI"
OPENFIGI_COMPOSITE_FIGI_NAMESPACE = "OPENFIGI:COMPOSITE_FIGI"
OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE = "OPENFIGI:SHARE_CLASS_FIGI"

#: Conservative anonymous-tier ceiling. OpenFIGI's published anonymous
#: jobs-per-request figures are inconsistent between its general rate-limit
#: table and its v3/mapping endpoint documentation (unresolved as of
#: 2026-09-07); this module never actually sends more than one job, but a
#: caller-supplied batch is still capped defensively rather than trusting
#: the more generous of the two published figures.
MAX_ANONYMOUS_JOBS_PER_REQUEST = 5

_SUPPORTED_MARKET_SEC_DES = "Equity"
_SUPPORTED_SECURITY_TYPES = frozenset({"Common Stock"})
_SUPPORTED_EXCH_CODE = "US"


class OpenFigiIdentityError(ValueError):
    """Base error for OpenFIGI current-identity enrichment."""


class OpenFigiUnsupportedInstrumentError(OpenFigiIdentityError):
    """Raised for any instrument outside this module's narrow, proven scope."""


class OpenFigiMappingNotFoundError(OpenFigiIdentityError):
    """Raised when OpenFIGI returns zero candidates for a mapping job."""


class OpenFigiAmbiguousMappingError(OpenFigiIdentityError):
    """Raised when OpenFIGI returns more than one candidate; never guess."""


class OpenFigiMappingMismatchError(OpenFigiIdentityError):
    """Raised when the single candidate does not match the queried instrument."""


class OpenFigiRequestError(OpenFigiIdentityError):
    """Raised for a transport-level failure calling the OpenFIGI API."""


@dataclass(frozen=True, slots=True)
class OpenFigiMappingJob:
    """One OpenFIGI ``/v3/mapping`` request job.

    Anonymous access only -- no API key field exists on this type by design.
    """

    id_type: str
    id_value: str
    exch_code: str | None = None
    mic_code: str | None = None
    market_sec_des: str | None = None

    def to_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {"idType": self.id_type, "idValue": self.id_value}
        if self.exch_code is not None:
            payload["exchCode"] = self.exch_code
        if self.mic_code is not None:
            payload["micCode"] = self.mic_code
        if self.market_sec_des is not None:
            payload["marketSecDes"] = self.market_sec_des
        return payload


@dataclass(frozen=True, slots=True)
class OpenFigiMappingCandidate:
    """One candidate from an OpenFIGI mapping response.

    In-memory only -- never persisted as a row; only the FIGI-family
    identifier values it carries are ever written to the instrument master.
    """

    figi: str
    ticker: str
    exch_code: str
    security_type: str
    security_type2: str
    market_sector: str
    composite_figi: str | None = None
    share_class_figi: str | None = None


def _canonical_request_hash(jobs: tuple[OpenFigiMappingJob, ...]) -> str:
    payload = [job.to_payload() for job in jobs]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _response_content_hash(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _parse_job_result(job_result: Any) -> tuple[OpenFigiMappingCandidate, ...]:
    if not isinstance(job_result, dict):
        raise OpenFigiRequestError("openfigi_mapping_job_result_shape_unexpected")
    if "data" not in job_result:
        # Matches the documented/observed shape for an unresolved job: only a
        # "warning" key, no "data" key at all -- not an empty "data" list.
        return ()
    data = job_result["data"]
    if not isinstance(data, list):
        raise OpenFigiRequestError("openfigi_mapping_data_shape_unexpected")
    candidates = []
    for entry in data:
        candidates.append(
            OpenFigiMappingCandidate(
                figi=str(entry["figi"]),
                ticker=str(entry["ticker"]),
                exch_code=str(entry["exchCode"]),
                security_type=str(entry["securityType"]),
                security_type2=str(entry["securityType2"]),
                market_sector=str(entry["marketSector"]),
                composite_figi=entry.get("compositeFIGI"),
                share_class_figi=entry.get("shareClassFIGI"),
            )
        )
    return tuple(candidates)


class OpenFigiMappingClient:
    """Anonymous-only HTTP client for OpenFIGI's ``/v3/mapping`` endpoint.

    No API key field exists anywhere on this class -- Module 3G.1f.1 is
    authorized for anonymous access only. Adding key support is a distinct,
    separately-authorized change, not an option on this client.
    """

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout_seconds = timeout_seconds

    def map_jobs(
        self, jobs: tuple[OpenFigiMappingJob, ...]
    ) -> tuple[tuple[OpenFigiMappingCandidate, ...], str, str]:
        """Send one OpenFIGI mapping request.

        Returns ``(first_job_candidates, request_hash, response_hash)``.
        ``jobs`` must contain between 1 and ``MAX_ANONYMOUS_JOBS_PER_REQUEST``
        entries; only the first job's candidates are meaningful for this
        module's current single-job usage.
        """
        if not 1 <= len(jobs) <= MAX_ANONYMOUS_JOBS_PER_REQUEST:
            raise OpenFigiIdentityError("openfigi_job_batch_size_invalid")
        request_hash = _canonical_request_hash(jobs)
        body = json.dumps([job.to_payload() for job in jobs], sort_keys=True).encode()
        request = Request(
            OPENFIGI_MAPPING_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "trade-platform-openfigi-identity/0.1 (anonymous)",
            },
        )
        try:
            # URL is a fixed, hardcoded HTTPS constant -- never derived from
            # caller input.
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec B310
                raw_body = response.read()
        except (HTTPError, URLError) as error:
            raise OpenFigiRequestError("openfigi_mapping_request_failed") from error
        response_hash = _response_content_hash(raw_body)
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise OpenFigiRequestError("openfigi_mapping_response_undecodable") from error
        if not isinstance(parsed, list) or len(parsed) != len(jobs):
            raise OpenFigiRequestError("openfigi_mapping_response_shape_unexpected")
        per_job = [_parse_job_result(job_result) for job_result in parsed]
        return per_job[0], request_hash, response_hash


def build_us_common_stock_mapping_job(instrument: ProfessionalInstrument) -> OpenFigiMappingJob:
    """Build the one supported job shape: US common stock via ticker + exchCode=US.

    Raises :class:`OpenFigiUnsupportedInstrumentError` for anything outside
    that proven scope, rather than guessing a job shape for an
    instrument_type/venue this module has not been validated against.
    """
    if (
        instrument.asset_class is not AssetClass.EQUITY
        or instrument.instrument_type is not InstrumentType.COMMON_STOCK
        or instrument.base_currency != "USD"
    ):
        raise OpenFigiUnsupportedInstrumentError(
            f"openfigi_enrichment_unsupported_instrument:{instrument.instrument_id}"
        )
    return OpenFigiMappingJob(
        id_type="TICKER",
        id_value=instrument.canonical_symbol,
        exch_code=_SUPPORTED_EXCH_CODE,
        market_sec_des=_SUPPORTED_MARKET_SEC_DES,
    )


def validate_and_build_identifier_mappings(
    instrument: ProfessionalInstrument,
    candidates: tuple[OpenFigiMappingCandidate, ...],
    *,
    captured_at: datetime,
    request_hash: str,
    response_hash: str,
) -> tuple[IdentifierMapping, ...]:
    """Validate a single OpenFIGI candidate against ``instrument`` and build durable rows.

    Never picks a first result among several candidates, never persists
    OpenFIGI descriptive metadata (ticker/name/exchCode/securityType/...),
    and never fabricates a share-class FIGI that OpenFIGI did not return.
    """
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise OpenFigiIdentityError("openfigi_captured_at_must_be_timezone_aware")
    if len(candidates) == 0:
        raise OpenFigiMappingNotFoundError(f"openfigi_mapping_not_found:{instrument.instrument_id}")
    if len(candidates) > 1:
        raise OpenFigiAmbiguousMappingError(
            f"openfigi_mapping_ambiguous:{instrument.instrument_id}:{len(candidates)}_candidates"
        )
    candidate = candidates[0]
    if candidate.ticker != instrument.canonical_symbol:
        raise OpenFigiMappingMismatchError(
            f"openfigi_ticker_mismatch:{instrument.instrument_id}:{candidate.ticker}"
        )
    if candidate.exch_code != _SUPPORTED_EXCH_CODE:
        raise OpenFigiMappingMismatchError(
            f"openfigi_exch_code_mismatch:{instrument.instrument_id}:{candidate.exch_code}"
        )
    if candidate.market_sector != _SUPPORTED_MARKET_SEC_DES:
        raise OpenFigiMappingMismatchError(
            f"openfigi_market_sector_mismatch:{instrument.instrument_id}:{candidate.market_sector}"
        )
    if candidate.security_type not in _SUPPORTED_SECURITY_TYPES:
        raise OpenFigiMappingMismatchError(
            f"openfigi_security_type_mismatch:{instrument.instrument_id}:{candidate.security_type}"
        )
    source_reference = (
        f"openfigi://v3/mapping/{request_hash}"
        f"?response={response_hash}&captured_at={captured_at.isoformat()}"
    )
    rows = [
        IdentifierMapping(
            instrument_id=instrument.instrument_id,
            source_kind=IdentifierSourceKind.STANDARD,
            namespace=OPENFIGI_FIGI_NAMESPACE,
            value=candidate.figi,
            valid_from=captured_at,
            valid_until=None,
            ingested_at=captured_at,
            source_reference=source_reference,
        )
    ]
    if candidate.composite_figi is not None:
        rows.append(
            IdentifierMapping(
                instrument_id=instrument.instrument_id,
                source_kind=IdentifierSourceKind.STANDARD,
                namespace=OPENFIGI_COMPOSITE_FIGI_NAMESPACE,
                value=candidate.composite_figi,
                valid_from=captured_at,
                valid_until=None,
                ingested_at=captured_at,
                source_reference=source_reference,
            )
        )
    if candidate.share_class_figi is not None:
        rows.append(
            IdentifierMapping(
                instrument_id=instrument.instrument_id,
                source_kind=IdentifierSourceKind.STANDARD,
                namespace=OPENFIGI_SHARE_CLASS_FIGI_NAMESPACE,
                value=candidate.share_class_figi,
                valid_from=captured_at,
                valid_until=None,
                ingested_at=captured_at,
                source_reference=source_reference,
            )
        )
    return tuple(rows)


def record_openfigi_capture_evidence(
    audit_store: AuditStore,
    *,
    instrument_id: str,
    mappings: tuple[IdentifierMapping, ...],
    request_hash: str,
    response_hash: str,
    captured_at: datetime,
) -> None:
    """Append minimal, auditable provenance for one OpenFIGI capture.

    Reuses the existing :class:`~trade_platform.audit.AuditStore` evidence
    authority rather than inventing a second one. Records only identifier
    values and hashes -- never the descriptive OpenFIGI payload (name,
    securityDescription, ...).
    """
    audit_store.append(
        event_type="openfigi_identity_enrichment_captured",
        actor="openfigi_identity_enrichment",
        payload={
            "instrument_id": instrument_id,
            "endpoint": "v3/mapping",
            "endpoint_version": "v3",
            "request_content_hash": request_hash,
            "response_content_hash": response_hash,
            "captured_at": captured_at.isoformat(),
            "identifiers": [
                {"namespace": mapping.namespace, "value": mapping.value} for mapping in mappings
            ],
        },
    )


def enrich_us_common_stock_with_openfigi_identity(
    master: PostgresProfessionalInstrumentMaster,
    client: OpenFigiMappingClient,
    audit_store: AuditStore,
    *,
    instrument_id: str,
    as_of: datetime,
    captured_at: datetime,
) -> tuple[IdentifierMapping, ...]:
    """Run the full Module 3G.1f.1 flow for one already-registered instrument.

    Reads the instrument via ``master.get_as_of`` (never registers one),
    queries OpenFIGI anonymously with the one proven job shape, validates the
    single candidate, persists up to three ``STANDARD`` identifier mappings
    via ``master.add_identifier_mapping``, and records minimal provenance.
    Never calls ``master.register`` or ``master.add_symbol_mapping``.
    """
    instrument = master.get_as_of(instrument_id, as_of)
    job = build_us_common_stock_mapping_job(instrument)
    candidates, request_hash, response_hash = client.map_jobs((job,))
    mappings = validate_and_build_identifier_mappings(
        instrument,
        candidates,
        captured_at=captured_at,
        request_hash=request_hash,
        response_hash=response_hash,
    )
    for mapping in mappings:
        master.add_identifier_mapping(mapping)
    record_openfigi_capture_evidence(
        audit_store,
        instrument_id=instrument_id,
        mappings=mappings,
        request_hash=request_hash,
        response_hash=response_hash,
        captured_at=captured_at,
    )
    return mappings
