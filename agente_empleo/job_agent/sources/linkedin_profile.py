"""Lectura del perfil de LinkedIn del candidato.

Dos vías, de más completa a más liviana:

* el ZIP oficial de "Descargar mis datos" (Settings → Data privacy → Get a copy of your data),
  que trae experiencia, habilidades, educación e idiomas completos;
* la página pública `linkedin.com/in/<usuario>`, que un visitante ve sin iniciar sesión
  (nombre, titular, resumen y experiencia; las habilidades suelen estar ocultas).

No usa la sesión ni la contraseña de la cuenta.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..cv_parser import infer_years, skills_mentioned
from .linkedin import HEADERS, LinkedInError

PROFILE_URL_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
YEAR_RE = re.compile(r"(?:19|20)\d{2}")


class LinkedInProfileError(LinkedInError):
    pass


def normalize_profile_url(url: str) -> str:
    match = PROFILE_URL_RE.search(url.strip())
    if not match:
        raise LinkedInProfileError("La URL tiene que ser de un perfil: https://www.linkedin.com/in/<usuario>/")
    slug = match.group(1).strip("/")
    return f"https://www.linkedin.com/in/{slug}/"


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def parse_public_profile(html: str, url: str = "") -> dict:
    """Convierte el HTML público de un perfil en un borrador de `perfil.yaml`."""
    soup = BeautifulSoup(html, "html.parser")
    nombre = _text(soup.select_one("h1.top-card-layout__title, h1"))
    titulo = _text(soup.select_one("h2.top-card-layout__headline, h2.top-card-layout__headline"))
    if titulo.lower().startswith(nombre.lower()) and len(titulo) > len(nombre) + 2:
        titulo = titulo[len(nombre):].strip(" -|")

    ubicacion = ""
    for node in soup.select(
        "h3.top-card-layout__first-subline, div.profile-info-subheader span.top-card__subline-item, "
        "span.top-card__subline-item"
    ):
        candidate = _text(node)
        lowered = candidate.lower()
        if candidate and not any(word in lowered for word in ("contacto", "contact", "seguidor", "follower", "connection")):
            ubicacion = candidate
            break

    summary_node = soup.select_one("section.summary div.core-section-container__content, section.summary")
    resumen = _text(summary_node)
    resumen = re.sub(r"^(acerca de|about)\s*", "", resumen, flags=re.I).strip()

    experiencia = []
    section = soup.select_one("section.experience")
    items = section.select("li") if section else soup.select("li.experience-item")
    for item in items:
        puesto = _text(item.select_one("h3"))
        empresa = _text(item.select_one("h4, .experience-item__subtitle, span.experience-item__subtitle"))
        metas = [_text(m) for m in item.select(".experience-item__meta-item, .experience-item__meta-item span")]
        metas = [m for m in _unique(metas) if m and m not in (puesto, empresa)]
        periodo = next((m for m in metas if YEAR_RE.search(m)), "")
        descripcion = _text(item.select_one(".experience-item__description, .show-more-less-text"))
        if not puesto and not empresa:
            continue
        experiencia.append({"puesto": puesto, "empresa": empresa, "periodo": periodo, "descripcion": descripcion})

    educacion = []
    edu_section = soup.select_one("section.education")
    for item in edu_section.select("li") if edu_section else []:
        school = _text(item.select_one("h3"))
        rest = _text(item)
        if school and rest.lower().startswith(school.lower()):
            rest = rest[len(school):].strip(" -–—")
        line = school if not rest else f"{school} — {rest}"
        if line.strip(" —"):
            educacion.append(line)

    # JSON-LD a veces trae el nombre cuando el HTML visible está recortado.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for node in payload if isinstance(payload, list) else [payload]:
            person = node.get("author") if isinstance(node, dict) and node.get("@type") == "Article" else node
            if isinstance(person, dict) and person.get("@type") == "Person" and not nombre:
                nombre = person.get("name") or nombre

    if not nombre and not experiencia:
        raise LinkedInProfileError(
            "LinkedIn no mostró el perfil (pide iniciar sesión o bloqueó la consulta). "
            "Descargá tus datos desde LinkedIn (Ajustes → Privacidad de datos → Obtener una copia) "
            "y pasá el ZIP con --export."
        )

    visible = soup.get_text("\n", strip=True)
    return {
        "nombre": nombre,
        "titulo_actual": titulo,
        "resumen": resumen,
        "ubicacion_actual": ubicacion,
        "experiencia": experiencia,
        "educacion": educacion,
        "habilidades": skills_mentioned(visible),
        "idiomas": [],
        "anios_experiencia": infer_years(experiencia),
        "contacto": {"linkedin": url, "email": "", "telefono": ""},
    }


def fetch_public_profile(url: str, session: requests.Session | None = None) -> dict:
    profile_url = normalize_profile_url(url)
    http = session or requests.Session()
    http.headers.update(HEADERS)
    try:
        resp = http.get(profile_url, timeout=25)
    except requests.RequestException as exc:
        raise LinkedInProfileError(f"No pude abrir el perfil: {exc}") from exc
    if resp.status_code != 200:
        raise LinkedInProfileError(
            f"LinkedIn respondió HTTP {resp.status_code}. Si el perfil no es público, usá --export con el ZIP de tus datos."
        )
    draft = parse_public_profile(resp.text, profile_url)
    draft["contacto"]["linkedin"] = profile_url
    return draft


# ---------------------------------------------------------------- export ZIP --
def _read_csv(archive: zipfile.ZipFile, basename: str) -> list[dict]:
    match = next((name for name in archive.namelist() if Path(name).name.lower() == basename.lower()), None)
    if match is None:
        return []
    raw = archive.read(match).decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(raw)))


def _column(row: dict, *candidates: str) -> str:
    lowered = {key.strip().lower(): value for key, value in row.items() if key}
    for name in candidates:
        value = lowered.get(name.lower())
        if value and value.strip():
            return value.strip()
    return ""


def _period(start: str, end: str) -> str:
    start, end = start.strip(), end.strip()
    if start and end:
        return f"{start} - {end}"
    if start:
        return f"{start} - actualidad"
    return end


def parse_export_zip(path: Path) -> dict:
    """Lee el ZIP que LinkedIn genera cuando pedís una copia de tus datos."""
    if not path.exists():
        raise LinkedInProfileError(f"No encontré {path}.")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise LinkedInProfileError(f"{path.name} no es un ZIP válido.") from exc

    with archive:
        profile_rows = _read_csv(archive, "Profile.csv")
        positions = _read_csv(archive, "Positions.csv")
        skills = _read_csv(archive, "Skills.csv")
        education = _read_csv(archive, "Education.csv")
        languages = _read_csv(archive, "Languages.csv")

    if not profile_rows and not positions and not skills:
        raise LinkedInProfileError(
            "El ZIP no tiene Profile.csv, Positions.csv ni Skills.csv. "
            "Pedí en LinkedIn la exportación completa (no solo 'datos básicos')."
        )

    profile = profile_rows[0] if profile_rows else {}
    first = _column(profile, "First Name", "Nombre")
    last = _column(profile, "Last Name", "Apellido")
    experiencia = []
    for row in positions:
        experiencia.append(
            {
                "puesto": _column(row, "Title", "Cargo"),
                "empresa": _column(row, "Company Name", "Nombre de la empresa"),
                "periodo": _period(_column(row, "Started On", "Fecha de inicio"), _column(row, "Finished On", "Fecha de finalización")),
                "descripcion": _column(row, "Description", "Descripción"),
            }
        )
    experiencia = [e for e in experiencia if e["puesto"] or e["empresa"]]

    educacion = []
    for row in education:
        school = _column(row, "School Name", "Nombre de la institución")
        degree = _column(row, "Degree Name", "Título")
        notes = _column(row, "Notes", "Notas")
        period = _period(_column(row, "Start Date", "Fecha de inicio"), _column(row, "End Date", "Fecha de finalización"))
        line = " — ".join(part for part in (school, degree, notes, period) if part)
        if line:
            educacion.append(line)

    idiomas = []
    for row in languages:
        name = _column(row, "Name", "Nombre")
        level = _column(row, "Proficiency", "Nivel")
        if name:
            idiomas.append(f"{name} {level}".strip())

    habilidad_nombres = [_column(row, "Name", "Nombre") for row in skills]
    habilidad_nombres = [h for h in habilidad_nombres if h]
    texto = " ".join(
        [profile.get("Summary", ""), profile.get("Headline", "")]
        + [e["descripcion"] for e in experiencia]
        + habilidad_nombres
    )

    return {
        "nombre": f"{first} {last}".strip(),
        "titulo_actual": _column(profile, "Headline", "Titular"),
        "resumen": _column(profile, "Summary", "Extracto"),
        "ubicacion_actual": _column(profile, "Geo Location", "Ubicación"),
        "experiencia": experiencia,
        "educacion": educacion,
        "habilidades": _unique(habilidad_nombres + skills_mentioned(texto)),
        "idiomas": idiomas,
        "anios_experiencia": infer_years(experiencia),
        "contacto": {"linkedin": "", "email": "", "telefono": ""},
    }
