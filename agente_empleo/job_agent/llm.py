"""Refinamiento opcional del matching con un modelo de lenguaje (API compatible con OpenAI).

Se activa con `--llm` si existe la variable de entorno OPENAI_API_KEY. El puntaje final mezcla
la heurística (que es determinística y barata) con la evaluación del modelo (que entiende
contexto: por ejemplo, que "experiencia en BI" cubre "Power BI o Tableau").
"""
from __future__ import annotations

import json
import logging

import requests

from . import config
from .models import Job, MatchResult
from .profile import Profile

log = logging.getLogger(__name__)

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
    return bool(config.OPENAI_API_KEY)


def score_with_llm(job: Job, profile: Profile, heuristic: MatchResult, weight_llm: float = 0.6) -> MatchResult:
    if not available():
        return heuristic
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
        resp = requests.post(
            f"{config.OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={
                "model": config.OPENAI_MODEL,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
        llm_score = float(data.get("puntaje", heuristic.score))
    except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
        log.warning("No pude usar el LLM para %s: %s. Uso solo la heurística.", job.id, exc)
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
