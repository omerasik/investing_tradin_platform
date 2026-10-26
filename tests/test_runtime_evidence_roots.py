"""Protected evidence-root wiring; only temporary fixtures and the CI test database.

The PostgreSQL case expects the existing migrated disposable CI service. This
module never runs migrations or discovers/connects to a local research database.
"""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from trade_platform.instrument_chart_v1 import ChartSeriesRefPage
from trade_platform.persistence import PostgresDatabase
from trade_platform.research_terminal_commands_v1 import PostgresTerminalCommandLedgerV1
from trade_platform.runtime_app import (
    RuntimeMode,
    compose_protected_postgres_app,
    create_runtime_app_from_environment,
)

ROOT_SETTINGS = {
    "research_data_root": "TRADE_PLATFORM_RESEARCH_DATA_ROOT",
    "capture_archive_root": "TRADE_PLATFORM_CAPTURE_ARCHIVE_ROOT",
    "universe_capture_archive_root": "TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT",
}
TOKEN = "fixture-root-wiring-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def configured_environment(roots):
    return {
        "TRADE_PLATFORM_ENVIRONMENT": "paper",
        "POSTGRES_DSN": "postgresql://fixture.invalid/unused",
        "TRADE_PLATFORM_OPERATOR_TOKEN": TOKEN,
        **{setting: str(roots[name]) for name, setting in ROOT_SETTINGS.items()},
    }


def write_evidence(roots):
    from tests.test_evidence_catalog_v1 import _partition
    from tests.test_instrument_chart_v1 import _bars, _write_window
    from trade_platform.first_party_capture_authority_v1 import (
        first_party_bybit_universe_contract_v1,
    )
    from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

    store = ResearchFrameStoreV1(roots["research_data_root"])
    windows = {symbol: _write_window(store, symbol, "2026-08-01", "2026-08-01", _bars())
               for symbol in SYMBOLS}
    _partition(roots["capture_archive_root"])
    for symbol in SYMBOLS:
        _partition(roots["universe_capture_archive_root"],
                   contract=first_party_bybit_universe_contract_v1(symbol))
    return windows


class EvidenceRootConfigurationTests(unittest.TestCase):
    def test_both_protected_modes_forward_only_explicit_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = {name: Path(directory) / name for name in ROOT_SETTINGS}
            for mode in ("paper", "production"):
                for configured in (True, False):
                    with self.subTest(mode=mode, configured=configured):
                        environment = configured_environment(roots)
                        environment["TRADE_PLATFORM_ENVIRONMENT"] = mode
                        if not configured:
                            for setting in ROOT_SETTINGS.values():
                                environment.pop(setting)
                        with mock.patch("trade_platform.runtime_app.compose_protected_postgres_app") as compose:
                            create_runtime_app_from_environment(env=environment.get)
                        for name, root in roots.items():
                            self.assertEqual(root if configured else None, compose.call_args.kwargs[name])
                        self.assertEqual(TOKEN if mode == "paper" else None,
                                         compose.call_args.kwargs["operator_token"])

    def test_development_uses_the_same_explicit_root_settings(self):
        from trade_platform.dev_app import create_dev_app

        with tempfile.TemporaryDirectory() as directory:
            roots = {name: Path(directory) / name for name in ROOT_SETTINGS}
            environment = configured_environment(roots)
            environment["TRADE_PLATFORM_ENVIRONMENT"] = "local_research"
            with mock.patch.dict(os.environ, environment, clear=True):
                app = create_dev_app(database=mock.MagicMock(spec=PostgresDatabase))
            for name, root in roots.items():
                self.assertEqual(root, getattr(app.state, name))


