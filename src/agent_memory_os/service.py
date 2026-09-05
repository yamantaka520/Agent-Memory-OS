"""Native service installation for the Web UI: run at login/boot on all three OSes.

- macOS: launchd LaunchAgent (`~/Library/LaunchAgents/<label>.plist`)
- Linux: systemd user unit (`~/.config/systemd/user/<name>.service`)
- Windows: Task Scheduler logon task (`schtasks /SC ONLOGON`)

All variants run `<current python> -m agent_memory_os.web_app` so the service
uses exactly the environment it was installed from (venvs included). Nothing
here needs admin rights; units are per-user. On Linux, add
`loginctl enable-linger $USER` if the service must start at boot without a
login session.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .constants import SYSTEMD_RESTART_DELAY_SECONDS
from .tokens import resolve_home

SERVICE_LABEL = "com.agent-memory-os.web"
SERVICE_NAME = "agent-memory-web"


def _sanitized_username() -> str:
    import getpass
    import re as _re

    try:
        raw = getpass.getuser()
    except Exception:  # noqa: BLE001 - no login database, etc.
        raw = "user"
    clean = _re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return clean or "user"


def windows_task_name() -> str:
    """Per-account Task Scheduler name.

    Unlike launchd LaunchAgents and systemd user units — which live in the
    account's own domain, so every account can use the same name — Windows
    Task Scheduler names in the root folder are MACHINE-GLOBAL. Two accounts
    installing plain "agent-memory-web" would silently overwrite each other
    (`/Create /F` replaces the task, changing which user it runs as). The
    username suffix keeps each account's task distinct.
    """
    return f"{SERVICE_NAME}-{_sanitized_username()}"


# Pre-suffix installs used the bare name; uninstall keeps removing it too.
LEGACY_WINDOWS_TASK_NAME = SERVICE_NAME


@dataclass
class ServiceConfig:
    home: Path
    host: str = "127.0.0.1"
    port: int = 8000
    python: str = field(default_factory=lambda: sys.executable)

    @property
    def arguments(self) -> list[str]:
        return [
            self.python, "-m", "agent_memory_os.web_app",
            "--host", self.host, "--port", str(self.port),
            "--home", str(self.home),
        ]

    @property
    def log_path(self) -> Path:
        return self.home / "logs" / "web.log"


def render_launchd_plist(config: ServiceConfig) -> str:
    payload = {
        "Label": SERVICE_LABEL,
        "ProgramArguments": config.arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(config.log_path),
        "StandardErrorPath": str(config.log_path),
    }
    return plistlib.dumps(payload).decode()


def render_systemd_unit(config: ServiceConfig) -> str:
    exec_start = " ".join(config.arguments)
    return f"""[Unit]
Description=Agent Memory OS Web console
After=network.target

[Service]
ExecStart={exec_start}
Restart=on-failure
RestartSec={SYSTEMD_RESTART_DELAY_SECONDS}

