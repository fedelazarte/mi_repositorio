"""Etapa de conocimiento del candidato, anterior a la búsqueda continua.

Combina tres fuentes en un `perfil.yaml`:

1. un CV (txt, md, pdf o docx),
2. el perfil de LinkedIn (página pública o ZIP de "descargar mis datos"),
3. un cuestionario corto sobre restricciones que un CV no dice
   (remoto excluyente, relocation, salario, disponibilidad, empresas a evitar).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

from . import config
from .cv_parser import CVError, parse_cv_file
from .profile import ProfileError
from .sources.linkedin_profile import LinkedInProfileError, fetch_public_profile, parse_export

InputFn = Callable[[str], str]

_YES = {"si", "sí", "s", "yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0"}


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in _YES:
        return True
    if text in _NO:
        return False
    raise ProfileError(f"No entendí '{value}' como sí/no.")


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [piece.strip() for piece in re.split(r"[,;]", value) if piece.strip()]
    return [str(piece).strip() for piece in value if str(piece).strip()]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = _norm_key(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


class Prompter:
    """Pregunta por consola, o responde desde un dict/`respuestas.yaml` en los tests y en modo no interactivo."""

    def __init__(self, answers: dict | None = None, input_fn: InputFn = input, output_fn: Callable[[str], None] = print):
        self.answers = answers
        self.input_fn = input_fn
        self.output_fn = output_fn

    def text(self, key: str, prompt: str, default: str = "") -> str:
        if self.answers is not None:
            if key not in self.answers or self.answers[key] is None:
                return default
            return str(self.answers[key]).strip()
        shown = f" [{default}]" if default else ""
        raw = self.input_fn(f"{prompt}{shown}: ").strip()
        return raw or default

    def yes_no(self, key: str, prompt: str, default: bool = False) -> bool:
        if self.answers is not None:
            return _as_bool(self.answers.get(key), default) if key in self.answers else default
        shown = "sí" if default else "no"
        while True:
            raw = self.input_fn(f"{prompt} (sí/no) [{shown}]: ").strip()
            if not raw:
                return default
            try:
                return _as_bool(raw)
            except ProfileError:
                self.output_fn("Respondé sí o no.")

    def items(self, key: str, prompt: str, default: list[str] | None = None) -> list[str]:
        default = default or []
        if self.answers is not None:
            return _as_list(self.answers[key]) if key in self.answers else list(default)
        shown = f" [{', '.join(default)}]" if default else ""
        raw = self.input_fn(f"{prompt}{shown}: ").strip()
        return _as_list(raw) if raw else list(default)

    def number(self, key: str, prompt: str) -> float | None:
        if self.answers is not None:
            raw = self.answers.get(key)
            if raw in (None, ""):
                return None
            return float(raw)
        raw = self.input_fn(f"{prompt}: ").strip().replace(",", "")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            self.output_fn("Lo tomo como 'sin mínimo'.")
            return None


def _blank() -> dict:
    return {
        "nombre": "",
        "titulo_actual": "",
        "anios_experiencia": 0,
        "resumen": "",
        "ubicacion_actual": "",
        "experiencia": [],
        "educacion": [],
        "habilidades": [],
        "idiomas": [],
        "contacto": {"email": "", "telefono": "", "linkedin": ""},
        "aspiraciones": {},
        "busqueda": {},
        "seguimiento": {},
        "notificaciones": {},
    }


def _fill(current: str, new: str, overwrite: bool) -> str:
    new = (new or "").strip()
    current = (current or "").strip()
    if overwrite and new:
        return new
    return current or new


def _merge_experience(base: list[dict], extra: list[dict]) -> list[dict]:
    merged: list[dict] = [dict(item) for item in base if isinstance(item, dict)]
    index = {_norm_key(f"{e.get('puesto')}|{e.get('empresa')}"): e for e in merged}
    for item in extra:
        if not isinstance(item, dict):
            continue
        key = _norm_key(f"{item.get('puesto')}|{item.get('empresa')}")
        if not key.strip("|"):
            continue
        existing = index.get(key)
        if existing is None:
            copied = dict(item)
            merged.append(copied)
            index[key] = copied
        elif item.get("descripcion") and len(item["descripcion"]) > len(existing.get("descripcion") or ""):
            existing["descripcion"] = item["descripcion"]
            existing["periodo"] = existing.get("periodo") or item.get("periodo") or ""
    return merged


def merge_drafts(base: dict | None, *drafts: dict, overwrite: bool = False) -> dict:
    """Une borradores. Las listas se suman; los textos se completan (o se pisan con `overwrite`)."""
    merged = _blank()
    if base:
        merged.update({k: v for k, v in base.items() if k != "contacto"})
        merged["contacto"] = {**merged["contacto"], **(base.get("contacto") or {})}
        for key in ("aspiraciones", "busqueda", "seguimiento", "notificaciones"):
            merged[key] = dict(base.get(key) or {})
        merged["experiencia"] = [dict(e) for e in base.get("experiencia") or [] if isinstance(e, dict)]
        merged["habilidades"] = list(base.get("habilidades") or [])
        merged["idiomas"] = list(base.get("idiomas") or [])
        merged["educacion"] = list(base.get("educacion") or [])

    for draft in drafts:
        if not draft:
            continue
        for field in ("nombre", "titulo_actual", "resumen", "ubicacion_actual"):
            merged[field] = _fill(str(merged.get(field) or ""), str(draft.get(field) or ""), overwrite)
        if draft.get("anios_experiencia") and (overwrite or not merged.get("anios_experiencia")):
            merged["anios_experiencia"] = int(draft["anios_experiencia"])
        merged["experiencia"] = _merge_experience(merged["experiencia"], draft.get("experiencia") or [])
        merged["educacion"] = _unique([str(e) for e in merged["educacion"]] + [str(e) for e in draft.get("educacion") or []])
        merged["habilidades"] = _unique([str(h) for h in merged["habilidades"]] + [str(h) for h in draft.get("habilidades") or []])
        merged["idiomas"] = _unique([str(i) for i in merged["idiomas"]] + [str(i) for i in draft.get("idiomas") or []])
        contact = draft.get("contacto") or {}
        for field in ("email", "telefono", "linkedin"):
            merged["contacto"][field] = _fill(merged["contacto"].get(field, ""), contact.get(field, ""), overwrite)
        if draft.get("contacto", {}).get("linkedin") and "linkedin.com/in/" in draft["contacto"]["linkedin"]:
            merged["contacto"]["linkedin"] = draft["contacto"]["linkedin"]
    return merged


def _suggest_queries(data: dict) -> list[dict]:
    aspiraciones = data.get("aspiraciones") or {}
    roles = aspiraciones.get("roles_objetivo") or []
    location = data.get("ubicacion_actual") or ""
    if not location:
        ubicaciones = aspiraciones.get("ubicaciones") or []
        location = ubicaciones[0] if ubicaciones else ""
    wants_remote = "remoto" in [m.lower() for m in aspiraciones.get("modalidad") or []]
    queries = []
    for role in roles[:4]:
        queries.append({"keywords": role, "location": location})
        if wants_remote:
            queries.append({"keywords": role, "location": location, "remoto": True})
    return queries


def apply_preferences(data: dict, prompter: Prompter) -> dict:
    """Completa aspiraciones con el cuestionario. Las respuestas pisan lo que hubiera en el YAML."""
    aspiraciones = dict(data.get("aspiraciones") or {})
    contacto = data.setdefault("contacto", {})

    aspiraciones["roles_objetivo"] = prompter.items(
        "roles_objetivo",
        "Roles a los que querés llegar (separados por coma)",
        aspiraciones.get("roles_objetivo") or ([data["titulo_actual"]] if data.get("titulo_actual") else []),
    )
    if not aspiraciones["roles_objetivo"]:
        raise ProfileError("Necesito al menos un rol objetivo para poder buscar.")

    remoto_excluyente = prompter.yes_no(
        "remoto_excluyente",
        "¿El remoto es excluyente? (si la oferta no es remota, no te interesa)",
        bool(aspiraciones.get("remoto_excluyente", False)),
    )
    aspiraciones["remoto_excluyente"] = remoto_excluyente
    if remoto_excluyente:
        aspiraciones["modalidad"] = ["remoto"]
    else:
        aspiraciones["modalidad"] = prompter.items(
            "modalidad",
            "Modalidades que aceptás (remoto, híbrido, presencial)",
            aspiraciones.get("modalidad") or ["remoto", "híbrido"],
        )

    relocation = prompter.yes_no("relocation", "¿Te interesa relocation (mudarte por el trabajo)?", bool(aspiraciones.get("relocation", False)))
    aspiraciones["relocation"] = relocation
    if relocation:
        aspiraciones["relocation_destinos"] = prompter.items(
            "relocation_destinos",
            "¿A qué ciudades o países te mudarías?",
            aspiraciones.get("relocation_destinos") or [],
        )
    else:
        aspiraciones["relocation_destinos"] = []

    data["ubicacion_actual"] = prompter.text("ubicacion_actual", "¿Dónde vivís hoy?", data.get("ubicacion_actual") or "")
    ubicaciones = list(aspiraciones.get("ubicaciones") or [])
    if data["ubicacion_actual"]:
        ubicaciones.insert(0, data["ubicacion_actual"])
    if relocation:
        ubicaciones.extend(aspiraciones["relocation_destinos"])
    aspiraciones["ubicaciones"] = _unique(ubicaciones) or ubicaciones

    aspiraciones["seniority"] = prompter.items(
        "seniority", "Seniority que buscás (junior, semi senior, senior, lead, manager)", aspiraciones.get("seniority") or ["semi senior", "senior"]
    )
    aspiraciones["industrias_preferidas"] = prompter.items(
        "industrias", "Industrias preferidas", aspiraciones.get("industrias_preferidas") or []
    )
    aspiraciones["evitar"] = prompter.items(
        "evitar", "Palabras que descalifican una oferta (ej. ventas, pasantía, comisión)", aspiraciones.get("evitar") or []
    )
    aspiraciones["empresas_evitar"] = prompter.items(
        "empresas_evitar", "Empresas a las que no te postularías", aspiraciones.get("empresas_evitar") or []
    )
    aspiraciones["aprendiendo"] = prompter.items(
        "aprendiendo", "Habilidades que estás aprendiendo (no cuentan como brecha grave)", aspiraciones.get("aprendiendo") or []
    )
    aspiraciones["tipo_contrato"] = prompter.items(
        "tipo_contrato", "Tipo de contrato (full-time, part-time, contractor)", aspiraciones.get("tipo_contrato") or ["full-time"]
    )
    aspiraciones["disponibilidad"] = prompter.text(
        "disponibilidad", "¿En cuánto podrías empezar? (inmediata, 15 días, 30 días, a convenir)", aspiraciones.get("disponibilidad") or ""
    )
    aspiraciones["viaje"] = prompter.text(
        "viaje", "¿Aceptás viajar? (no, ocasional, frecuente)", aspiraciones.get("viaje") or "ocasional"
    ).lower()
    salario = prompter.number("salario_minimo", "Salario mínimo mensual (vacío si no querés filtrar)")
    if salario:
        aspiraciones["salario_minimo"] = salario
        aspiraciones["moneda"] = prompter.text("moneda", "Moneda de ese mínimo (USD, ARS, EUR)", aspiraciones.get("moneda") or "USD").upper()
    else:
        aspiraciones.pop("salario_minimo", None)
    aspiraciones["autorizacion_trabajo"] = prompter.text(
        "autorizacion_trabajo",
        "¿Dónde podés trabajar legalmente? (ej. Argentina; visa en trámite para España)",
        aspiraciones.get("autorizacion_trabajo") or "",
    )
    motivacion = prompter.text(
        "motivacion",
        "En una frase: ¿qué tiene que tener tu próximo rol para que valga la pena?",
        aspiraciones.get("motivacion") or "",
    )
    aspiraciones["motivacion"] = motivacion
    if motivacion and motivacion.lower() not in (data.get("resumen") or "").lower():
        data["resumen"] = f"{data.get('resumen') or ''} {motivacion}".strip()

    contacto["email"] = prompter.text("email", "Mail para avisarte cuando haya un match de 85% o más", contacto.get("email") or "")

    data["aspiraciones"] = aspiraciones
    data.setdefault("notificaciones", {})
    data["notificaciones"].setdefault("email", True)
    data["notificaciones"].setdefault("umbral_match", 85)
    data.setdefault("seguimiento", {})
    data["seguimiento"].setdefault("dias_sin_respuesta_para_avisar", 10)
    data["seguimiento"].setdefault("dias_para_marcar_sin_respuesta", 30)
    data["seguimiento"].setdefault("puntaje_minimo_para_recomendar", 80)
    busqueda = data.setdefault("busqueda", {})
    if not busqueda.get("consultas"):
        busqueda["consultas"] = _suggest_queries(data)
    busqueda.setdefault("publicado_ultimos_dias", 7)
    busqueda.setdefault("max_resultados_por_consulta", 25)
    return data


def dump_profile(data: dict, path: Path) -> None:
    header = (
        "# Perfil generado por `python -m job_agent conocer`.\n"
        "# Podés editarlo a mano. `buscar` lo usa tal cual.\n"
    )
    path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# La primera vez que corre `conocer`, el YAML (incluido el de ejemplo) no es tu historia laboral.
_BIO_FIELDS = ("nombre", "titulo_actual", "anios_experiencia", "resumen", "experiencia", "educacion", "habilidades", "idiomas", "ubicacion_actual")


def _load_base(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ProfileError(f"{path} no tiene un perfil válido.")
    if not loaded.get("fuentes"):
        for key in _BIO_FIELDS:
            loaded.pop(key, None)
        contacto = loaded.get("contacto") or {}
        contacto.pop("linkedin", None)
        loaded["contacto"] = contacto
    return loaded


def _print_summary(data: dict, output) -> None:
    output(f"\nPerfil de {data.get('nombre') or '(sin nombre)'} — {data.get('titulo_actual') or '(sin título)'}")
    output(f"  Experiencia : {len(data.get('experiencia') or [])} roles, {data.get('anios_experiencia') or '?'} años")
    output(f"  Habilidades : {', '.join(data.get('habilidades') or []) or '-'}")
    output(f"  Roles       : {', '.join(data['aspiraciones'].get('roles_objetivo') or [])}")
    modalidad = ", ".join(data["aspiraciones"].get("modalidad") or [])
    if data["aspiraciones"].get("remoto_excluyente"):
        modalidad += " (excluyente)"
    output(f"  Modalidad   : {modalidad}")
    relocation = "sí" if data["aspiraciones"].get("relocation") else "no"
    destinos = data["aspiraciones"].get("relocation_destinos") or []
    output(f"  Relocation  : {relocation}" + (f" → {', '.join(destinos)}" if destinos else ""))
    output(f"  Avisos      : {data.get('contacto', {}).get('email') or '(sin mail)'} cuando el match sea ≥ {data['notificaciones'].get('umbral_match', 85):.0f}")


def run_onboarding(
    *,
    cv_path: Path | None = None,
    linkedin_url: str | None = None,
    linkedin_export: Path | None = None,
    answers: dict | None = None,
    answers_path: Path | None = None,
    overwrite: bool = False,
    profile_path: Path | None = None,
    input_fn: InputFn = input,
    output_fn: Callable[[str], None] = print,
) -> dict:
    path = profile_path or config.PROFILE_PATH
    if answers is None and answers_path is not None:
        if not answers_path.exists():
            raise ProfileError(f"No encontré las respuestas en {answers_path}.")
        with answers_path.open(encoding="utf-8") as fh:
            answers = yaml.safe_load(fh) or {}

    drafts = []
    fuentes: dict[str, str] = {}
    if cv_path is not None:
        output_fn(f"Leyendo CV {cv_path.name}...")
        try:
            drafts.append(parse_cv_file(cv_path))
        except CVError as exc:
            raise ProfileError(str(exc)) from exc
        fuentes["cv"] = cv_path.name
    if linkedin_export is not None:
        output_fn(f"Leyendo exportación de LinkedIn {linkedin_export.name}...")
        try:
            drafts.append(parse_export(linkedin_export))
        except LinkedInProfileError as exc:
            raise ProfileError(str(exc)) from exc
        fuentes["linkedin_export"] = linkedin_export.name
    if linkedin_url:
        output_fn(f"Leyendo el perfil público de LinkedIn...")
        try:
            drafts.append(fetch_public_profile(linkedin_url))
        except LinkedInProfileError as exc:
            raise ProfileError(str(exc)) from exc
        fuentes["linkedin"] = linkedin_url

    data = merge_drafts(_load_base(path), *drafts, overwrite=overwrite)
    if not data.get("habilidades") and answers is None and not drafts:
        raise ProfileError("Pasá un CV (--cv), un perfil de LinkedIn (--linkedin o --export) o un perfil.yaml existente.")

    found = [data.get("nombre"), data.get("titulo_actual")]
    output_fn("Encontré: " + " — ".join(p for p in found if p) if any(found) else "No había datos previos: las preguntas arman el perfil desde cero.")
    if data.get("habilidades"):
        output_fn("Habilidades detectadas: " + ", ".join(data["habilidades"][:20]))

    data = apply_preferences(data, Prompter(answers, input_fn=input_fn, output_fn=output_fn))
    if not data.get("habilidades"):
        raise ProfileError("No detecté habilidades. Agregalas en el CV o en `habilidades` del perfil.")
    data["fuentes"] = {**(data.get("fuentes") or {}), **fuentes, "actualizado": date.today().isoformat()}
    dump_profile(data, path)
    _print_summary(data, output_fn)
    output_fn(f"\nPerfil guardado en {path}. Siguiente paso: `python -m job_agent buscar`.")
    return data
