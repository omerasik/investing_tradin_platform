"""Canonical one-command local development orchestrator for Trade Investing Panel.

Prerequisites:
  - Python >= 3.11 (with project dependencies installed: pip install -e ".[dev]")
  - Docker Desktop running (for local PostgreSQL 16 on compose.dev.yml)
  - Node.js >= 20 and pnpm / corepack

Usage:
  python scripts/dev.py
  python scripts/dev.py --reset-db
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"


def log(msg: str, prefix: str = "▶") -> None:
    print(f"\033[36m{prefix}\033[0m {msg}", flush=True)


def log_success(msg: str) -> None:
    print(f"\033[32m✔\033[0m {msg}", flush=True)


def log_error(msg: str) -> None:
    print(f"\033[31m✖ Error:\033[0m {msg}", file=sys.stderr, flush=True)


def log_warn(msg: str) -> None:
    print(f"\033[33m▲ Warning:\033[0m {msg}", flush=True)


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except (TimeoutError, ConnectionRefusedError, OSError):
            return False


def resolve_pnpm_runner() -> list[str]:
    """Find available pnpm or corepack runner."""
    if shutil.which("pnpm"):
        return ["pnpm"]
    if shutil.which("corepack"):
        return ["corepack", "pnpm"]
    if shutil.which("npx"):
        return ["npx", "pnpm"]
    return ["pnpm"]


def check_prerequisites(postgres_port: int, api_port: int, dashboard_port: int) -> list[str]:
    log("Checking prerequisites...")

    # 1. Python version check
    if sys.version_info < (3, 12):
        log_error(f"Python 3.12+ is required (found {sys.version.split()[0]}).")
        sys.exit(1)

    # 2. Python dependencies check
    missing_deps = []
    for pkg in ("fastapi", "uvicorn", "psycopg", "alembic"):
        try:
            __import__(pkg)
        except ImportError:
            missing_deps.append(pkg)

    if missing_deps:
        log_error(
            f"Missing required Python dependencies: {', '.join(missing_deps)}.\n"
            "Please run: pip install -e \".[dev]\" (or activate your .venv virtual environment)."
        )
        sys.exit(1)

    # 3. Docker CLI & daemon check
    if not shutil.which("docker"):
        log_error(
            "Docker CLI is not found in PATH.\n"
            "Please install Docker Desktop: https://www.docker.com/products/docker-desktop"
        )
        sys.exit(1)

    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if res.returncode != 0:
            log_error(
                "Docker daemon is not running.\n"
                "Please start Docker Desktop and retry."
            )
            sys.exit(1)
    except (OSError, subprocess.SubprocessError) as exc:
        log_error(f"Failed to communicate with Docker daemon: {exc}")
        sys.exit(1)

    # 4. Node.js check
    if not shutil.which("node"):
        log_error(
            "Node.js is not found in PATH.\n"
            "Please install Node.js 20+: https://nodejs.org/"
        )
        sys.exit(1)

    # 5. pnpm runner check
    pnpm_cmd = resolve_pnpm_runner()
    try:
        res = subprocess.run(
            pnpm_cmd + ["--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=(sys.platform == "win32" and pnpm_cmd[0] in ("pnpm", "npx", "corepack")),
            timeout=10,
        )
        if res.returncode != 0:
            log_error("Could not run pnpm or corepack pnpm. Run 'corepack enable' or install pnpm.")
            sys.exit(1)
    except (OSError, subprocess.SubprocessError) as exc:
        log_error(f"Failed to check pnpm: {exc}")
        sys.exit(1)

    # 6. Check if ports are already bound unexpectedly
    for name, port in [("FastAPI Backend", api_port), ("Next.js Dashboard", dashboard_port)]:
        if is_port_in_use(port):
            log_error(
                f"{name} port {port} is already in use by another process on localhost.\n"
                f"Please terminate the conflicting process or specify a different port."
            )
            sys.exit(1)

    log_success("Prerequisites verified successfully.")
    return pnpm_cmd


def start_postgres(postgres_port: int, reset_db: bool = False) -> None:
    compose_file = str(ROOT_DIR / "compose.dev.yml")
    env = os.environ.copy()
    env["POSTGRES_PORT"] = str(postgres_port)

    if reset_db:
        log_warn("Resetting development database volume (--reset-db)...")
        subprocess.run(
            ["docker", "compose", "-f", compose_file, "down", "-v"],
            env=env,
            check=False,
        )

    log(f"Starting PostgreSQL 16 development container on port {postgres_port}...")
    res = subprocess.run(
        ["docker", "compose", "-f", compose_file, "up", "-d", "postgres"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        log_error(
            f"Failed to start PostgreSQL container on localhost:{postgres_port}.\n"
            f"{res.stderr}\n"
            "Check that Docker Desktop is running or change the configured development port."
        )
        sys.exit(1)

    log("Waiting for PostgreSQL database readiness...")
    dsn = f"postgresql://postgres:postgres@127.0.0.1:{postgres_port}/trade_platform"  # pragma: allowlist secret
    deadline = time.time() + 30
    import psycopg

    while time.time() < deadline:
        try:
            with (
                psycopg.connect(dsn, connect_timeout=1) as conn,
                conn.cursor() as cur,
            ):
                cur.execute("SELECT 1")
                log_success(f"PostgreSQL container is healthy on localhost:{postgres_port}.")
                return
        except (OSError, psycopg.Error):
            time.sleep(1)

    log_error(
        f"PostgreSQL development container did not become ready on localhost:{postgres_port} within 30s.\n"
        "Check docker logs via: docker compose -f compose.dev.yml logs"
    )
    sys.exit(1)


def run_migrations(postgres_port: int) -> None:
    log("Applying Alembic migrations...")
    dsn = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{postgres_port}/trade_platform"  # pragma: allowlist secret
    env = os.environ.copy()
    env["POSTGRES_DSN"] = dsn
    env["PYTHONPATH"] = str(ROOT_DIR / "src")

    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(ROOT_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        log_error(f"Alembic migrations failed:\n{res.stderr}\n{res.stdout}")
        sys.exit(1)

    log_success("Alembic migrations applied up to head.")


def start_backend(postgres_port: int, api_port: int, operator_token: str) -> subprocess.Popen[str]:
    log(f"Starting FastAPI backend on http://127.0.0.1:{api_port}...")
    dsn = f"postgresql://postgres:postgres@127.0.0.1:{postgres_port}/trade_platform"  # pragma: allowlist secret
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR / "src")
    env["POSTGRES_DSN"] = dsn
    env["API_PORT"] = str(api_port)
    env["TRADE_PLATFORM_OPERATOR_TOKEN"] = operator_token
    env["TRADE_PLATFORM_ENVIRONMENT"] = "local_research"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "trade_platform.dev_app:create_dev_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(api_port),
        "--log-level",
        "warning",
    ]

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc


def start_frontend(
    pnpm_cmd: list[str],
    api_port: int,
    dashboard_port: int,
    operator_token: str,
    view_token: str,
    session_secret: str,
) -> subprocess.Popen[str]:
    log(f"Starting Next.js dashboard on http://127.0.0.1:{dashboard_port}...")
    env = os.environ.copy()
    env["PORT"] = str(dashboard_port)
    env["TRADE_PLATFORM_API_BASE_URL"] = f"http://127.0.0.1:{api_port}"
    env["TRADE_PLATFORM_OPERATOR_TOKEN"] = operator_token
    env["TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN"] = view_token
    env["TRADE_PLATFORM_SESSION_SECRET"] = session_secret
    env["TRADE_PLATFORM_DASHBOARD_ORIGIN"] = f"http://127.0.0.1:{dashboard_port}"

    cmd = pnpm_cmd + ["exec", "next", "dev", "-p", str(dashboard_port)]

    proc = subprocess.Popen(
        cmd,
        cwd=str(WEB_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        shell=(sys.platform == "win32" and pnpm_cmd[0] in ("pnpm", "npx", "corepack")),
    )
    return proc


def wait_for_services(api_port: int, dashboard_port: int) -> None:
    log("Verifying service health and readiness...")
    deadline = time.time() + 30

    # 1. Wait for FastAPI backend
    api_live_url = f"http://127.0.0.1:{api_port}/health/live"
    api_ready_url = f"http://127.0.0.1:{api_port}/health/ready"

    api_ready = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(api_live_url, timeout=1) as res:
                if res.status == 200:
                    with urllib.request.urlopen(api_ready_url, timeout=1) as rres:
                        if rres.status == 200:
                            api_ready = True
                            break
        except (OSError, TimeoutError, urllib.error.URLError):
            time.sleep(0.5)

    if not api_ready:
        log_error(f"FastAPI backend did not become healthy on {api_live_url} within 30s.")
        sys.exit(1)

    log_success(f"FastAPI backend is LIVE and READY on http://127.0.0.1:{api_port}.")

    # 2. Wait for Next.js dashboard
    frontend_login_url = f"http://127.0.0.1:{dashboard_port}/login"
    frontend_ready = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(frontend_login_url, timeout=1) as res:
                if res.status == 200:
                    frontend_ready = True
                    break
        except (OSError, TimeoutError, urllib.error.URLError):
            time.sleep(0.5)

    if not frontend_ready:
        log_error(f"Next.js frontend did not respond on {frontend_login_url} within 30s.")
        sys.exit(1)

    log_success(f"Next.js dashboard is ready on http://127.0.0.1:{dashboard_port}.")


def print_banner(
    postgres_port: int,
    api_port: int,
    dashboard_port: int,
    view_token: str,
) -> None:
    print(
        "\n"
        "\033[32m========================================================================\033[0m\n"
        "\033[1;37m Trade Platform local environment ready\033[0m\n"
        "\033[32m------------------------------------------------------------------------\033[0m\n"
        f" PostgreSQL: HEALTHY (localhost:{postgres_port})\n"
        f" API:        http://127.0.0.1:{api_port}\n"
        f" Dashboard:  http://127.0.0.1:{dashboard_port}\n"
        f" Login URL:  http://127.0.0.1:{dashboard_port}/login\n"
        " Mode:       LOCAL RESEARCH / PAPER ONLY\n"
        " Live:       DISABLED\n"
        "\033[32m========================================================================\033[0m\n"
        f" Operator View Password: \033[1;33m{view_token}\033[0m\n"
        " (Use this password to authenticate at http://localhost:3000/login)\n"
        "\033[32m========================================================================\033[0m\n"
        " Press \033[1;31mCtrl+C\033[0m to stop child services cleanly.\n",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-command local development orchestrator for Trade Investing Panel."
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Destructively reset the development PostgreSQL data volume before starting.",
    )
    parser.add_argument(
        "--postgres-port",
        type=int,
        default=int(os.environ.get("POSTGRES_PORT", "5439")),
        help="Port for local development PostgreSQL (default: 5439).",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=int(os.environ.get("API_PORT", "8000")),
        help="Port for FastAPI backend (default: 8000).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "3000")),
        help="Port for Next.js dashboard (default: 3000).",
    )
    args = parser.parse_args()

    operator_token = os.environ.get("TRADE_PLATFORM_OPERATOR_TOKEN", "local-dev-operator-token")
    view_token = os.environ.get("TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN", "local-dev-view-password")
    session_secret = os.environ.get(
        "TRADE_PLATFORM_SESSION_SECRET", "local-dev-session-secret-change-in-production"
    )

    pnpm_cmd = check_prerequisites(args.postgres_port, args.api_port, args.port)
    start_postgres(args.postgres_port, reset_db=args.reset_db)
    run_migrations(args.postgres_port)

    backend_proc = start_backend(args.postgres_port, args.api_port, operator_token)
    frontend_proc = start_frontend(
        pnpm_cmd, args.api_port, args.port, operator_token, view_token, session_secret
    )

    procs = [backend_proc, frontend_proc]

    def cleanup(*_args: object) -> None:
        log("\nShutting down child application processes cleanly...")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                p.kill()
        log_success("Child processes stopped. PostgreSQL volume preserved.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        wait_for_services(args.api_port, args.port)
        print_banner(args.postgres_port, args.api_port, args.port, view_token)

        while True:
            # Monitor child processes
            for p, name in ((backend_proc, "FastAPI Backend"), (frontend_proc, "Next.js Frontend")):
                code = p.poll()
                if code is not None:
                    err_out = p.stderr.read() if p.stderr else ""
                    log_error(f"{name} exited unexpectedly with code {code}:\n{err_out}")
                    cleanup()
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