class ProtectedEvidenceReaderTests(unittest.TestCase):
    """Real API/readers over temporary evidence; the database is an explicit double."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.roots = {name: Path(temporary.name) / name for name in ROOT_SETTINGS}
        self.database = mock.MagicMock(spec=PostgresDatabase)
        self.database_patch = mock.patch("trade_platform.runtime_app.PostgresDatabase",
                                         return_value=self.database)
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)

    def app(self, roots=None):
        app = compose_protected_postgres_app(
            environment=RuntimeMode.PAPER, dsn="postgresql://fixture.invalid/unused",
            operator_token=TOKEN, **(roots or {}))
        self.addCleanup(app.state.postgres_authorities.close)
        # The file catalogue is real; unrelated PostgreSQL chart rows are absent.
        app.state.operator_dashboard_queries.chart_series = mock.Mock(
            return_value=ChartSeriesRefPage(state="UNAVAILABLE", items=[]))
        return app

    def test_configured_evidence_is_read_and_commands_keep_the_original_database(self):
        windows = write_evidence(self.roots)
        app = self.app(self.roots)
        self.assertIsInstance(app.state.terminal_commands, PostgresTerminalCommandLedgerV1)
        self.assertIs(app.state.terminal_commands.database, self.database)
        for name, root in self.roots.items():
            self.assertEqual(root, getattr(app.state, name))
        client = TestClient(app)
        for path in ("chart-series", "capture-availability", "research-terminal/commands"):
            self.assertEqual(401, client.get(f"/operator-dashboard/{path}").status_code)
        response = client.get("/operator-dashboard/chart-series", headers=HEADERS)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(set(SYMBOLS), {row["instrument"] for row in response.json()["items"]})
        for symbol, window in windows.items():
            path = f"/operator-dashboard/chart-series/{window['bar_frame_manifest_hash']}/candles"
            response = client.get(path, params={"timeframe": "1m", "limit": 2}, headers=HEADERS)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(symbol, response.json()["series"]["instrument"])
            self.assertEqual(window["dataset_version_id"], response.json()["series"]["dataset_version_id"])
            self.assertEqual(2, len(response.json()["candles"]))
        capture = client.get("/operator-dashboard/capture-availability", headers=HEADERS)
        self.assertEqual(200, capture.status_code, capture.text)
        self.assertEqual(4, len(capture.json()["sources"]))
        self.assertEqual(set(SYMBOLS), {s["exchange_symbol"] for s in capture.json()["sources"]})
        self.assertTrue(all(s["windows"] for s in capture.json()["sources"]))

    def test_unset_missing_and_file_roots_never_fall_back_to_host_evidence(self):
        file_root = self.roots["research_data_root"].parent / "not-a-directory"
        file_root.write_text("fixture", encoding="utf-8")
        for roots, capture_state in (({}, "UNCONFIGURED"), (self.roots, "UNAVAILABLE"),
                                     ({name: file_root for name in ROOT_SETTINGS}, "UNAVAILABLE")):
            with self.subTest(roots=roots):
                app = self.app(roots)
                client = TestClient(app)
                catalogue = client.get("/operator-dashboard/chart-series", headers=HEADERS)
                self.assertEqual(200, catalogue.status_code, catalogue.text)
                self.assertEqual("UNAVAILABLE", catalogue.json()["state"])
                self.assertEqual([], catalogue.json()["items"])
                capture = client.get("/operator-dashboard/capture-availability", headers=HEADERS)
                self.assertEqual(capture_state, capture.json()["state"])
                self.assertEqual([], capture.json()["sources"])
                self.assertTrue(capture.json()["limitations"])

    def test_inaccessible_roots_and_missing_chart_objects_are_refused(self):
        windows = write_evidence(self.roots)
        client = TestClient(self.app(self.roots))
        with mock.patch("trade_platform.api.list_research_window_series_v1",
                        side_effect=PermissionError):
            self.assertEqual(503, client.get("/operator-dashboard/chart-series", headers=HEADERS).status_code)
        with mock.patch("trade_platform.api.read_capture_availability_v1",
                        side_effect=PermissionError):
            self.assertEqual(503, client.get("/operator-dashboard/capture-availability", headers=HEADERS).status_code)
        for path in (self.roots["research_data_root"] / "v1" / "objects").glob("*/*.parquet"):
            path.unlink()  # Own temporary fixture only; retain catalogue identity.
        manifest = windows["BTCUSDT"]["bar_frame_manifest_hash"]
        response = client.get(f"/operator-dashboard/chart-series/{manifest}/candles",
                              params={"timeframe": "1m"}, headers=HEADERS)
        self.assertEqual(503, response.status_code, response.text)


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class ProtectedEvidenceRootsPostgresTests(unittest.TestCase):
    def test_environment_composes_catalogue_charts_associations_capture_and_commands(self):
        from tests.test_strategy_lab_ledger_v1_postgres import _study
        from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1

        with tempfile.TemporaryDirectory() as directory:
            roots = {name: Path(directory) / name for name in ROOT_SETTINGS}
            windows = write_evidence(roots)
            environment = configured_environment(roots)
            environment["POSTGRES_DSN"] = os.environ["POSTGRES_TEST_DSN"]
            app = create_runtime_app_from_environment(env=environment.get)
            try:
                database = app.state.postgres_authorities.database
                self.assertIs(app.state.terminal_commands.database, database)
                ledger = PostgresStrategyLabLedgerV1(database)
                studies = {}
                for symbol, window in windows.items():
                    study = _study(1)
                    study = replace(study, datasets=(replace(
                        study.datasets[0], dataset_version_id=UUID(window["dataset_version_id"]),
                        content_hash=window["content_hash"]),), label="unrelated label DOGEUSDT")
                    ledger.register_study(study)
                    studies[symbol] = study
                # A plausible label cannot repair a mismatched catalogue identity.
                wrong = replace(studies["BTCUSDT"], datasets=(replace(
                    studies["BTCUSDT"].datasets[0], content_hash="f" * 64),), label="BTCUSDT")
                ledger.register_study(wrong)
                client = TestClient(app)
                self.assertEqual(401, client.get("/operator-dashboard/chart-series").status_code)
                catalogue = client.get("/operator-dashboard/chart-series", headers=HEADERS)
                self.assertEqual(200, catalogue.status_code, catalogue.text)
                for symbol, window in windows.items():
                    response = client.get("/operator-dashboard/strategy-lab/studies",
                                          params={"instrument": symbol}, headers=HEADERS)
                    self.assertEqual(200, response.status_code, response.text)
                    rows = {row["study_id"]: row for row in response.json()["items"]}
                    self.assertIn(str(studies[symbol].study_id), rows)
                    self.assertNotIn(str(wrong.study_id), rows)
                    self.assertEqual("RESEARCH_WINDOW_CATALOGUE",
                                     rows[str(studies[symbol].study_id)]["instrument_source"])
                    manifest = window["bar_frame_manifest_hash"]
                    response = client.get(f"/operator-dashboard/chart-series/{manifest}/candles",
                                          params={"timeframe": "1m", "limit": 2}, headers=HEADERS)
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual(symbol, response.json()["series"]["instrument"])
                capture = client.get("/operator-dashboard/capture-availability", headers=HEADERS)
                self.assertEqual(200, capture.status_code, capture.text)
                self.assertEqual(4, len(capture.json()["sources"]))
                command = {"kind": "STRATEGY_SEARCH", "inputs": {"family": "trend_ma_cross",
                    "dataset_version_id": windows["BTCUSDT"]["dataset_version_id"], "workers": 1},
                    "idempotency_key": f"root-wiring-{uuid4()}"}
                path = "/operator-dashboard/research-terminal/commands"
                self.assertEqual(401, client.post(path, json=command).status_code)
                response = client.post(path, json=command, headers=HEADERS)
                self.assertEqual(202, response.status_code, response.text)
                self.assertEqual("REQUESTED", response.json()["state"])
                repeated = client.post(path, json=command, headers=HEADERS)
                self.assertEqual(202, repeated.status_code, repeated.text)
                self.assertEqual(response.json()["command_id"], repeated.json()["command_id"])
            finally:
                app.state.postgres_authorities.close()
