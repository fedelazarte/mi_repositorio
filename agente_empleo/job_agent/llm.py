"""Llamadas al modelo open source que corre en la máquina (Ollama por defecto).

También puede apuntar a cualquier servidor compatible con la API de OpenAI
(`JOB_AGENT_LLM_BASE_URL`), pero el proyecto no manda el perfil a la nube salvo que
cambies esa dirección.
"""
from __future__ import annotations

import json
import logging
import re

import requests

from . import config
from .models import Job, MatchResult
from .profile import Profile

log = logging.getLogger(__name__)

LOCAL_SETUP = """No llego al modelo local. Este proyecto usa un modelo open source en tu máquina, con Ollama.

  brew install ollama
  ollama pull qwen2.5:7b

Si Ollama no quedó corriendo solo: ollama serve
El modelo por defecto es qwen2.5:7b. Para usar otro: export JOB_AGENT_LLM_MODEL=llama3.1:8b
"""


class LocalModelError(Exception):
    pass

PROMPT = """Sos un coach de carrera experto en reclutamiento tech en Latinoamérica.
Evaluá qué tan buena es esta oferta para la persona, considerando su experiencia Y sus aspiraciones.

PERFIL:
{perfil}

ASPIRACIONES:
- Roles objetivo: {roles}
- Seniority buscado: {seniority}
- Modalidad: {modalidad}{remoto_excluyente}
- Relocation: {relocation}
- Ubicaciones: {ubicaciones}
- Salario mínimo: {salario}
- Evitar: {evitar}
- Empresas a evitar: {empresas}

OFERTA:
Título: {titulo}
Empresa: {empresa}
Ubicación: {ubicacion}
Seniority declarado: {job_seniority}
Descripción:
{descripcion}

Respondé SOLO con JSON válido con esta forma:
{{"puntaje": <0-100>, "razones": ["..."], "brechas": ["..."], "consejo": "una frase con cómo adaptar el CV o la carta para esta oferta"}}
"""


def available() -> bool:
    return True


def parse_json_content(content: str) -> dict:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def complete_json(messages: list[dict], *, timeout: int = 180) -> dict:
    """Pide un objeto JSON al modelo local. Lanza LocalModelError si Ollama no está."""
    endpoint = config.llm_endpoint()
    url = f"{endpoint['base_url']}/chat/completions"
    headers = {"Authorization": f"Bearer {endpoint['api_key']}"}
    payload = {"model": endpoint["model"], "temperature": 0.2, "messages": messages}
    try:
        response = requests.post(url, headers=headers, json={**payload, "response_format": {"type": "json_object"}}, timeout=timeout)
        if response.status_code == 400:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.ConnectionError as exc:
        raise LocalModelError(LOCAL_SETUP) from exc
    except requests.Timeout as exc:
        raise LocalModelError(
            f"El modelo local no respondió a tiempo ({endpoint['model']}). "
            "Un modelo más chico, por ejemplo llama3.2:3b, tarda menos."
        ) from exc
    if response.status_code == 404:
        raise LocalModelError(f"Ollama no tiene el modelo {endpoint['model']}. Corré: ollama pull {endpoint['model']}")
    try:
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return parse_json_content(content)
    except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        raise LocalModelError(f"El modelo local no devolvió JSON válido: {exc}") from exc


def score_with_llm(job: Job, profile: Profile, heuristic: MatchResult, weight_llm: float = 0.6) -> MatchResult:
    prompt = PROMPT.format(
        perfil=profile.texto[:4000],
        roles=", ".join(profile.roles_objetivo),
        seniority=", ".join(profile.seniority) or "sin preferencia",
        modalidad=", ".join(profile.modalidad) or "sin preferencia",
        remoto_excluyente=" (excluyente: si no es remoto, no sirve)" if profile.remoto_excluyente else "",
        relocation=("sí, hacia " + ", ".join(profile.relocation_destinos)) if profile.relocation else "no",
        ubicaciones=", ".join(profile.ubicaciones) or "sin preferencia",
        salario=f"{profile.salario_minimo:.0f} {profile.moneda}" if profile.salario_minimo else "sin mínimo",
        evitar=", ".join(profile.evitar) or "-",
        empresas=", ".join(profile.empresas_evitar) or "-",
        titulo=job.title,
        empresa=job.company,
        ubicacion=job.location,
        job_seniority=job.seniority or "no indicado",
        descripcion=(job.description or "(sin descripción)")[:6000],
    )
    try:
        data = complete_json([{"role": "user", "content": prompt}], timeout=90)
        llm_score = float(data.get("puntaje", heuristic.score))
    except (LocalModelError, ValueError, TypeError) as exc:
        log.warning("No pude usar el modelo local para %s: %s. Uso solo la heurística.", job.id, exc)
        return heuristic

    blended = round((1 - weight_llm) * heuristic.score + weight_llm * llm_score, 1)
    reasons = list(dict.fromkeys([str(r) for r in data.get("razones", [])] + heuristic.reasons))
    gaps = list(dict.fromkeys([str(g) for g in data.get("brechas", [])] + heuristic.gaps))
    return MatchResult(
        job_id=job.id,
        score=max(0.0, min(100.0, blended)),
        reasons=reasons,
        gaps=gaps,
        method="heuristico+llm",
        advice=str(data.get("consejo", "")),
    )
