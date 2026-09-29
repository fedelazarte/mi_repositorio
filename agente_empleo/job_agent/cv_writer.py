"""CV en inglés, en PDF, orientado a una oferta concreta.

Usa solo datos del perfil: no inventa empleos, fechas ni habilidades. Traduce al inglés
el texto que está en español y deja primero lo que la oferta pide. El archivo no es
para seguir editándolo.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config
from .matcher import canon, extract_required_skills, normalize
from .models import Job
from .profile import Profile

CVS_DIR = config.HOME / "cvs"

_SPANISH = re.compile(
    r"[áéíóúñü]|(\b(de|del|la|el|los|las|con|para|por|años|experiencia|actualidad|en|una|un|y)\b)",
    re.I,
)
_MONTHS = {
    "enero": "January", "febrero": "February", "marzo": "March", "abril": "April",
    "mayo": "May", "junio": "June", "julio": "July", "agosto": "August",
    "septiembre": "September", "setiembre": "September", "octubre": "October",
    "noviembre": "November", "diciembre": "December",
}
_SKILL_EN = {
    "estadistica": "statistics",
    "ingles": "English",
    "portugues": "Portuguese",
    "aprendizaje automatico": "machine learning",
}
_FONT_PAIRS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    return slug[:40] or "role"


def _needs_translation(text: str) -> bool:
    return bool(text and _SPANISH.search(text))


def _skill_label(skill: str) -> str:
    return _SKILL_EN.get(canon(skill), skill)


def _period(text: str) -> str:
    translated = text
    translated = re.sub(r"\bactualidad\b", "Present", translated, flags=re.I)
    translated = re.sub(r"\bpresente\b", "Present", translated, flags=re.I)
    for spanish, english in _MONTHS.items():
        translated = re.sub(rf"\b{spanish}\b", english, translated, flags=re.I)
    translated = re.sub(r"\bde\b", "", translated, flags=re.I)
    return re.sub(r"\s+", " ", translated).replace(" -", " -").strip()


def _relevant_skills(profile: Profile, job: Job) -> tuple[list[str], list[str], list[str]]:
    required = extract_required_skills(normalize(job.full_text), profile)
    relevant, other = [], []
    for skill in profile.habilidades:
        bucket = relevant if canon(skill) in required else other
        bucket.append(_skill_label(skill))
    learning = [_skill_label(skill) for skill in profile.aprendiendo if canon(skill) in required]
    return relevant, other, learning


class _English:
    def __init__(self, translator):
        self.translator = translator
        self.cache: dict[str, str] = {}
        self.failed = False

    def __call__(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if text in self.cache:
            return self.cache[text]
        if not _needs_translation(text):
            self.cache[text] = text
            return text
        try:
            translated = (self.translator([text]) or [text])[0].strip()
        except Exception:
            translated = ""
        if not translated:
            self.failed = True
            translated = text
        self.cache[text] = translated
        return translated


def google_translate(texts: list[str]) -> list[str]:
    from deep_translator import GoogleTranslator

    translator = GoogleTranslator(source="auto", target="en")
    return [translator.translate(text) for text in texts]


def llm_translate(texts: list[str]) -> list[str]:
    import json

    import requests

    prompt = (
        "Translate each item to English. Keep company names, product names, dates and numbers unchanged. "
        "Do not add facts. Reply with a JSON array of strings in the same order.\n\n"
        + json.dumps(texts, ensure_ascii=False)
    )
    response = requests.post(
        f"{config.OPENAI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
        json={
            "model": config.OPENAI_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return JSON of the form {\"items\": [..]} with one English string per input."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = json.loads(response.json()["choices"][0]["message"]["content"])
    items = payload.get("items") or payload.get("translations") or []
    if len(items) != len(texts):
        raise ValueError("la traducción no devolvió la misma cantidad de textos")
    return [str(item) for item in items]


def default_translator(texts: list[str]) -> list[str]:
    if config.OPENAI_API_KEY:
        try:
            return llm_translate(texts)
        except Exception:
            pass
    return google_translate(texts)


def _summary(profile: Profile, job: Job, relevant: list[str], english: _English) -> str:
    years = f"{profile.anios_experiencia} years of experience" if profile.anios_experiencia else "experience"
    title = english(profile.titulo_actual) or "Professional"
    opening = f"{title} with {years}."
    body = english(profile.resumen)
    highlight = f"For a {job.title} role, the most relevant strengths are {', '.join(relevant)}." if relevant else ""
    return " ".join(part for part in (opening, body, highlight) if part)


def render_sections(profile: Profile, job: Job, english: _English) -> list[tuple[str, list[str]]]:
    relevant, other, learning = _relevant_skills(profile, job)
    sections: list[tuple[str, list[str]]] = [("Profile", [_summary(profile, job, relevant, english)])]
    skills: list[str] = []
    if relevant:
        skills.append("For this role: " + ", ".join(relevant))
    if other:
        skills.append("Also: " + ", ".join(other))
    if learning:
        skills.append("Currently learning (requested in this role): " + ", ".join(learning))
    sections.append(("Skills", skills or ["No skills listed in the profile."]))
    roles: list[str] = []
    for role in profile.experiencia:
        title = english(role.get("puesto") or "")
        company = role.get("empresa") or ""
        period = _period(english(role.get("periodo") or ""))
        header = " — ".join(part for part in (title, company) if part)
        if period:
            header = f"{header} ({period})" if header else period
        description = english(role.get("descripcion") or "")
        roles.append("\n".join(part for part in (header, description) if part))
    sections.append(("Experience", roles or ["No experience listed in the profile."]))
    education = [english(str(item)) for item in (profile.raw.get("educacion") or []) if str(item).strip()]
    if education:
        sections.append(("Education", education))
    if profile.idiomas:
        sections.append(("Languages", [", ".join(english(item) for item in profile.idiomas)]))
    return sections


def _font_pair() -> tuple[str, str]:
    for regular, bold in _FONT_PAIRS:
        if Path(regular).exists() and Path(bold).exists():
            return regular, bold
    raise FileNotFoundError("No encontré una fuente del sistema para escribir el PDF (Arial o DejaVu).")


def write_pdf(path: Path, profile: Profile, job: Job, sections: list[tuple[str, list[str]]], english: _English) -> None:
    from fpdf import FPDF

    from fpdf.enums import XPos, YPos

    regular, bold = _font_pair()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(16, 14, 16)
    pdf.add_page()
    pdf.add_font("Body", "", regular)
    pdf.add_font("Body", "B", bold)

    def paragraph(text: str, size: int, bold: bool = False, line_height: float = 5) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Body", "B" if bold else "", size)
        pdf.multi_cell(pdf.epw, line_height, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    paragraph(profile.nombre or "CV", 18, bold=True, line_height=8)
    title = english(profile.titulo_actual)
    if title:
        pdf.set_text_color(70, 70, 70)
        paragraph(title, 11, line_height=6)
        pdf.set_text_color(0, 0, 0)
    contact = "  ·  ".join(part for part in (profile.email, profile.telefono, profile.linkedin, english(profile.ubicacion_actual)) if part)
    if contact:
        paragraph(contact, 9, line_height=5)
    pdf.ln(2)

    for heading, blocks in sections:
        pdf.ln(2)
        paragraph(heading.upper(), 11, bold=True, line_height=6)
        y = pdf.get_y()
        pdf.set_draw_color(30, 30, 30)
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(2)
        for block in blocks:
            paragraph(block, 10, line_height=5)
            pdf.ln(1)
    pdf.output(str(path))


def write_cv(profile: Profile, job: Job, directory: Path | None = None, *, translator=None) -> tuple[Path, bool]:
    """Escribe el PDF y devuelve la ruta y si alguna frase no se pudo traducir."""
    folder = directory or CVS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    english = _English(translator or default_translator)
    sections = render_sections(profile, job, english)
    path = folder / f"{job.id}-{_slug(job.title)}-{_slug(job.company)}.pdf"
    write_pdf(path, profile, job, sections, english)
    return path, english.failed
