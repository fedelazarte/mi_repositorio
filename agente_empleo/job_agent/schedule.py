"""Corrida diaria: buscar ofertas y revisar el seguimiento.

En Mac se instala con launchd. En Linux se imprime la línea de cron.
La contraseña del mail se copia al agente de launchd porque esa sesión
no lee ~/.zshrc. Queda solo en ~/Library/LaunchAgents, fuera del repo.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

LABEL = "com.jobagent.diario"
ENV_KEYS = (
    "JOB_AGENT_SMTP_HOST",
    "JOB_AGENT_SMTP_PORT",
    "JOB_AGENT_SMTP_USER",
    "JOB_AGENT_SMTP_PASSWORD",
    "JOB_AGENT_SMTP_FROM",
    "JOB_AGENT_EMAIL_TO",
    "JOB_AGENT_SMTP_TLS",
    "JOB_AGENT_HOME",
)


def plist_dict(*, python: str, workdir: Path, hour: int, minute: int, log_path: Path, env: dict[str, str]) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "job_agent", "diario"],
        "WorkingDirectory": str(workdir),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": env,
    }


def cron_line(*, python: str, workdir: Path, hour: int, minute: int, log_path: Path) -> str:
    return f"{minute} {hour} * * * cd {workdir} && {python} -m job_agent diario >> {log_path} 2>&1"


def _env_from_process() -> dict[str, str]:
    env = {key: os.environ[key] for key in ENV_KEYS if os.environ.get(key)}
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    return env


def install_daily(*, workdir: Path, hour: int = 9, minute: int = 0) -> str:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("La hora tiene que estar entre 0 y 23, y los minutos entre 0 y 59.")
    python = sys.executable
    log_path = workdir / "diario.log"
    if sys.platform == "darwin":
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist_path = agents / f"{LABEL}.plist"
        payload = plist_dict(
            python=python,
            workdir=workdir,
            hour=hour,
            minute=minute,
            log_path=log_path,
            env=_env_from_process(),
        )
        plist_path.write_bytes(plistlib.dumps(payload))
        return str(plist_path)
    return cron_line(python=python, workdir=workdir, hour=hour, minute=minute, log_path=log_path)
