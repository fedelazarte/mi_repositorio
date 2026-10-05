"""Lectura de un CV (txt, markdown, pdf o docx) y extracción de un borrador de perfil."""
from __future__ import annotations

import re
from pathlib import Path

from .matcher import LANGUAGE_KEYWORDS, SKILL_CATALOG, SKILL_PATTERNS, _contains, canon, normalize

_LANGUAGE_CANON = {canon(name) for name in LANGUAGE_KEYWORDS}

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:[\w.]+\.)?linkedin\.com/in/[A-Za-z0-9_\-%]+", re.I)
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4}")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")
LOCATION_RE = re.compile(r"(?:ubicaci[oó]n|location|residencia|ciudad)\s*[:\-]\s*(.+)", re.I)

SECTION_NAMES = {
    "resumen": ["resumen", "perfil profesional", "perfil", "about", "summary", "objetivo", "acerca de"],
    "experiencia": ["experiencia laboral", "experiencia", "experience", "work experience", "historial laboral", "empleo"],
    "educacion": ["educacion", "education", "formacion academica", "formacion", "estudios"],
    "habilidades": ["habilidades", "skills", "competencias", "tecnologias", "stack", "herramientas"],
    "idiomas": ["idiomas", "languages"],
}
HEADER_TO_SECTION = {normalize(name): key for key, names in SECTION_NAMES.items() for name in names}
SKIP_NAME = {
    "curriculum vitae", "curriculum", "cv", "resume", "hoja de vida", "perfil profesional",
    "datos personales", "informacion personal", "informacion de contacto", "contacto",
    "datos de contacto", "personal information", "personal details",
}


def _is_decoration(line: str) -> bool:
    """Líneas de adorno o títulos de sección, no un nombre ni un puesto."""
    stripped = line.strip()
    letters = re.sub(r"[^A-Za-zÁÉÍÓÚáéíóúÑñ]", "", stripped)
    if len(letters) < 2:
        return True
    label = normalize(re.sub(r"[^a-zA-ZáéíóúñÁÉÍÓÚ ]", " ", stripped))
    label = re.sub(r"\s+", " ", label).strip()
    return label in SKIP_NAME or label in HEADER_TO_SECTION

ROLE_RE = re.compile(
    r"^(?P<puesto>[^|\n]{2,80}?)\s*[|\-–—]\s*(?P<empresa>[^|\n]{2,80}?)\s*[|\-–—(]\s*(?P<periodo>[^)\n]*\d{4}[^)\n]*)\)?\s*$"
)
BULLET_RE = re.compile(r"^[-*•·]\s+")


class CVError(Exception):
    pass


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        from docx import Document

        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    raise CVError(f"Formato no soportado ({suffix}). Usá .txt, .md, .pdf o .docx.")


def skills_mentioned(text: str) -> list[str]:
    """Habilidades del catálogo que aparecen en el texto, en forma canónica y sin duplicados."""
    norm = normalize(text)
    found: list[str] = []
    seen: set[str] = set()
    for skill in sorted(SKILL_CATALOG, key=len, reverse=True):
        key = normalize(skill)
        pattern = SKILL_PATTERNS.get(key)
        hit = bool(pattern.search(norm)) if pattern else _contains(key, norm)
        if not hit:
            continue
        name = canon(skill)
        if name in _LANGUAGE_CANON or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def _is_header(line: str) -> str | None:
    cleaned = normalize(line).strip(" :.-")
    if not cleaned or len(cleaned) > 40:
        return None
    return HEADER_TO_SECTION.get(cleaned)


def _split_sections(text: str) -> tuple[list[str], dict[str, list[str]]]:
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        header = _is_header(line)
        if header:
            current = header
            sections.setdefault(current, [])
            continue
        if current is None:
            preamble.append(line)
        else:
            sections.setdefault(current, []).append(line)
    return preamble, sections


def _parse_experience(lines: list[str]) -> list[dict]:
    roles: list[dict] = []
    for line in lines:
        body = BULLET_RE.sub("", line).strip()
        match = ROLE_RE.match(body)
        if match:
            roles.append(
                {
                    "puesto": match.group("puesto").strip(" -|"),
                    "empresa": match.group("empresa").strip(" -|"),
                    "periodo": match.group("periodo").strip(" -|)"),
                    "descripcion": "",
                }
            )
            continue
        if roles and body:
            extra = body if not roles[-1]["descripcion"] else f"{roles[-1]['descripcion']} {body}"
            roles[-1]["descripcion"] = extra
    return roles


def _list_from_lines(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        for piece in re.split(r"[,;•|]", BULLET_RE.sub("", line)):
            piece = piece.strip(" -–—")
            if 1 < len(piece) <= 60:
                items.append(piece)
    return _unique(items)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = normalize(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _guess_name(preamble: list[str]) -> str:
    for line in preamble:
        if _is_decoration(line) or "@" in line or "linkedin.com" in line.lower():
            continue
        words = [word for word in re.sub(r"[-=_*#|]+", " ", line).split() if word]
        if 2 <= len(words) <= 5 and len(line) <= 60 and not any(ch.isdigit() for ch in line):
            return " ".join(words)
    return ""


def _guess_title(preamble: list[str], name: str, experiencia: list[dict]) -> str:
    for line in preamble:
        if line == name or _is_decoration(line) or "@" in line or "linkedin.com" in line.lower() or len(line) > 80:
            continue
        if not any(ch.isdigit() for ch in line):
            return line.strip()
    return experiencia[0]["puesto"] if experiencia else ""


def infer_years(experiencia: list[dict]) -> int:
    years: list[int] = []
    for exp in experiencia:
        years.extend(int(y) for y in YEAR_RE.findall(exp.get("periodo") or ""))
    if not years:
        return 0
    from datetime import date

    return max(0, min(60, date.today().year - min(years)))


def parse_cv_text(text: str) -> dict:
    """Convierte el texto de un CV en un borrador compatible con `perfil.yaml`."""
    preamble, sections = _split_sections(text)
    experiencia = _parse_experience(sections.get("experiencia", []))
    habilidades = _list_from_lines(sections.get("habilidades", []))
    habilidades = _unique(habilidades + skills_mentioned(text))
    nombre = _guess_name(preamble)
    email = EMAIL_RE.search(text)
    linkedin = LINKEDIN_RE.search(text)
    phone = PHONE_RE.search(text)
    location = LOCATION_RE.search(text)
    resumen_lines = sections.get("resumen", [])
    stated_years = re.search(r"(\d{1,2})\s+anos de experiencia", normalize(text))
    return {
        "nombre": nombre,
        "titulo_actual": _guess_title(preamble, nombre, experiencia),
        "anios_experiencia": int(stated_years.group(1)) if stated_years else infer_years(experiencia),
        "resumen": " ".join(resumen_lines).strip(),
        "experiencia": experiencia,
        "educacion": sections.get("educacion", []),
        "habilidades": habilidades,
        "idiomas": _list_from_lines(sections.get("idiomas", [])),
        "ubicacion_actual": location.group(1).strip() if location else "",
        "contacto": {
            "email": email.group(0) if email else "",
            "telefono": phone.group(0).strip() if phone else "",
            "linkedin": linkedin.group(0) if linkedin else "",
        },
    }


def parse_cv_file(path: Path) -> dict:
    if not path.exists():
        raise CVError(f"No encontré el archivo {path}.")
    text = read_document(path)
    if not text.strip():
        raise CVError(f"{path.name} no tiene texto extraíble.")
    draft = parse_cv_text(text)
    draft["fuente_cv"] = path.name
    return draft
