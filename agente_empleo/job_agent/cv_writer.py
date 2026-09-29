"""CV en inglés y en PDF, redactado por un modelo a partir del perfil y de una oferta.

El modelo solo puede usar hechos del perfil. Se descartan salario, otras postulaciones,
proyectos personales y cualquier empleo o herramienta que no esté en el perfil.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config
from .llm import LocalModelError, complete_json
from .matcher import canon, extract_required_skills, normalize
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
        "Write every string in English, even if the source is Spanish. "
        "Use only facts from the candidate. Never invent employers, dates, tools, degrees, or metrics. "
        "Keep every real job and every course: do not drop roles. Keep company names unchanged. "
        "Omit personal projects, salary, other applications, interviews, and the job search itself. "
        "The summary is four or five lines about who the person is, aimed at the target role. "
        "Each job has three or four bullets taken from that job's description, leading with what overlaps the target role. "
        "Skills: only tools the candidate has, ordered with the ones this role asks for first. "
        "Education includes degrees and courses from the source."
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


def _field(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _role_records(draft: dict) -> list[dict]:
    raw = _field(draft, "experience", "experiencia", "jobs") or []
    if isinstance(raw, dict):
        raw = [raw]
    records = []
    for role in raw:
        if not isinstance(role, dict):
            continue
        bullets = _field(role, "bullets", "logros", "responsabilidades", "description", "descripcion") or []
        if isinstance(bullets, str):
            bullets = [bullets]
        records.append({
            "title": str(_field(role, "title", "puesto", "cargo") or ""),
            "company": str(_field(role, "company", "empresa") or ""),
            "period": str(_field(role, "period", "periodo", "fechas") or ""),
            "bullets": [str(item) for item in bullets],
        })
    return records


def _bullets_from_description(text: str) -> list[str]:
    parts = re.split(r"[\n•;]+|(?<=[.])\s+", text or "")
    return [part.strip(" -") for part in parts if len(part.strip(" -")) > 25][:4]


def _ordered_skills(profile: Profile, job: Job, known: str, drafted: list[str]) -> list[str]:
    required = extract_required_skills(normalize(job.full_text), profile)
    first = [skill for skill in profile.habilidades if canon(skill) in required]
    rest = [skill for skill in profile.habilidades if skill not in first]
    ordered = first + rest
    for skill in drafted:
        label = str(skill).strip()
        if label and _allowed_skill(label, known) and label not in ordered:
            ordered.append(label)
    return ordered[:16]


def sanitize(profile: Profile, job: Job, draft: dict) -> dict:
    """Completa con el perfil real y tira empresas, sueldos o herramientas inventadas."""
    known = _known_text(profile)
    model_roles = _role_records(draft)
    experience = []
    for source in profile.experiencia:
        company = source.get("empresa") or ""
        title = source.get("puesto") or ""
        if _PERSONAL.search(company) or _PERSONAL.search(title):
            continue
        match = next((role for role in model_roles if _same_company(role["company"], company)), None)
        bullets = []
        if match:
            bullets = [cleaned for item in match["bullets"] if (cleaned := _clean_sentence(item))]
            title = _clean_sentence(match["title"]) or title
        if not bullets:
            bullets = _bullets_from_description(source.get("descripcion") or "")
        experience.append({
            "title": title,
            "company": company,
            "period": (match or {}).get("period") or source.get("periodo") or "",
            "bullets": bullets[:4],
        })

    education = []
    drafted_education = _field(draft, "education", "educacion", "cursos", "courses") or []
    if isinstance(drafted_education, str):
        drafted_education = [drafted_education]
    tokens = _education_tokens(profile)
    for item in drafted_education:
        text = _clean_sentence(str(item))
        if text and any(token and token in normalize(text) for token in tokens):
            education.append(text)
    if not education:
        education = [str(item) for item in (profile.raw.get("educacion") or []) if str(item).strip()]

    summary = _clean_sentence(str(_field(draft, "summary", "resumen", "profile", "perfil") or ""))
    if len(summary) < 160:
        extra = _clean_sentence(profile.resumen)
        summary = f"{summary} {extra}".strip() if extra and extra not in summary else summary

    drafted_skills = _field(draft, "skills", "habilidades", "conocimientos") or []
    if isinstance(drafted_skills, str):
        drafted_skills = [part.strip() for part in drafted_skills.split(",")]
    languages = _field(draft, "languages", "idiomas") or profile.idiomas
    if isinstance(languages, str):
        languages = [languages]
    headline = _clean_sentence(str(_field(draft, "headline", "titular") or "")) or profile.titulo_actual
    return {
        "headline": headline,
        "summary": summary,
        "skills": _ordered_skills(profile, job, known, drafted_skills),
        "learning": [skill for skill in profile.aprendiendo if canon(skill) in extract_required_skills(normalize(job.full_text), profile)],
        "experience": experience,
        "education": education,
        "languages": [str(item).strip() for item in languages if str(item).strip()][:6],
    }


def _education_tokens(profile: Profile) -> list[str]:
    tokens = []
    for item in profile.raw.get("educacion") or []:
        tokens.extend(token for token in re.findall(r"[a-z0-9]{5,}", normalize(str(item))))
        tokens.extend(token.lower() for token in re.findall(r"\b[A-Z]{2,}\b", str(item)))
    return tokens


def _is_spanish(cv: dict) -> bool:
    sample = " ".join([
        cv.get("summary") or "",
        " ".join(bullet for role in cv.get("experience") or [] for bullet in role.get("bullets") or []),
    ])
    markers = _SPANISH.findall(sample) if (_SPANISH := re.compile(
        r"[áéíóúñ]|(\b(de|del|con|para|años|experiencia|sobre|entre|desde|hacia)\b)", re.I
    )) else []
    return len(markers) >= 3


def translate_to_english(cv: dict) -> dict:
    """Segundo paso, corto, para cuando el modelo local contestó en español."""
    try:
        translated = complete_json(
            [
                {"role": "system", "content": (
                    "Translate this CV JSON to English. Keep every job, course, skill, date, and company name. "
                    "Do not add or remove entries. Return the same JSON shape: "
                    '{"headline","summary","skills","learning","experience":[{"title","company","period","bullets"}],"education","languages"}.'
                )},
                {"role": "user", "content": json.dumps(cv, ensure_ascii=False)},
            ],
            timeout=180,
        )
    except LocalModelError:
        return cv
    if not isinstance(translated, dict):
        return cv
    translated.setdefault("learning", cv.get("learning") or [])
    return translated


def generate_with_llm(profile: Profile, job: Job) -> dict:
    try:
        return complete_json(build_messages(profile, job), timeout=180)
    except LocalModelError as exc:
        raise CVError(str(exc)) from exc


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
        if cv.get("learning"):
            text("Currently learning: " + ", ".join(cv["learning"]), 10.5, height=5.2)

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
        section("Education and courses")
        for item in cv["education"]:
            text(item, 10.5, height=5.2)

    if cv.get("languages"):
        section("Languages")
        text(", ".join(cv["languages"]), 10.5, height=5.2)

    pdf.output(str(path))


def write_cv(profile: Profile, job: Job, directory: Path | None = None, *, generator=None, translate: bool = True) -> Path:
    folder = directory or CVS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    draft = (generator or generate_with_llm)(profile, job)
    cv = sanitize(profile, job, draft)
    if translate and _is_spanish(cv):
        cv = sanitize(profile, job, translate_to_english(cv))
    if not cv["experience"]:
        raise CVError("El perfil no tiene experiencia laboral para armar el CV.")
    path = folder / f"{job.id}-{_slug(job.title)}-{_slug(job.company)}.pdf"
    write_pdf(path, profile, cv)
    return path
