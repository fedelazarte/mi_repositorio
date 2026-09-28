from __future__ import annotations

import os
from pathlib import Path

# Todos los archivos del agente (perfil, base de datos) viven en JOB_AGENT_HOME
# o, por defecto, en el directorio de trabajo actual.
HOME = Path(os.environ.get("JOB_AGENT_HOME", ".")).resolve()
PROFILE_PATH = HOME / "perfil.yaml"
EXAMPLE_PROFILE_PATH = Path(__file__).resolve().parent.parent / "perfil.ejemplo.yaml"
DB_PATH = HOME / "job_agent.db"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("JOB_AGENT_LLM_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Pausa entre requests a LinkedIn (segundos). Ser amable evita bloqueos.
REQUEST_DELAY = float(os.environ.get("JOB_AGENT_REQUEST_DELAY", "2.0"))
