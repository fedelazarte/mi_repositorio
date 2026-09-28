"""Lectura del perfil de LinkedIn del candidato.

Tres vías, de más completa a más liviana:

* el ZIP oficial de "Descargar mis datos" (Settings → Data privacy → Get a copy of your data),
  que trae experiencia, habilidades, educación e idiomas completos;
* el PDF que genera el botón "Más → Guardar como PDF" del propio perfil
  (nombre, titular, extracto, experiencia, educación, aptitudes principales e idiomas);
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

from ..cv_parser import EMAIL_RE, LINKEDIN_RE, PHONE_RE, infer_years, skills_mentioned
from ..matcher import normalize
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


def parse_export(path: Path) -> dict:
    """Acepta el ZIP de 'descargar mis datos' o el PDF de 'guardar perfil como PDF'."""
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return parse_export_zip(path)
    if suffix == ".pdf":
        return parse_profile_pdf(path)
    raise LinkedInProfileError(
        f"No sé leer '{path.name}'. Pasá el ZIP de 'Obtener una copia de tus datos' o el PDF de "
        "'Más → Guardar como PDF' de tu perfil."
    )


# ---------------------------------------------------------------- perfil PDF --
PDF_SECTIONS = {
    "contacto": ["contactar", "contact", "contacto"],
    "habilidades": ["aptitudes principales", "principales aptitudes", "top skills", "skills", "aptitudes", "conocimientos y aptitudes"],
    "idiomas": ["languages", "idiomas"],
    "certificaciones": ["certifications", "certificaciones", "licencias y certificaciones"],
    "otros": ["honors-awards", "honores y premios", "publications", "publicaciones", "patents", "patentes", "courses", "cursos", "projects", "proyectos"],
    "resumen": ["extracto", "summary", "acerca de", "about"],
    "experiencia": ["experiencia", "experience"],
    "educacion": ["educación", "educacion", "education"],
}
PDF_HEADER_TO_SECTION = {normalize(name): key for key, names in PDF_SECTIONS.items() for name in names}
LEFT_SECTIONS = {"contacto", "habilidades", "idiomas", "certificaciones", "otros"}
PLACE_WORDS = {"remoto", "remote", "hibrido", "presencial", "buenos aires", "caba", "latam", "latin america"}

PAGE_RE = re.compile(r"^(page|página|pagina)\s+\d+\s+(of|de)\s+\d+$", re.I)
MONTH = r"(?:[a-záéíóú]+\.?\s+(?:de\s+)?)?"
DATE_LINE_RE = re.compile(
    rf"^{MONTH}(?:19|20)\d{{2}}\s*[-–]\s*(?:present|presente|actualidad|hoy|now|{MONTH}(?:19|20)\d{{2}})(?:\s*\(.*\))?$",
    re.I,
)
DURATION_RE = re.compile(r"^\d+\s+(?:años?|years?|meses|months?|mes|month)(?:\s+\d+\s+(?:meses|months?|mes|month))?$", re.I)
EDU_DETAIL_RE = re.compile(r"[·(]|\b(?:19|20)\d{2}\b")


def _pdf_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or PAGE_RE.match(line):
            continue
        lines.append(line)
    return lines


COUNTRIES = {
    "argentina", "uruguay", "chile", "paraguay", "bolivia", "peru", "colombia", "ecuador", "venezuela", "mexico", "brasil", "brazil",
    "espana", "spain", "portugal", "estados unidos", "united states", "canada", "alemania", "germany", "francia", "france",
    "italia", "italy", "reino unido", "united kingdom", "irlanda", "ireland", "paises bajos", "netherlands", "costa rica", "panama",
    "republica dominicana", "guatemala", "el salvador", "honduras", "nicaragua", "cuba", "puerto rico",
}


def _looks_like_name(line: str) -> bool:
    words = line.split()
    return 2 <= len(words) <= 5 and len(line) <= 60 and not any(ch.isdigit() for ch in line) and not re.search(r"[|()@/:,]", line)


def _looks_like_place(line: str) -> bool:
    norm = normalize(line).strip()
    return bool(line) and len(line) <= 60 and ("," in line or norm in PLACE_WORDS or norm in COUNTRIES)


def _sentence_like(line: str) -> bool:
    """Una descripción termina en punto y es larga; 'Empresa Real S.A.' termina en punto pero es corta."""
    return line.endswith((".", ",", ";", ":")) and len(line.split()) > 6


def _looks_like_company(line: str) -> bool:
    return len(line) <= 70 and not _sentence_like(line) and not DATE_LINE_RE.match(line) and not DURATION_RE.match(line)


def _is_location(line: str, nxt: str, nxt2: str, multi_role: bool) -> bool:
    """La línea que sigue a las fechas suele ser la ciudad. Se distingue de un título o una empresa nueva."""
    if not line or line.endswith(".") or DATE_LINE_RE.match(nxt) or not _looks_like_place(line):
        return False
    # Si le siguen [título, fecha] y no estamos dentro de una empresa con varios roles, es una empresa nueva.
    if DATE_LINE_RE.match(nxt2) and not multi_role and "," not in line:
        return False
    return True


def _parse_pdf_experience(lines: list[str]) -> list[dict]:
    roles: list[dict] = []
    company = ""
    multi_role = False
    i = 0
    n = len(lines)

    def at(k: int) -> str:
        return lines[k] if 0 <= k < n else ""

    while i < n:
        line = lines[i]
        nxt, nxt2 = at(i + 1), at(i + 2)
        if DURATION_RE.match(nxt) and _looks_like_company(line):
            company, multi_role = line, True
            i += 2
            continue
        if DATE_LINE_RE.match(nxt2) and not DATE_LINE_RE.match(nxt) and _looks_like_company(line) and not (
            multi_role and roles and roles[-1]["empresa"] == company and _sentence_like(line)
        ):
            company, multi_role = line, False
            i += 1
            continue
        if DATE_LINE_RE.match(nxt):
            role = {"puesto": line, "empresa": company, "periodo": nxt, "descripcion": ""}
            roles.append(role)
            i += 2
            if _is_location(at(i), at(i + 1), at(i + 2), multi_role):
                role["ubicacion"] = at(i)
                i += 1
            continue
        if roles:
            roles[-1]["descripcion"] = f"{roles[-1]['descripcion']} {line}".strip()
        i += 1
    for role in roles:
        role["periodo"] = re.sub(r"\s*\(.*\)$", "", role["periodo"])
        role["periodo"] = re.sub(r"\b(present|presente|now|hoy)\b", "actualidad", role["periodo"], flags=re.I)
    return roles


def _parse_pdf_education(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        school = lines[i]
        detail = lines[i + 1] if i + 1 < len(lines) else ""
        if detail and EDU_DETAIL_RE.search(detail) and not EDU_DETAIL_RE.search(school):
            out.append(f"{school} — {detail.replace(' · ', ' ')}")
            i += 2
        else:
            out.append(school)
            i += 1
    return out


def parse_profile_pdf_text(text: str) -> dict:
    """Interpreta el texto del PDF 'Guardar como PDF' de un perfil de LinkedIn."""
    lines = _pdf_lines(text)
    sections: dict[str, list[str]] = {}
    left_tail: list[str] = []  # líneas de la columna izquierda: ahí queda también el bloque nombre/titular/ubicación
    current: str | None = None
    for line in lines:
        header = PDF_HEADER_TO_SECTION.get(normalize(line).strip(" :"))
        if header:
            current = header
            sections.setdefault(current, [])
            continue
        if current is None or current in LEFT_SECTIONS:
            left_tail.append(line)
        sections.setdefault(current or "contacto", []).append(line)

    # El bloque nombre / titular [/ ubicación] es lo último que aparece antes de Extracto o Experiencia.
    nombre = titulo = ubicacion = ""
    tail = left_tail[-3:]
    if len(tail) == 3 and _looks_like_name(tail[0]) and _looks_like_place(tail[2]):
        nombre, titulo, ubicacion = tail
    elif len(tail) >= 2 and _looks_like_name(tail[-2]):
        nombre, titulo = tail[-2], tail[-1]
    else:
        for idx in range(len(left_tail) - 1, -1, -1):
            if _looks_like_name(left_tail[idx]):
                nombre = left_tail[idx]
                titulo = left_tail[idx + 1] if idx + 1 < len(left_tail) else ""
                break

    def clean(items: list[str]) -> list[str]:
        return [item for item in items if item not in (nombre, titulo, ubicacion)]

    habilidades = clean(sections.get("habilidades", []))
    idiomas = clean(sections.get("idiomas", []))
    resumen = " ".join(sections.get("resumen", []))
    experiencia = _parse_pdf_experience(sections.get("experiencia", []))
    educacion = _parse_pdf_education(sections.get("educacion", []))

    contact_text = "\n".join(sections.get("contacto", []))
    email = EMAIL_RE.search(contact_text) or EMAIL_RE.search(text)
    linkedin = LINKEDIN_RE.search(contact_text) or LINKEDIN_RE.search(text)
    phone = PHONE_RE.search(contact_text)

    if not nombre and not experiencia:
        raise LinkedInProfileError("El PDF no parece ser el de un perfil de LinkedIn (no encontré nombre ni experiencia).")

    body = " ".join([resumen, titulo] + [f"{e['puesto']} {e['descripcion']}" for e in experiencia] + habilidades)
    return {
        "nombre": nombre,
        "titulo_actual": titulo,
        "resumen": resumen,
        "ubicacion_actual": ubicacion,
        "experiencia": experiencia,
        "educacion": educacion,
        "habilidades": _unique(habilidades + skills_mentioned(body)),
        "idiomas": idiomas,
        "anios_experiencia": infer_years(experiencia),
        "contacto": {
            "linkedin": linkedin.group(0) if linkedin else "",
            "email": email.group(0) if email else "",
            "telefono": phone.group(0).strip() if phone else "",
        },
    }


def parse_profile_pdf(path: Path) -> dict:
    if not path.exists():
        raise LinkedInProfileError(f"No encontré {path}.")
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf lanza varias excepciones propias
        raise LinkedInProfileError(f"No pude leer {path.name}: {exc}") from exc
    if not text.strip():
        raise LinkedInProfileError(f"{path.name} no tiene texto extraíble (¿es una imagen escaneada?).")
    return parse_profile_pdf_text(text)


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
