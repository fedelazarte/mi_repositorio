import json

import pytest

from job_agent.config import llm_endpoint
from job_agent.llm import LocalModelError, complete_json, parse_json_content


def test_endpoint_stays_on_the_local_machine(monkeypatch):
    monkeypatch.delenv("JOB_AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("JOB_AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-change-the-server")
    endpoint = llm_endpoint()
    assert endpoint["base_url"] == "http://127.0.0.1:11434/v1"
    assert endpoint["model"] == "qwen2.5:7b"
    assert "openai.com" not in endpoint["base_url"]


def test_parse_json_accepts_a_fenced_reply():
    raw = """```json
{"headline": "Data Analyst", "skills": ["Python"]}
```"""
    assert parse_json_content(raw)["headline"] == "Data Analyst"


def test_connection_error_explains_how_to_install_ollama(monkeypatch):
    monkeypatch.delenv("JOB_AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    def refuse(*args, **kwargs):
        raise __import__("requests").ConnectionError("refused")

    monkeypatch.setattr("job_agent.llm.requests.post", refuse)
    with pytest.raises(LocalModelError) as error:
        complete_json([{"role": "user", "content": "hi"}])
    assert "ollama pull qwen2.5:7b" in str(error.value)
    assert "sk-" not in str(error.value)


def test_reads_json_from_the_local_server(monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"headline": "Analyst"})}}]}

    monkeypatch.setattr("job_agent.llm.requests.post", lambda *args, **kwargs: Response())
    assert complete_json([{"role": "user", "content": "hi"}])["headline"] == "Analyst"
