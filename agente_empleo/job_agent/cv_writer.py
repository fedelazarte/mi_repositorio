"""CV en inglés y en PDF, redactado por un modelo a partir del perfil y de una oferta.

El modelo solo puede usar hechos del perfil. Se descartan salario, otras postulaciones,
proyectos personales y cualquier empleo o herramienta que no esté en el perfil.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from . import config
from .matcher import canon, normalize
from .models import Job
from .profile import Profile

CVS_DIR = config.HOME / "cvs"

_FORBIDDEN = re.compile(
    r"\b(salary|compensation|expected pay|pay expectation|salario|remuneracion|pretension|"
    r"other applications|otras postulaciones|i applied|me postul|postulacion|applying to)\b",
    re.I,
)
_PERSONAL = re.compile(r"\b(personal project|side project|proyecto personal|hobby|portfolio project)\b", re.I)
_FONT_PAIRS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]


class CVError(Exception):
    pass


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    return slug[:40] or "role"


def source_material(profile: Profile, job: Job) -> dict:
    """Lo único que el modelo puede ver. Sin salario, sin postulaciones, sin preferencias de búsqueda."""
    return {
        "name": profile.nombre,
        "current_title": profile.titulo_actual,
        "location": profile.ubicacion_actual,
        "email": profile.email,
        "phone": profile.telefono,
        "linkedin": profile.linkedin,
        "summary": profile.resumen,
        "skills": profile.habilidades,
        "languages": profile.idiomas,
        "experience": [
            {
                "title": role.get("puesto") or "",
                "company": role.get("empresa") or "",
                "period": role.get("periodo") or "",
                "description": role.get("descripcion") or "",
            }
            for role in profile.experiencia
            if role.get("puesto") or role.get("empresa")
        ],
        "education": [str(item) for item in (profile.raw.get("educacion") or []) if str(item).strip()],
        "target_role": {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "description": (job.description or "")[:5000],
        },
    }


def build_messages(profile: Profile, job: Job) -> list[dict]:
    system = (
        "You write one-page CVs in natural professional English. "
        "Return JSON only, with this shape: "
        '{"headline": "", "summary": "", "skills": [""], '
        '"experience": [{"title": "", "company": "", "period": "", "bullets": [""]}], '
        '"education": [""], "languages": [""]}. '
        "Rules: use only facts present in the candidate material. Never invent employers, dates, tools, degrees, or metrics. "
        "Keep company names exactly as written. "
        "Include only real employment. Omit personal projects, side projects, hobbies, and student exercises. "
        "Never mention salary, compensation, other applications, interviews, or the fact that the person is job hunting. "
        "Do not mention relocation, notice period, or visas. "
        "Tailor the wording to the target role: keep what overlaps, drop what does not help this application. "
        "At most four bullets per role, each one a concrete responsibility or result already supported by the source. "
        "The summary is three or four lines, without 'I' and without phrases like 'for this role'. "
        "Skills: between 8 and 14 items the candidate actually has, ordered by relevance to the target role. "
        "Do not add a skill just because the job description asks for it."
    )
    user = "CANDIDATE AND TARGET ROLE:\n" + json.dumps(source_material(profile, job), ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _same_company(left: str, right: str) -> bool:
    a, b = normalize(left), normalize(right)
    return bool(a and b and (a in b or b in a))


def _known_text(profile: Profile) -> str:
    chunks = [profile.resumen, profile.titulo_actual, " ".join(profile.habilidades)]
    for role in profile.experiencia:
        chunks.append(" ".join(str(role.get(key) or "") for key in ("puesto", "empresa", "descripcion")))
    chunks.extend(str(item) for item in (profile.raw.get("educacion") or []))
    return normalize("\n".join(chunks))


def _allowed_skill(skill: str, known: str) -> bool:
    label = normalize(skill)
    if not label or _FORBIDDEN.search(skill) or _PERSONAL.search(skill):
        return False
    return label in known or canon(skill) in known


def _clean_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.])\s+", (text or "").strip())
    kept = [part.strip() for part in parts if part.strip() and not _FORBIDDEN.search(part) and not _PERSONAL.search(part)]
    return " ".join(kept)


def sanitize(profile: Profile, draft: dict) -> dict:
    """Tira lo que el modelo no tenía derecho a poner."""
    known = _known_text(profile)
    companies = [role.get("empresa") or "" for role in profile.experiencia]
    experience = []
    for role in draft.get("experience") or []:
        company = str(role.get("company") or "")
        if not any(_same_company(company, known_company) for known_company in companies):
            continue
        if _PERSONAL.search(str(role.get("title") or "")):
            continue
        bullets = []
        for bullet in role.get("bullets") or []:
            cleaned = _clean_sentence(str(bullet))
            if cleaned:
                bullets.append(cleaned)
        if not bullets and not role.get("title"):
            continue
        experience.append({
            "title": _clean_sentence(str(role.get("title") or "")) or str(role.get("title") or ""),
            "company": company,
            "period": str(role.get("period") or ""),
            "bullets": bullets[:4],
        })
    skills = []
    for skill in draft.get("skills") or []:
        label = str(skill).strip()
        if label and _allowed_skill(label, known) and label not in skills:
            skills.append(label)
    education = []
    for item in draft.get("education") or []:
        text = _clean_sentence(str(item))
        if text and any(token and token in normalize(text) for token in _education_tokens(profile)):
            education.append(text)
    return {
        "headline": _clean_sentence(str(draft.get("headline") or "")) or profile.titulo_actual,
        "summary": _clean_sentence(str(draft.get("summary") or "")),
        "skills": skills[:14],
        "experience": experience,
        "education": education,
        "languages": [str(item).strip() for item in (draft.get("languages") or []) if str(item).strip()][:6],
    }


def _education_tokens(profile: Profile) -> list[str]:
    tokens = []
    for item in profile.raw.get("educacion") or []:
        tokens.extend(token for token in re.findall(r"[a-z0-9]{5,}", normalize(str(item))))
        tokens.extend(token.lower() for token in re.findall(r"\b[A-Z]{2,}\b", str(item)))
    return tokens


def generate_with_llm(profile: Profile, job: Job) -> dict:
    if not config.OPENAI_API_KEY:
        raise CVError(
            "Para armar el CV hace falta un modelo. Definí OPENAI_API_KEY "
            "(y, si no es OpenAI, JOB_AGENT_LLM_MODEL y OPENAI_BASE_URL) y volvé a correr el comando."
        )
    try:
        response = requests.post(
            f"{config.OPENAI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            json={
                "model": config.OPENAI_MODEL,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": build_messages(profile, job),
            },
            timeout=90,
        )
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])
    except requests.RequestException as exc:
        raise CVError(f"No pude hablar con el modelo: {exc}") from exc
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
        raise CVError(f"El modelo no devolvió un CV válido: {exc}") from exc


def _font_pair() -> tuple[str, str]:
    for regular, bold in _FONT_PAIRS:
        if Path(regular).exists() and Path(bold).exists():
            return regular, bold
    raise CVError("No encontré una fuente del sistema para escribir el PDF (Arial o DejaVu).")


def write_pdf(path: Path, profile: Profile, cv: dict) -> None:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    regular, bold = _font_pair()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    pdf.add_font("Body", "", regular)
    pdf.add_font("Body", "B", bold)
    navy = (31, 58, 95)

    def text(value: str, size: int, *, bold: bool = False, height: float = 5, color: tuple[int, int, int] = (20, 20, 20)) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_text_color(*color)
        pdf.set_font("Body", "B" if bold else "", size)
        pdf.multi_cell(pdf.epw, height, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def section(title: str) -> None:
        pdf.ln(3)
        text(title, 12, bold=True, height=6, color=navy)
        pdf.set_draw_color(*navy)
        pdf.set_line_width(0.3)
        y = pdf.get_y()
        pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
        pdf.ln(2.5)

    text(profile.nombre or "CV", 20, bold=True, height=9)
    if cv.get("headline"):
        text(cv["headline"], 11, height=6, color=(70, 70, 70))
    contact = "   ·   ".join(part for part in (profile.email, profile.telefono, profile.linkedin, profile.ubicacion_actual) if part)
    if contact:
        text(contact, 9, height=5, color=(90, 90, 90))

    if cv.get("summary"):
        section("Profile")
        text(cv["summary"], 10.5, height=5.2)

    if cv.get("skills"):
        section("Skills")
        text(", ".join(cv["skills"]), 10.5, height=5.2)

    if cv.get("experience"):
        section("Experience")
        for role in cv["experience"]:
            left = " — ".join(part for part in (role.get("title") or "", role.get("company") or "") if part)
            period = role.get("period") or ""
            pdf.set_text_color(20, 20, 20)
            pdf.set_font("Body", "B", 11)
            pdf.set_x(pdf.l_margin)
            if period and pdf.get_string_width(left) + pdf.get_string_width(period) + 6 < pdf.epw:
                pdf.set_font("Body", "", 9)
                date_width = pdf.get_string_width(period) + 1
                pdf.set_font("Body", "B", 11)
                pdf.cell(pdf.epw - date_width, 6, left, new_x=XPos.RIGHT, new_y=YPos.TOP)
                pdf.set_font("Body", "", 9)
                pdf.set_text_color(90, 90, 90)
                pdf.cell(date_width, 6, period, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            else:
                text(left, 11, bold=True, height=6)
                if period:
                    text(period, 9, height=5, color=(90, 90, 90))
            for bullet in role.get("bullets") or []:
                text("•  " + bullet, 10.5, height=5.2)
            pdf.ln(1.5)

    if cv.get("education"):
        section("Education")
        for item in cv["education"]:
            text(item, 10.5, height=5.2)

    if cv.get("languages"):
        section("Languages")
        text(", ".join(cv["languages"]), 10.5, height=5.2)

    pdf.output(str(path))


def write_cv(profile: Profile, job: Job, directory: Path | None = None, *, generator=None) -> Path:
    folder = directory or CVS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    draft = (generator or generate_with_llm)(profile, job)
    cv = sanitize(profile, draft)
    if not cv["summary"] and not cv["experience"]:
        raise CVError("El modelo no dejó contenido usable. Probá de nuevo; no guardé el PDF.")
    path = folder / f"{job.id}-{_slug(job.title)}-{_slug(job.company)}.pdf"
    write_pdf(path, profile, cv)
    return path
