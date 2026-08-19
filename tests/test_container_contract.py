import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ContainerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.requirements = (ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        )
        self.workflow = (ROOT / ".github" / "workflows" / "verify.yml").read_text(
            encoding="utf-8"
        )

    def test_build_context_is_an_explicit_allowlist(self) -> None:
        entries = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        self.assertEqual(
            entries,
            {
                "**",
                "!Dockerfile",
                "!.dockerignore",
                "!requirements-runtime.txt",
                "!src/",
                "!src/**",
            },
        )

    def test_base_image_and_runtime_dependencies_are_exactly_pinned(self) -> None:
        image = next(
            line for line in self.dockerfile.splitlines() if line.startswith("FROM ")
        )
        self.assertRegex(
            image,
            r"^FROM python:3\.12\.11-slim-bookworm@sha256:[0-9a-f]{64}$",
        )
        resolved = {
            line.split("==", 1)[0]
            for line in self.requirements.splitlines()
            if line and not line.startswith("#")
        }
        self.assertEqual(
            resolved,
            {
                "annotated-doc",
                "annotated-types",
                "anyio",
                "click",
                "fastapi",
                "h11",
                "idna",
                "psycopg",
                "psycopg-binary",
                "pydantic",
                "pydantic-core",
                "starlette",
                "typing-extensions",
                "typing-inspection",
                "tzdata",
                "uvicorn",
            },
        )
        for line in self.requirements.splitlines():
            if line and not line.startswith("#"):
                self.assertRegex(line, r"^[a-z0-9-]+==[0-9]+(?:\.[0-9]+)+(?:\.[0-9]+)?$")

    def test_image_installs_only_the_locked_runtime_and_source_tree(self) -> None:
        self.assertIn("pip install --no-deps --requirement", self.dockerfile)
        self.assertIn("python -m pip check", self.dockerfile)
        self.assertIn("COPY --chown=10001:10001 src ./src", self.dockerfile)
        self.assertNotRegex(self.dockerfile, r"(?m)^COPY\s+\.\s")
        self.assertNotIn("TRADE_PLATFORM_OPERATOR_TOKEN=", self.dockerfile)

    def test_final_process_is_non_root_and_has_a_liveness_probe(self) -> None:
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn("/health/live", self.dockerfile)
        self.assertIn(
            '["python", "-m", "uvicorn", "trade_platform.api:app"',
            self.dockerfile,
        )
        self.assertLess(
            self.dockerfile.index("USER 10001:10001"),
            self.dockerfile.index('CMD ["python", "-m", "uvicorn"'),
        )

    def test_ci_runs_the_image_with_hardened_flags_and_authorization_checks(self) -> None:
        for required in (
            "docker build --tag",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs /tmp:rw,noexec,nosuid,size=16m",
            "id -u",
            'test "$unauthenticated_status" = "401"',
            'test "$viewer_read_status" = "200"',
            'test "$viewer_write_status" = "403"',
        ):
            self.assertIn(required, self.workflow)

    def test_container_scanner_is_version_and_digest_pinned(self) -> None:
        self.assertIn(
            "aquasec/trivy:0.73.0@sha256:"
            "7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c",  # pragma: allowlist secret
            self.workflow,
        )

    def test_container_scanner_has_no_engine_socket_or_root_runtime(self) -> None:
        self.assertNotIn("/var/run/docker.sock", self.workflow)
        for required in (
            '--user "$(id -u):$(id -g)"',
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            '--volume "$scan_input:/input:ro"',
        ):
            self.assertIn(required, self.workflow)

    def test_container_vulnerability_policy_fails_closed(self) -> None:
        for required in (
            "--scanners vuln",
            "--severity HIGH,CRITICAL",
            "--exit-code 1",
            "--exit-on-eol 1",
            "--input /input/research-api.tar",
        ):
            self.assertIn(required, self.workflow)

    def test_container_security_evidence_is_retained(self) -> None:
        for evidence in (
            "container-security-evidence/vulnerability-report.json",
            "container-security-evidence/sbom.cdx.json",
        ):
            self.assertIn(evidence, self.workflow)


if __name__ == "__main__":
    unittest.main()
