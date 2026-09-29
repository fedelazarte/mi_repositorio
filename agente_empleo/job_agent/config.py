from __future__ import annotations

import os
from pathlib import Path

# Todos los archivos del agente (perfil, base de datos) viven en JOB_AGENT_HOME
# o, por defecto, en el directorio de trabajo actual.
HOME = Path(os.environ.get("JOB_AGENT_HOME", ".")).resolve()
PROFILE_PATH = HOME / "perfil.yaml"
EXAMPLE_PROFILE_PATH = Path(__file__).resolve().parent.parent / "perfil.ejemplo.yaml"
DB_PATH = HOME / "job_agent.db"

# Modelo open source local. Ollama expone una API compatible con OpenAI en este puerto.
LOCAL_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
LOCAL_LLM_MODEL = "qwen2.5:7b"


def llm_endpoint() -> dict:
    """Servidor de modelo. Por defecto es Ollama en la máquina, no un servicio de la nube."""
    base = os.environ.get("JOB_AGENT_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or LOCAL_LLM_BASE_URL
    model = os.environ.get("JOB_AGENT_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or LOCAL_LLM_MODEL
    key = os.environ.get("JOB_AGENT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "ollama"
    return {"base_url": base.rstrip("/"), "model": model, "api_key": key}

# Pausa entre requests a LinkedIn (segundos). Ser amable evita bloqueos.
REQUEST_DELAY = float(os.environ.get("JOB_AGENT_REQUEST_DELAY", "2.0"))


def smtp_settings() -> dict | None:
    """Lee la config de mail en el momento del envío (así los tests pueden setear el entorno)."""
    host = os.environ.get("JOB_AGENT_SMTP_HOST")
    user = os.environ.get("JOB_AGENT_SMTP_USER")
    password = os.environ.get("JOB_AGENT_SMTP_PASSWORD")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(os.environ.get("JOB_AGENT_SMTP_PORT", "587")),
        "user": user,
        "password": password,
        "from": os.environ.get("JOB_AGENT_SMTP_FROM") or user,
        "tls": os.environ.get("JOB_AGENT_SMTP_TLS", "1") != "0",
        "ssl": os.environ.get("JOB_AGENT_SMTP_SSL", "0") == "1",
    }
