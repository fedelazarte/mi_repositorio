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


def _with(profile, **aspiraciones):
    import copy

    from job_agent.profile import Profile

    data = copy.deepcopy(profile.raw)
    data["aspiraciones"].update(aspiraciones)
    return Profile(data)


def test_questionnaire_constraints_change_the_score(profile):
    exclusive = _with(profile, remoto_excluyente=True, modalidad=["remoto"], empresas_evitar=["Acme"])
    base = dict(title="Senior Data Scientist", location="Buenos Aires, Argentina",
                description="Python, pandas, scikit-learn, SQL, BigQuery y Power BI.")
    remote = make_job(id="remote", company="Otra", **{**base, "description": "100% remoto. " + base["description"]})
    onsite = make_job(id="onsite", company="Otra", **{**base, "description": "Trabajo presencial. " + base["description"]})
    blocked = make_job(id="blocked", company="Acme Corp", **{**base, "description": "100% remoto. " + base["description"]})
    remote_score = score_job(remote, exclusive)
    assert remote_score.score > score_job(onsite, exclusive).score + 25
    assert any("excluyente" in gap for gap in score_job(onsite, exclusive).gaps)
    assert any("empresas a evitar" in gap for gap in score_job(blocked, exclusive).gaps)

    paid_little = _with(profile, salario_minimo=5000, moneda="USD")
    cheap = make_job(id="cheap", description="Remoto. Compensación hasta USD 2,000. Python y SQL.")
    assert any("debajo de tu mínimo" in gap for gap in score_job(cheap, paid_little).gaps)

    movable = _with(profile, relocation=True, relocation_destinos=["Madrid"])
    madrid = make_job(id="mad", location="Madrid, España", description="Esquema híbrido. Python y SQL.")
    assert any("relocation" in reason for reason in score_job(madrid, movable).reasons)

    stays = _with(profile, viaje="no")
    traveler = make_job(id="trip", description="Disponibilidad para viajar. Python y SQL.")
    assert any("viajar" in gap for gap in score_job(traveler, stays).gaps)


def test_regions_cover_cities_in_either_language(profile):
    europe = _with(profile, ubicaciones=["European Union", "United Kingdom"], relocation=False)
    london = make_job(id="lon", location="Londres, Reino Unido", description="Híbrido. Python y SQL.")
    result = score_job(london, europe)
    assert any("United Kingdom" in reason for reason in result.reasons)
    assert not any("fuera de tus preferencias" in gap for gap in result.gaps)

    only_eu = _with(profile, ubicaciones=["European Union"], relocation=False)
    # El Reino Unido no es parte de la Unión Europea.
    assert any("fuera de tus preferencias" in gap for gap in score_job(london, only_eu).gaps)
    berlin = make_job(id="ber", location="Berlín, Alemania", description="Híbrido. Python y SQL.")
    assert any("European Union" in reason for reason in score_job(berlin, only_eu).reasons)
    belfast = make_job(id="bel", location="Belfast, Northern Ireland", description="Híbrido. Python y SQL.")
    assert any("fuera de tus preferencias" in gap for gap in score_job(belfast, only_eu).gaps)


def test_fluent_language_the_profile_lacks_is_an_automatic_reject(profile):
    data = profile.raw
    data["idiomas"] = ["español nativo", "inglés B2"]
    from job_agent.profile import Profile

    person = Profile(data)
    bulgarian = make_job(
        id="bg",
        company="Glovo",
        description="We are hiring in Sofia. Fluent Bulgarian is required. Python and SQL. Remote.",
    )
    result = score_job(bulgarian, person)
    assert result.score == 0
    assert any("búlgaro" in gap for gap in result.gaps)

    english = make_job(id="en", company="Glovo", description="Fluent English is required. Python and SQL.")
    assert score_job(english, person).score > 0
    assert not any("Rechazo automático" in gap for gap in score_job(english, person).gaps)

    optional = make_job(id="opt", company="Glovo", description="Bulgarian is a plus. Fluent English required. Python and SQL.")
    assert not any("búlgaro" in gap for gap in score_job(optional, person).gaps)


def test_score_is_bounded(profile):
    for job in (GOOD, BAD, GAP, make_job(id="empty", title="", description="")):
        assert 0 <= score_job(job, profile, text_similarity=1.0).score <= 100
