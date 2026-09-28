from job_agent.matcher import detect_modality, detect_seniority, score_job, score_jobs
from job_agent.models import Job


def make_job(**kw) -> Job:
    base = dict(id="1", title="Data Scientist", company="Acme", location="Buenos Aires, Argentina", url="u")
    base.update(kw)
    return Job(**base)


GOOD = make_job(
    id="good",
    title="Senior Data Scientist",
    description=(
        "Buscamos Data Scientist con Python, pandas, scikit-learn y SQL sobre BigQuery. "
        "Experiencia en A/B testing y dashboards en Power BI. Trabajo remoto. Inglés intermedio."
    ),
)
BAD = make_job(
    id="bad",
    title="Ejecutivo de Ventas Junior",
    location="Madrid, España",
    description="Venta telefónica en call center, trabajo presencial, comisión por objetivos. Se requiere Salesforce.",
)
GAP = make_job(
    id="gap",
    title="Data Engineer",
    description="Spark, Airflow, Kafka, Scala y Kubernetes en AWS. Modalidad híbrida.",
)


def test_good_job_scores_higher_than_bad(profile):
    scores = {r.job_id: r.score for r in score_jobs([GOOD, BAD, GAP], profile)}
    assert scores["good"] > 70
    assert scores["bad"] < 35
    assert scores["good"] > scores["gap"] > scores["bad"]


def test_reasons_and_gaps_are_explained(profile):
    result = score_job(GOOD, profile)
    joined = " ".join(result.reasons).lower()
    assert "python" in joined and "sql" in joined
    assert any("rol objetivo" in r for r in result.reasons)
    assert any("senior" in r for r in result.reasons)
    assert any("remoto" in r.lower() for r in result.reasons)


def test_gaps_detect_missing_skills_and_learning_ones(profile):
    result = score_job(GAP, profile)
    gap_text = " ".join(result.gaps)
    assert "kafka" in gap_text and "scala" in gap_text and "kubernetes" in gap_text
    # dbt/airflow/spark están en `aprendiendo`: no son brecha grave, se mencionan como razón parcial
    assert "airflow" not in gap_text and "spark" not in gap_text
    assert any("aprendiendo" in r for r in result.reasons)


def test_languages_and_aliases_count_as_skills(profile):
    job = make_job(id="lang", description="Requiere English fluido, sklearn y Postgres. GitHub obligatorio.")
    result = score_job(job, profile)
    gaps = " ".join(result.gaps)
    assert "ingles" not in gaps and "english" not in gaps  # está en idiomas del perfil
    assert "scikit-learn" not in gaps and "sklearn" not in gaps
    assert "git" not in gaps
    assert "postgresql" in gaps


def test_substring_of_owned_skill_is_not_a_gap(profile):
    job = make_job(id="looker", description="Dashboards en Looker Studio y SQL.")
    result = score_job(job, profile)
    assert not any("looker" in g for g in result.gaps)
    assert any("looker studio" in r for r in result.reasons)


def test_ambiguous_skill_names_need_specific_context(profile):
    noisy = make_job(id="noisy", description="We go the extra mile; the rest of the team uses R&D budgets.")
    assert not any(g.startswith("Piden y no tenés") for g in score_job(noisy, profile).gaps)
    real = make_job(id="real", description="Backend en Golang y RStudio para análisis; exponer REST APIs.")
    gaps = " ".join(score_job(real, profile).gaps)
    assert "go" in gaps.split(", ") or " go" in gaps
    assert " r" in gaps or ", r" in gaps


def test_avoid_words_penalize(profile):
    result = score_job(BAD, profile)
    assert any("evitar" in g for g in result.gaps)
    assert any("Seniority 'junior'" in g for g in result.gaps)
    assert any("presencial" in g for g in result.gaps)


def test_seniority_and_modality_detection():
    assert detect_seniority(make_job(title="Sr. Analytics Engineer")) == "senior"
    assert detect_seniority(make_job(title="Analista de Datos Ssr")) == "semi senior"
    assert detect_seniority(make_job(title="Head of Data")) == "manager"
    assert detect_seniority(make_job(title="Data Analyst", seniority="Intermedio")) == "semi senior"
    assert detect_seniority(make_job(title="Data Analyst")) is None
    assert detect_modality(make_job(description="Posición 100% remota")) == "remoto"
    assert detect_modality(make_job(description="Esquema hybrid 3x2")) == "híbrido"
    assert detect_modality(make_job(remote=False)) == "presencial"


def test_score_is_bounded(profile):
    for job in (GOOD, BAD, GAP, make_job(id="empty", title="", description="")):
        assert 0 <= score_job(job, profile, text_similarity=1.0).score <= 100
