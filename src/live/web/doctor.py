"""
Why can the web chart not reach the bot? Run on the machine where the bot and the chart run:

    .venv/bin/python -m src.live.web.doctor

Checks live.yaml `web`, WEB_API_TOKEN in .env, whether the bot process is running,
whether anything listens on the web API port, and calls /api/health and /api/me.
Read-only: it changes nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv  # pyright: ignore[reportMissingModuleSource]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OK, BAD, INFO = "✅", "❌", "ℹ️ "


def _line(mark: str, text: str) -> None:
    print(f"{mark} {text}")


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _get(url: str, token: str = "") -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read(300).decode("utf-8", "replace")
    except Exception as error:  # connection refused, timeout, ...
        return 0, str(error)


def _bot_processes() -> list[str]:
    """Command lines of running live bots (works on Linux and macOS)."""
    try:
        result = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or not Path(parts[1]).name.startswith("python"):
            continue
        args = parts[2:]
        is_module = any(a == "-m" and b == "src.live.run" for a, b in zip(args, args[1:]))
        if is_module or any(a.endswith("src/live/run.py") for a in args):
            found.append(line.strip())
    return found


def _docker_bot() -> str:
    if not shutil.which("docker"):
        return ""
    result = subprocess.run(["docker", "ps", "--filter", "name=bot-trade", "--format", "{{.Names}} {{.Status}}"],
                            capture_output=True, text=True)
    return result.stdout.strip()


def _chart_target(port: int) -> str:
    env_file = PROJECT_ROOT / "chart" / ".env"
    target = os.environ.get("BOT_API_URL", "")
    if not target and env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("BOT_API_URL="):
                target = raw.split("=", 1)[1].strip().strip('"').strip("'")
    return target or f"http://127.0.0.1:8787 (default){'' if port == 8787 else '  <- port differs from web.port!'}"


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    from src.config import load_live_config
    from src.live.web.orders import load_web_config

    problems: list[str] = []
    print(f"Project: {PROJECT_ROOT}\n")

    web = load_web_config(load_live_config(include_strategy=False))
    host, port = str(web.get("host") or "127.0.0.1"), int(web.get("port") or 8787)
    if web.get("enabled"):
        _line(OK, f"live.yaml web.enabled: true (host {host}, port {port})")
    else:
        _line(BAD, "live.yaml web.enabled is false -> the bot never opens its web API")
        problems.append("Set `web: enabled: true` in src/config/live.yaml, then restart the bot.")
    if host not in ("127.0.0.1", "0.0.0.0", "localhost"):
        _line(BAD, f"web.host is {host}: the chart proxies to 127.0.0.1, which this host does not listen on")
        problems.append('Set `web: host: "127.0.0.1"` in src/config/live.yaml.')

    token = os.environ.get("WEB_API_TOKEN", "")
    if len(token) >= 16:
        _line(OK, f"WEB_API_TOKEN in .env ({len(token)} characters)")
    else:
        _line(BAD, "WEB_API_TOKEN missing or shorter than 16 characters -> the web API refuses to start")
        problems.append('Add WEB_API_TOKEN to .env: python -c "import secrets; print(secrets.token_urlsafe(24))"')

    processes = _bot_processes()
    docker = _docker_bot()
    if processes:
        _line(OK, f"bot process running: {processes[0][:100]}")
    elif docker:
        _line(BAD, f"bot runs in Docker ({docker}): its 127.0.0.1 is inside the container, the chart cannot reach it")
        problems.append("Run the bot with ./start.sh on the server (or map the port and use web.host 0.0.0.0 in Docker).")
    elif _listening("127.0.0.1", port):
        _line(INFO, "no `python -m src.live.run` process found (the bot may be started another way)")
    else:
        _line(BAD, "no bot process (python -m src.live.run) found")
        problems.append("Start the bot: ./start.sh (chart + bot) or .venv/bin/python -m src.live.run")

    if _listening("127.0.0.1", port):
        _line(OK, f"something listens on 127.0.0.1:{port}")
        status, body = _get(f"http://127.0.0.1:{port}/api/health")
        if status == 200 and '"ok"' in body:
            _line(OK, f"/api/health answers: {body.strip()[:120]}")
            status, body = _get(f"http://127.0.0.1:{port}/api/me", token)
            if status == 200:
                _line(OK, "/api/me accepts the token from .env (enter this same token in the chart)")
            else:
                _line(BAD, f"/api/me -> HTTP {status}: {body.strip()[:120]}")
                problems.append("The running bot uses another WEB_API_TOKEN: restart it after changing .env.")
        else:
            _line(BAD, f"port {port} answers but not like the bot (HTTP {status}): another program uses it")
            problems.append(f"Free port {port} or change web.port (and BOT_API_URL in chart/.env).")
    else:
        _line(BAD, f"nothing listens on 127.0.0.1:{port} (this is the ECONNREFUSED the chart shows)")
        if web.get("enabled") and len(token) >= 16 and processes:
            problems.append("The bot runs but its web API did not start: look for 'WEB: Web API disabled: ...' "
                            "in the bot dashboard errors (or res/logs) and restart the bot.")

    _line(INFO, f"chart proxies /api to: {_chart_target(port)}")
    log = PROJECT_ROOT / "res" / "logs" / "chart.log"
    if log.exists():
        errors = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if "proxy error" in line]
        if errors:
            _line(INFO, f"chart.log has {len(errors)} proxy error(s), last: {errors[-1].strip()[:120]}")

    print()
    if problems:
        print("Fix:")
        for number, problem in enumerate(dict.fromkeys(problems), 1):
            print(f"  {number}. {problem}")
        return 1
    print("All checks passed: the chart should connect. Enter the WEB_API_TOKEN from .env in 'Connect bot'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
