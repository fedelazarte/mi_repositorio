"""Matching perfil <-> oferta.

El puntaje (0-100) combina señales explicables, para que cada match venga con razones y brechas:

* habilidades: qué proporción de las habilidades que pide la oferta tenés,
* rol: qué tanto se parece el título al de tus roles objetivo,
* similitud textual (TF-IDF coseno) entre tu perfil completo y la descripción,
* seniority, modalidad y ubicación según tus aspiraciones,
* restricciones del cuestionario: remoto excluyente, relocation, salario, contrato, viaje y empresas a evitar,
* penalización por palabras que querés evitar.

Opcionalmente, `llm.score_with_llm` refina el puntaje con un modelo de lenguaje.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import Job, MatchResult
from .places import matching_preference
from .priority_companies import BONUS as PRIORITY_BONUS
from .priority_companies import priority_company as priority_company_name
from .profile import Profile

WEIGHTS = {
    "skills": 0.35,
    "title": 0.25,
    "text": 0.15,
    "seniority": 0.10,
    "modality": 0.08,
    "location": 0.07,
}

# Catálogo de habilidades frecuentes en ofertas de datos/tecnología. Se usa para detectar qué pide
# una oferta aunque vos no tengas esa habilidad (así aparecen las brechas). Se suma a tus habilidades.
SKILL_CATALOG = [
    "python", "r", "sql", "nosql", "scala", "java", "javascript", "typescript", "c++", "c#", "go", "rust",
    "pandas", "numpy", "scikit-learn", "sklearn", "tensorflow", "pytorch", "keras", "xgboost", "lightgbm",
    "machine learning", "deep learning", "nlp", "computer vision", "llm", "mlops", "estadística", "statistics",
    "a/b testing", "experimentación", "forecasting", "series de tiempo", "time series",
    "power bi", "tableau", "looker", "looker studio", "data studio", "qlik", "metabase", "superset",
    "excel", "google sheets",
    "bigquery", "snowflake", "redshift", "databricks", "postgresql", "postgres", "mysql", "sql server",
    "oracle", "mongodb", "elasticsearch", "clickhouse",
    "spark", "pyspark", "hadoop", "kafka", "airflow", "dbt", "prefect", "dagster", "luigi", "etl", "elt",
    "aws", "gcp", "google cloud", "azure", "docker", "kubernetes", "terraform", "linux", "bash",
    "git", "github", "gitlab", "ci/cd", "apis rest", "rest", "graphql", "fastapi", "flask", "django",
    "react", "node", "vue", "angular",
    "agile", "scrum", "jira", "kanban",
    "inglés", "english", "portugués", "portuguese",
    "sap", "salesforce", "hubspot", "google analytics", "ga4", "mixpanel", "amplitude",
]

SENIORITY_KEYWORDS = {
    "junior": ["junior", "jr", "trainee", "entry level", "entry-level", "intern", "pasante", "pasantía", "practicante"],
    "semi senior": ["semi senior", "semi-senior", "semisenior", "ssr", "mid level", "mid-level", "mid-senior", "intermediate"],
    "senior": ["senior", "sr", "sr."],
    "lead": ["lead", "líder", "lider", "principal", "staff", "tech lead"],
    "manager": ["manager", "gerente", "head of", "head", "director", "directora", "jefe", "jefa", "vp"],
}

MODALITY_KEYWORDS = {
    "remoto": ["remoto", "remota", "remote", "home office", "teletrabajo", "100% remoto"],
    "híbrido": ["híbrido", "hibrido", "hybrid"],
    "presencial": ["presencial", "on-site", "onsite", "on site", "in office", "in-office"],
}

MODALITY_ALIASES = {"remote": "remoto", "hybrid": "híbrido", "hibrido": "híbrido", "onsite": "presencial", "on-site": "presencial"}

# Variantes que significan lo mismo: se comparan por su forma canónica (ya normalizada, sin tildes).
SKILL_ALIASES = {
    "english": "ingles",
    "portuguese": "portugues",
    "sklearn": "scikit-learn",
    "postgres": "postgresql",
    "google cloud": "gcp",
    "data studio": "looker studio",
    "statistics": "estadistica",
    "time series": "series de tiempo",
    "rest": "apis rest",
    "github": "git",
    "gitlab": "git",
}

# Habilidades cuyo nombre es una palabra común: se detectan con un patrón específico para evitar falsos positivos.
SKILL_PATTERNS = {
    "go": re.compile(r"\b(golang|go lang|go/|/go\b|\(go\)|go programming)", re.I),
    "r": re.compile(r"\b(rstudio|r studio|tidyverse|ggplot|lenguaje r|r language|python/r|r/python|python, r\b|r, python|\(r\)|r y python|r and python)", re.I),
    "rest": re.compile(r"\b(rest ?api|api ?rest|apis? rest|restful)", re.I),
}

LANGUAGE_KEYWORDS = ["ingles", "english", "portugues", "portuguese", "aleman", "german", "frances", "french", "italiano", "italian"]

CONTRACT_ALIASES = {
    "full time": "full-time", "fulltime": "full-time", "jornada completa": "full-time", "tiempo completo": "full-time",
    "part time": "part-time", "parttime": "part-time", "media jornada": "part-time", "tiempo parcial": "part-time",
    "freelance": "contractor", "contractor": "contractor", "por contrato": "contractor",
    "pasantia": "pasantía", "internship": "pasantía", "trainee": "pasantía",
}
CONTRACT_PATTERNS = [
    ("full-time", re.compile(r"jornada completa|full[- ]?time|tiempo completo")),
    ("part-time", re.compile(r"part[- ]?time|media jornada|tiempo parcial")),
    ("contractor", re.compile(r"contractor|freelance|por contrato")),
    ("pasantía", re.compile(r"pasantia|internship")),
]
SALARY_RE = re.compile(
    r"(?P<cur>usd|u\$s|us\$|ars|eur|€)\s*(?P<num>\d[\d.,]*)|(?P<num2>\d[\d.,]*)\s*(?P<cur2>usd|dolares|dólares|ars|pesos|eur|euros)"
)
TRAVEL_RE = re.compile(r"viajes? frecuentes|must travel|travel required|disponibilidad para viajar|viajar frecuentemente")


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar de forma tolerante."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _contains(term: str, text: str) -> bool:
    """Busca `term` como palabra/frase completa dentro de `text` (ambos ya normalizados)."""
    pattern = r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#])"
    return re.search(pattern, text) is not None


def _tokens(text: str) -> set[str]:
    stop = {"de", "del", "la", "el", "y", "en", "para", "con", "a", "the", "of", "and", "for", "in", "to", "-", "|"}
    return {t for t in re.findall(r"[a-z0-9+#/]+", normalize(text)) if t not in stop and len(t) > 1}


def detect_seniority(job: Job) -> str | None:
    """Detecta el seniority a partir del título (y del criterio de LinkedIn si existe)."""
    title = normalize(job.title)
    for level in ("manager", "lead", "semi senior", "senior", "junior"):
        if any(_contains(normalize(k), title) for k in SENIORITY_KEYWORDS[level]):
            return level
    crit = normalize(job.seniority or "")
    if crit:
        if "intermedio" in crit or "mid" in crit or "asociado" in crit or "associate" in crit:
            return "semi senior"
        if "director" in crit or "ejecutivo" in crit or "executive" in crit:
            return "manager"
        if "sin experiencia" in crit or "entry" in crit or "prácticas" in crit or "internship" in crit:
            return "junior"
        for level in ("senior", "junior"):
            if level in crit:
                return level
    return None


def detect_modality(job: Job) -> str | None:
    if job.remote is True:
        return "remoto"
    text = normalize(f"{job.title} {job.location} {job.description[:2000]}")
    for modality, keywords in MODALITY_KEYWORDS.items():
        if any(_contains(normalize(k), text) for k in keywords):
            return modality
    if job.remote is False:
        return "presencial"
    return None


def canon(skill: str) -> str:
    n = normalize(skill).strip()
    return SKILL_ALIASES.get(n, n)


def profile_skills(profile: Profile) -> set[str]:
    """Habilidades del perfil en forma canónica, incluyendo los idiomas que declara hablar."""
    mine = {canon(s) for s in profile.habilidades}
    for idioma in profile.idiomas:
        text = normalize(idioma)
        mine.update(canon(k) for k in LANGUAGE_KEYWORDS if _contains(k, text))
    return mine


def extract_required_skills(job_text_norm: str, profile: Profile) -> set[str]:
    universe = {normalize(s) for s in SKILL_CATALOG} | {normalize(s) for s in profile.habilidades} | {
        normalize(s) for s in profile.aprendiendo
    }
    found = set()
    for skill in universe:
        if not skill:
            continue
        pattern = SKILL_PATTERNS.get(skill)
        hit = pattern.search(job_text_norm) if pattern else _contains(skill, job_text_norm)
        if hit:
            found.add(canon(skill))
    return found


def _title_score(job: Job, profile: Profile) -> tuple[float, str | None]:
    title_norm = normalize(job.title)
    title_tokens = _tokens(job.title)
    best, best_role = 0.0, None
    for role in profile.roles_objetivo:
        role_norm = normalize(role)
        if _contains(role_norm, title_norm):
            score = 1.0
        else:
            role_tokens = _tokens(role)
            score = len(role_tokens & title_tokens) / len(role_tokens) if role_tokens else 0.0
        if score > best:
            best, best_role = score, role
    return best, best_role


def _tfidf_scores(profile_text: str, jobs: list[Job]) -> list[float]:
    if not jobs:
        return []
    corpus = [normalize(profile_text)] + [normalize(j.full_text) for j in jobs]
    try:
        matrix = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True).fit_transform(corpus)
    except ValueError:  # corpus vacío / solo stopwords
        return [0.0] * len(jobs)
    sims = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
    # La similitud coseno entre un CV y una descripción rara vez supera ~0.5; se reescala.
    return [min(1.0, float(s) / 0.5) for s in sims]


def _parse_amount(raw: str) -> float | None:
    text = raw.strip()
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", "") if len(text.split(",")[-1]) == 3 else text.replace(",", ".")
    elif text.count(".") > 1 or ("." in text and len(text.split(".")[-1]) == 3):
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _currency(token: str) -> str | None:
    token = normalize(token)
    if token in {"usd", "u$s", "us$"}:
        return "USD"
    if token in {"ars", "pesos"}:
        return "ARS"
    if token in {"eur", "euros", "€"}:
        return "EUR"
    return None


def salary_below_minimum(job: Job, profile: Profile) -> float | None:
    """Si la oferta declara un tope en la misma moneda y está debajo del mínimo, lo devuelve."""
    if not profile.salario_minimo or not profile.moneda:
        return None
    amounts = []
    for match in SALARY_RE.finditer(normalize(job.full_text)):
        currency = _currency(match.group("cur") or match.group("cur2") or "")
        amount = _parse_amount(match.group("num") or match.group("num2") or "")
        if currency == profile.moneda and amount:
            amounts.append(amount)
    if not amounts:
        return None
    ceiling = max(amounts)
    return ceiling if ceiling < profile.salario_minimo else None


def detect_contract(job: Job) -> str | None:
    text = normalize(f"{job.employment_type or ''} {job.description[:1500]}")
    for name, pattern in CONTRACT_PATTERNS:
        if pattern.search(text):
            return name
    return None


def score_job(job: Job, profile: Profile, text_similarity: float = 0.0) -> MatchResult:
    reasons: list[str] = []
    gaps: list[str] = []
    job_text = normalize(job.full_text)

    # --- habilidades
    required = extract_required_skills(job_text, profile)
    mine = profile_skills(profile)
    learning = {canon(s) for s in profile.aprendiendo}
    matched = sorted(required & mine)
    # "looker" no es brecha si la oferta habla de "looker studio" y eso ya lo tenés.
    covered = {s for s in required if any(s != m and _contains(s, m) for m in matched)}
    required -= covered
    missing = sorted(required - mine - learning)
    missing_learning = sorted(required & learning - mine)
    if required:
        skills_score = (len(matched) + 0.5 * len(missing_learning)) / len(required)
    else:
        skills_score = 0.5
    if matched:
        reasons.append(f"Habilidades en común ({len(matched)}/{len(required)}): {', '.join(matched)}")
    if missing_learning:
        reasons.append(f"Piden algo que estás aprendiendo: {', '.join(missing_learning)}")
    if missing:
        gaps.append(f"Piden y no tenés: {', '.join(missing)}")

    # --- rol objetivo
    title_score, role = _title_score(job, profile)
    if title_score >= 0.99:
        reasons.append(f"El título coincide con tu rol objetivo '{role}'")
    elif title_score >= 0.5:
        reasons.append(f"El título se parece a tu rol objetivo '{role}'")
    else:
        gaps.append("El título no se parece a tus roles objetivo")

    # --- seniority
    job_seniority = detect_seniority(job)
    if not profile.seniority or job_seniority is None:
        seniority_score = 0.7
    elif job_seniority in profile.seniority:
        seniority_score = 1.0
        reasons.append(f"Seniority '{job_seniority}' acorde a lo que buscás")
    else:
        seniority_score = 0.3
        gaps.append(f"Seniority '{job_seniority}' fuera de lo que buscás ({', '.join(profile.seniority)})")

    # --- modalidad
    wanted_modalities = {MODALITY_ALIASES.get(m, m) for m in profile.modalidad}
    job_modality = detect_modality(job)
    if not wanted_modalities or job_modality is None:
        modality_score = 0.7
    elif job_modality in wanted_modalities:
        modality_score = 1.0
        reasons.append(f"Modalidad {job_modality}")
    else:
        modality_score = 0.3
        gaps.append(f"Modalidad {job_modality} (buscás {', '.join(sorted(wanted_modalities))})")

    # --- ubicación: una ciudad cuenta como su país, y una región (UE, LATAM) como sus países
    preferred = list(profile.ubicaciones)
    if profile.ubicacion_actual:
        preferred.append(profile.ubicacion_actual)
    if profile.relocation:
        preferred.extend(profile.relocation_destinos)
    relocation_penalty = 0
    matched_place = matching_preference(preferred, job.location)
    matched_relocation = matching_preference(profile.relocation_destinos, job.location) if profile.relocation else None
    if not preferred or not job.location:
        location_score = 0.6
    elif job_modality == "remoto" or matched_place:
        location_score = 1.0
        if matched_relocation:
            reasons.append(f"La ubicación '{job.location}' entra en tu relocation ({matched_relocation})")
        elif matched_place and job_modality != "remoto":
            reasons.append(f"La ubicación '{job.location}' entra en '{matched_place}'")
    else:
        location_score = 0.4
        gaps.append(f"Ubicación '{job.location}' fuera de tus preferencias")
        if not profile.relocation:
            relocation_penalty = 12

    total = 100 * (
        WEIGHTS["skills"] * skills_score
        + WEIGHTS["title"] * title_score
        + WEIGHTS["text"] * text_similarity
        + WEIGHTS["seniority"] * seniority_score
        + WEIGHTS["modality"] * modality_score
        + WEIGHTS["location"] * location_score
    )

    # --- restricciones que salieron del cuestionario
    if profile.remoto_excluyente and job_modality != "remoto":
        if job_modality is None:
            total -= 15
            gaps.append("No aclara si es remoto y para vos el remoto es excluyente")
        else:
            total -= 40
            gaps.append(f"Pedís remoto excluyente y la oferta es {job_modality}")
    total -= relocation_penalty

    wanted_contracts = {CONTRACT_ALIASES.get(normalize(c), normalize(c)) for c in profile.tipo_contrato}
    job_contract = detect_contract(job)
    if wanted_contracts and job_contract and job_contract not in wanted_contracts:
        total -= 20
        gaps.append(f"Contrato '{job_contract}' (aceptás {', '.join(sorted(wanted_contracts))})")

    low_salary = salary_below_minimum(job, profile)
    if low_salary is not None:
        total -= 25
        gaps.append(f"Pagan {low_salary:.0f} {profile.moneda}, debajo de tu mínimo ({profile.salario_minimo:.0f})")

    if profile.viaje == "no" and TRAVEL_RE.search(job_text):
        total -= 12
        gaps.append("Pide viajar y vos no querés viajar")

    company_norm = normalize(job.company)
    for company in profile.empresas_evitar:
        if company and _contains(normalize(company), company_norm):
            total -= 40
            gaps.append(f"La empresa '{job.company}' está en tu lista de empresas a evitar")

    # --- palabras a evitar
    title_norm = normalize(job.title)
    for word in profile.evitar:
        w = normalize(word)
        if _contains(w, title_norm):
            total -= 25
            gaps.append(f"El título menciona '{word}' (en tu lista de evitar)")
        elif _contains(w, job_text):
            total -= 8
            gaps.append(f"La descripción menciona '{word}' (en tu lista de evitar)")

    if text_similarity >= 0.6:
        reasons.append("La descripción se parece mucho a tu experiencia")

    prioritized = priority_company_name(job.company)
    if prioritized:
        total += PRIORITY_BONUS
        reasons.append(f"Empresa prioritaria: {prioritized}")

    return MatchResult(job_id=job.id, score=round(max(0.0, min(100.0, total)), 1), reasons=reasons, gaps=gaps)


def score_jobs(jobs: Iterable[Job], profile: Profile) -> list[MatchResult]:
    """Puntúa un lote de ofertas (el TF-IDF se ajusta con todo el lote para tener IDF útil)."""
    jobs = list(jobs)
    sims = _tfidf_scores(profile.texto, jobs)
    return [score_job(job, profile, sim) for job, sim in zip(jobs, sims)]