[Install]
WantedBy=default.target
"""


def build_schtasks_create(config: ServiceConfig) -> list[str]:
    pythonw = Path(config.python).with_name("pythonw.exe")
    launcher = str(pythonw) if pythonw.exists() else config.python
    command = " ".join(
        [f'"{launcher}"'] + [f'"{part}"' if " " in part else part for part in config.arguments[1:]]
    )
    return [
        "schtasks", "/Create", "/TN", windows_task_name(), "/TR", command,
        "/SC", "ONLOGON", "/F",
    ]


def _unit_path(platform: str) -> Path:
    if platform == "darwin":
        return Path("~/Library/LaunchAgents").expanduser() / f"{SERVICE_LABEL}.plist"
    return Path("~/.config/systemd/user").expanduser() / f"{SERVICE_NAME}.service"


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def _run_required(command: list[str]) -> subprocess.CompletedProcess:
    result = _run(command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"service command failed ({result.returncode}): {' '.join(command)}{suffix}"
        )
    return result


def install(config: ServiceConfig, *, platform: str = sys.platform, dry_run: bool = False) -> list[str]:
    """Install and start the login service; returns the actions performed."""
    actions: list[str] = []
    config.log_path.parent.mkdir(parents=True, exist_ok=True)
    if platform == "darwin":
        path = _unit_path(platform)
        actions.append(f"write {path}")
        commands = [
            ["launchctl", "bootout", f"gui/{_uid()}/{SERVICE_LABEL}"],  # replace quietly
            ["launchctl", "bootstrap", f"gui/{_uid()}", str(path)],
        ]
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_launchd_plist(config))
        for index, command in enumerate(commands):
            actions.append(" ".join(command))
            if not dry_run:
                if index == 0:
                    _run(command)  # replacing an absent unit may fail harmlessly
                else:
                    _run_required(command)
    elif platform.startswith("linux"):
        path = _unit_path(platform)
        actions.append(f"write {path}")
        commands = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"],
        ]
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_systemd_unit(config))
        for command in commands:
            actions.append(" ".join(command))
            if not dry_run:
                _run_required(command)
        actions.append("hint: loginctl enable-linger $USER  # start at boot without login")
    elif platform == "win32":
        create = build_schtasks_create(config)
        run_now = ["schtasks", "/Run", "/TN", windows_task_name()]
        for command in (create, run_now):
            actions.append(" ".join(command))
            if not dry_run:
                _run_required(command)
    else:
        raise RuntimeError(f"unsupported platform: {platform}")
    return actions


def uninstall(*, platform: str = sys.platform, dry_run: bool = False) -> list[str]:
    actions: list[str] = []
    if platform == "darwin":
        commands = [["launchctl", "bootout", f"gui/{_uid()}/{SERVICE_LABEL}"]]
        path = _unit_path(platform)
    elif platform.startswith("linux"):
        commands = [["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"]]
        path = _unit_path(platform)
    elif platform == "win32":
        commands = [
            ["schtasks", "/Delete", "/TN", windows_task_name(), "/F"],
            # Installs made before the per-account suffix used the bare name.
            ["schtasks", "/Delete", "/TN", LEGACY_WINDOWS_TASK_NAME, "/F"],
        ]
        path = None
    else:
        raise RuntimeError(f"unsupported platform: {platform}")
    for command in commands:
        actions.append(" ".join(command))
        if not dry_run:
            _run(command)
    if path is not None:
        actions.append(f"remove {path}")
        if not dry_run and path.exists():
            path.unlink()
    return actions


def control(action: str, *, platform: str = sys.platform) -> subprocess.CompletedProcess:
    """start / stop / restart / status for the installed service."""
    if platform == "darwin":
        commands = {
            "start": ["launchctl", "kickstart", f"gui/{_uid()}/{SERVICE_LABEL}"],
            "stop": ["launchctl", "bootout", f"gui/{_uid()}/{SERVICE_LABEL}"],
            "restart": ["launchctl", "kickstart", "-k", f"gui/{_uid()}/{SERVICE_LABEL}"],
            "status": ["launchctl", "print", f"gui/{_uid()}/{SERVICE_LABEL}"],
        }
    elif platform.startswith("linux"):
        commands = {
            "start": ["systemctl", "--user", "start", f"{SERVICE_NAME}.service"],
            "stop": ["systemctl", "--user", "stop", f"{SERVICE_NAME}.service"],
            "restart": ["systemctl", "--user", "restart", f"{SERVICE_NAME}.service"],
            "status": ["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"],
        }
    elif platform == "win32":
        commands = {
            "start": ["schtasks", "/Run", "/TN", windows_task_name()],
            "stop": ["schtasks", "/End", "/TN", windows_task_name()],
            "status": ["schtasks", "/Query", "/TN", windows_task_name()],
        }
        if action == "restart":
            _run(commands["stop"])
            return _run(commands["start"])
    else:
        raise RuntimeError(f"unsupported platform: {platform}")
    return _run(commands[action])


def make_config(home: str | Path | None, host: str, port: int) -> ServiceConfig:
    return ServiceConfig(home=resolve_home(home), host=host, port=port)


def _uid() -> int:
    import os

    # os.getuid does not exist on Windows; only reachable there in dry-run
    # previews of the darwin flow, where any stable placeholder is fine.
    return os.getuid() if hasattr(os, "getuid") else 0


def status_info(*, platform: str = sys.platform) -> dict:
    """Structured install/run state of this account's Web UI service.

    Never raises: on hosts without the service manager available the fields
    degrade to installed=False / running=None with the error in `detail`.
    """
    info: dict = {"platform": platform, "installed": False, "running": None,
                  "unit": None, "detail": ""}
    try:
        if platform == "darwin":
            path = _unit_path(platform)
            info["unit"] = str(path)
            info["installed"] = path.exists()
            result = _run(["launchctl", "print", f"gui/{_uid()}/{SERVICE_LABEL}"])
            info["running"] = result.returncode == 0 and "state = running" in (result.stdout or "")
            if result.returncode != 0:
                info["detail"] = (result.stderr or "").strip()[:200]
        elif platform.startswith("linux"):
            path = _unit_path(platform)
            info["unit"] = str(path)
            info["installed"] = path.exists()
            result = _run(["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"])
            info["running"] = (result.stdout or "").strip() == "active"
            info["detail"] = (result.stdout or result.stderr or "").strip()[:200]
        elif platform == "win32":
            task = windows_task_name()
            info["unit"] = task
            result = _run(["schtasks", "/Query", "/TN", task])
            info["installed"] = result.returncode == 0
            info["running"] = "Running" in (result.stdout or "") if result.returncode == 0 else None
            if result.returncode != 0:
                info["detail"] = (result.stderr or "").strip()[:200]
        else:
            info["detail"] = f"unsupported platform: {platform}"
    except Exception as exc:  # noqa: BLE001 - status must never crash the CLI
        info["detail"] = str(exc)
    return info


def running_under_systemd() -> bool:
    """True when this process was started by systemd (it sets INVOCATION_ID
    for every unit it launches)."""
    import os

    return bool(os.environ.get("INVOCATION_ID"))


def systemd_self_update(*, pip_runner=None, killer=None) -> bool:
    """Upgrade in-process, then self-exit so the service manager restarts us.

    Under systemd the detached-updater flow cannot work: the spawned updater
    lives in the unit's cgroup and the default KillMode reaps it the moment
    the main process stops — pip never finishes and the node silently stays
    on the old version. Instead: run pip to completion while the server keeps
    serving, then terminate ourselves; Restart=always brings the console back
    on the new code. Returns True when the restart was triggered, False when
    the upgrade failed (we stay up on the current version).
    """
    import os
    import signal

    run = pip_runner or (lambda: subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "agent-memory-os"],
        capture_output=True, text=True,
    ))
    result = run()
    if getattr(result, "returncode", 1) != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        print(f"systemd self-update failed; staying on the current version: {detail[-500:]}",
              file=sys.stderr, flush=True)
        return False
    (killer or (lambda: os.kill(os.getpid(), signal.SIGTERM)))()
    return True
