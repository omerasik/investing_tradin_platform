"""Static contract checks for the Module 3E staging deployment topology.

Mirrors tests/test_container_contract.py's style: pure text/YAML assertions, no
Docker invocation (the actual end-to-end behavior -- build, migrate, start, health --
was verified manually against a real Docker Compose stack; see
docs/MODULE_3E_STAGING_DEPLOYMENT_AND_SCHEDULER.md). These tests exist so a future
edit that breaks an invariant (e.g. reintroducing a duplicate `build:` race, or
dropping a hardening flag) fails fast in CI without needing Docker at all.
"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class StagingComposeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
        self.compose = yaml.safe_load(self.raw)

    def test_expected_services_are_present(self) -> None:
        self.assertEqual(
            set(self.compose["services"]), {"postgres", "api", "migrate", "worker", "web"}
        )

    def test_only_one_service_per_image_owns_the_build_section(self) -> None:
        # A duplicate `build:` block for the same image tag makes `docker compose up
        # --build` build it more than once concurrently, which fails outright (see
        # this file's own header comment and git history for the real failure).
        images_with_build: dict[str, list[str]] = {}
        for name, service in self.compose["services"].items():
            if "build" not in service:
                continue
            images_with_build.setdefault(service["image"], []).append(name)
        duplicates = {image: owners for image, owners in images_with_build.items() if len(owners) > 1}
        self.assertEqual(duplicates, {})

    def test_migrate_is_a_disposable_one_shot_step_gating_api_and_worker(self) -> None:
        migrate = self.compose["services"]["migrate"]
        self.assertEqual(migrate["build"]["dockerfile"], "Dockerfile.migrate")
        self.assertEqual(migrate["restart"], "no")
        for dependant in ("api", "worker"):
            depends_on = self.compose["services"][dependant]["depends_on"]
            self.assertEqual(depends_on.get("migrate", {}).get("condition"), "service_completed_successfully")

    def test_api_and_worker_use_the_research_image_and_no_sqlite_fallback(self) -> None:
        for name in ("api", "worker"):
            service = self.compose["services"][name]
            self.assertTrue(service["image"].startswith("trade-platform-research:"))
            self.assertIn("POSTGRES_DSN", service["environment"])

    def test_api_and_worker_and_web_run_hardened(self) -> None:
        for name in ("api", "worker", "web"):
            service = self.compose["services"][name]
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertIn("no-new-privileges:true", service["security_opt"])
            self.assertTrue(any(entry.startswith("/tmp:") for entry in service["tmpfs"]))

    def test_api_and_worker_expose_health_endpoints(self) -> None:
        for name in ("api", "worker"):
            healthcheck = self.compose["services"][name]["healthcheck"]
            self.assertIn("/health/live", " ".join(healthcheck["test"]))

    def test_secrets_are_never_hardcoded(self) -> None:
        # Every secret-shaped environment value must come from ${VAR...} substitution
        # (required via `:?` or defaulted via `:-`) or reference the read-only secrets
        # mount -- never a literal value.
        for name in ("password", "secret", "token"):
            for line in self.raw.lower().splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or name not in stripped:
                    continue
                self.assertTrue(
                    "${" in stripped or "run/secrets" in stripped,
                    f"possible hardcoded {name}: {line}",
                )

    def test_no_kubernetes_kafka_redis_or_celery_infrastructure(self) -> None:
        # Scan only the actual config (never the header prose, which names these
        # systems deliberately, explaining why they are absent).
        code_only = "\n".join(
            line for line in self.raw.splitlines() if not line.strip().startswith("#")
        ).lower()
        for forbidden in ("kubernetes", "kafka", "redis", "celery"):
            self.assertNotIn(forbidden, code_only)

    def test_postgres_data_is_a_named_persistent_volume(self) -> None:
        postgres = self.compose["services"]["postgres"]
        self.assertTrue(any("postgres-data" in item for item in postgres["volumes"]))
        self.assertIn("trade-platform-postgres-data", self.compose["volumes"])


class MigrateImageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dockerfile = (ROOT / "Dockerfile.migrate").read_text(encoding="utf-8")
        self.requirements = (ROOT / "requirements-migrate.txt").read_text(encoding="utf-8")

    def test_base_image_is_pinned_by_digest(self) -> None:
        image = next(line for line in self.dockerfile.splitlines() if line.startswith("FROM "))
        self.assertRegex(image, r"^FROM python:3\.12\.14-slim-bookworm@sha256:[0-9a-f]{64}$")

    def test_runs_as_non_root(self) -> None:
        self.assertIn("USER 10001:10001", self.dockerfile)

    def test_command_is_exactly_alembic_upgrade_head(self) -> None:
        self.assertIn('CMD ["python", "-m", "alembic", "upgrade", "head"]', self.dockerfile)

    def test_includes_alembic_and_its_own_dependency_closure(self) -> None:
        for package in ("alembic", "sqlalchemy", "mako", "markupsafe", "greenlet"):
            self.assertIn(package, self.requirements.lower())


class DashboardImageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")

    def test_base_image_is_pinned_by_digest(self) -> None:
        image = next(line for line in self.dockerfile.splitlines() if line.startswith("FROM "))
        self.assertRegex(image, r"^FROM node:22-alpine@sha256:[0-9a-f]{64} AS base$")

    def test_runs_as_non_root_with_a_liveness_probe(self) -> None:
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn("HEALTHCHECK", self.dockerfile)

    def test_does_not_invoke_pnpm_at_container_start(self) -> None:
        # `pnpm exec next start` would make corepack lazily fetch the pnpm binary
        # from the network on first container start; the built `next` binary must be
        # invoked directly instead. See this file's own comment for the failure mode.
        self.assertNotIn('CMD ["pnpm"', self.dockerfile)
        self.assertIn("node_modules/.bin/next", self.dockerfile)
        self.assertIn('"start"', self.dockerfile)

    def test_never_bakes_in_a_backend_credential(self) -> None:
        for forbidden in ("TRADE_PLATFORM_OPERATOR_TOKEN", "TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN="):
            self.assertNotIn(forbidden, self.dockerfile)


if __name__ == "__main__":
    unittest.main()
