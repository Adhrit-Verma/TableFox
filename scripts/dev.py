from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Sequence
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


REPO = Path(__file__).resolve().parents[1]
WEB_DIR = REPO / "apps" / "web"
SOURCE_DIR = REPO / "services" / "dbmap" / "src"
API_HEALTH_URL = "http://127.0.0.1:8000/health"
WEB_URL = "http://127.0.0.1:3000"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Database Agent with one command.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the web UI.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the database and graph snapshot, then exit.",
    )
    return parser.parse_args()


def prepare_environment() -> dict[str, str]:
    env = os.environ.copy()
    source_path = str(SOURCE_DIR)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, env.get("PYTHONPATH", "")) if part
    )

    if not (REPO / ".env").exists() and not env.get("DATABASE_URL"):
        raise RuntimeError("No database configuration found. Create .env from .env.example.")

    return env


def require_command(command: str) -> None:
    executable = f"{command}.cmd" if os.name == "nt" and command == "npm" else command
    if shutil.which(executable) is None:
        raise RuntimeError(f"Required command not found: {command}")


def load_backend(env: dict[str, str]):
    os.environ.update(env)
    sys.path.insert(0, str(SOURCE_DIR))
    try:
        from dbmap.postgres import PostgresIntrospector
    except ImportError as error:
        raise RuntimeError(
            "Python dependencies are missing. Run .\\run.cmd -Install once."
        ) from error
    return PostgresIntrospector()


def validate_database(introspector) -> dict:
    print("Checking PostgreSQL credentials and read-only access...")
    try:
        connection = introspector.connectivity_check()
        snapshot = introspector.snapshot(refresh=True)
    except Exception as error:
        raise RuntimeError(f"Database check failed: {error}") from None
    summary = snapshot.summary
    print(
        f"Connected to {connection['database']} as {connection['user']} "
        f"({summary.get('table', 0)} tables, {summary.get('view', 0)} views, "
        f"{len(snapshot.nodes)} graph nodes)."
    )
    return connection


def read_url(url: str, timeout_seconds: float = 2) -> str | None:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            if 200 <= response.status < 300:
                return response.read().decode("utf-8", errors="replace")
    except (OSError, URLError):
        pass
    return None


def api_health() -> dict | None:
    payload = read_url(API_HEALTH_URL, timeout_seconds=10)
    if payload is None:
        return None
    try:
        health = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return health if health.get("ok") else None


def web_ready() -> bool:
    payload = read_url(WEB_URL)
    return payload is not None and "Database Agent" in payload


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_for_existing_service(check, timeout_seconds: int = 5) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.25)
    return False


def start_process(command: Sequence[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )


def wait_for_services(processes: list[subprocess.Popen], timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        exited = [process for process in processes if process.poll() is not None]
        if exited:
            raise RuntimeError(f"A service exited during startup with code {exited[0].returncode}.")
        if api_health() is not None and web_ready():
            return
        time.sleep(0.5)
    raise RuntimeError("The API and web UI did not become ready within 90 seconds.")


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        import signal

        os.killpg(process.pid, signal.SIGTERM)


def run() -> int:
    args = parse_args()
    os.chdir(REPO)
    env = prepare_environment()
    introspector = load_backend(env)
    connection = validate_database(introspector)
    if args.check:
        print("Database Agent check passed.")
        return 0

    require_command("npm")
    if not (WEB_DIR / "node_modules").exists() and not (REPO / "node_modules").exists():
        raise RuntimeError("Web dependencies are missing. Run .\\run.cmd -Install once.")

    processes: list[subprocess.Popen] = []
    try:
        running_health = api_health()
        if running_health is None and port_open(8000):
            if wait_for_existing_service(api_health):
                running_health = api_health()
            else:
                raise RuntimeError(
                    "Port 8000 is occupied by a service that is not a healthy Database Agent API."
                )
        if running_health is None:
            processes.append(
                start_process(
                    [sys.executable, "-c", "from dbmap.cli import run_api; run_api()"],
                    REPO,
                    env,
                )
            )
        else:
            running_database = running_health.get("database", {})
            if (
                running_database.get("database") != connection["database"]
                or running_database.get("user") != connection["user"]
            ):
                raise RuntimeError(
                    "Port 8000 is serving a different database/user. Stop the older API "
                    "process and run this command again."
                )
            print("API is already running on port 8000.")

        running_web = web_ready()
        if not running_web and port_open(3000):
            running_web = wait_for_existing_service(web_ready)
            if not running_web:
                raise RuntimeError(
                    "Port 3000 is occupied by a service that is not the Database Agent web UI."
                )

        if not running_web:
            shutil.rmtree(WEB_DIR / ".next", ignore_errors=True)
            npm = "npm.cmd" if os.name == "nt" else "npm"
            processes.append(start_process([npm, "run", "dev"], WEB_DIR, env))
        else:
            print("Web UI is already running on port 3000.")

        wait_for_services(processes)
        print(f"Database Agent is ready: {WEB_URL}")
        print(f"API health: {API_HEALTH_URL}")
        if not args.no_browser:
            webbrowser.open(WEB_URL)

        if not processes:
            return 0

        print("Press Ctrl+C to stop services started by this launcher.")
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        failed = next(process for process in processes if process.poll() is not None)
        raise RuntimeError(f"A service stopped unexpectedly with code {failed.returncode}.")
    except KeyboardInterrupt:
        print("\nStopping Database Agent...")
        return 0
    finally:
        for process in reversed(processes):
            stop_process_tree(process)


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Startup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
