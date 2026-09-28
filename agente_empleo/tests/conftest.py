import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_agent.profile import Profile  # noqa: E402


@pytest.fixture
def profile() -> Profile:
    with (ROOT / "perfil.ejemplo.yaml").open(encoding="utf-8") as fh:
        return Profile(yaml.safe_load(fh))
