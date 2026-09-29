import json

from pypdf import PdfReader

from job_agent.cv_writer import build_messages, sanitize, source_material, write_cv
from job_agent.models import Job


def _job() -> Job:
    return Job(
        id="4471583731",
        title="Senior Data Scientist",
        company="Acme",
        location="London, United Kingdom",
        url="https://www.linkedin.com/jobs/view/4471583731/",
        description="We need a Data Scientist with Python and SQL. Kubernetes is a plus.",
    )


def _draft() -> dict:
    return {
        "headline": "Data Analyst",
        "summary": "Data analyst used to turning operational data into decisions. Expected salary: 9000 USD. Also applying to other companies.",
        "skills": ["Python", "SQL", "Kubernetes", "salary negotiation"],
        "experience": [
            {
                "title": "Data Analyst",
                "company": "Empresa Ejemplo S.A.",
                "period": "2022 - Present",
                "bullets": [
                    "Built Power BI dashboards and SQL models on BigQuery.",
                    "Asked for a higher salary during the process.",
                ],
            },
            {
                "title": "Personal project",
                "company": "Proyecto propio",
                "period": "2021",
                "bullets": ["A hobby app."],
            },
            {
                "title": "Invented role",
                "company": "Inventada SA",
                "period": "2019",
                "bullets": ["Did something that never happened."],
            },
        ],
        "education": ["B.Sc. in Statistics, UBA"],
        "languages": ["Spanish (native)", "English (B2)"],
    }


def test_the_model_does_not_receive_salary_or_other_applications(profile):
    profile.salario_minimo = 9000
    material = source_material(profile, _job())
    assert "salario" not in material
    assert "9000" not in json.dumps(material)
    prompt = build_messages(profile, _job())[0]["content"].lower()
    assert "salary" in prompt and "personal projects" in prompt and "other applications" in prompt


def test_sanitize_keeps_real_jobs_and_drops_invented_content(profile):
    cleaned = sanitize(profile, _job(), _draft())
    companies = {role["company"] for role in cleaned["experience"]}
    assert "Empresa Ejemplo S.A." in companies
    assert "Startup Deportiva" in companies
    assert "Inventada SA" not in companies
    assert "Proyecto propio" not in companies
    bullets = " ".join(bullet for role in cleaned["experience"] for bullet in role["bullets"]).lower()
    assert "bigquery" in bullets or "power bi" in bullets
    assert "salary" not in bullets
    assert "kubernetes" not in [skill.lower() for skill in cleaned["skills"]]
    assert cleaned["skills"][0].lower() in {"python", "sql"}
    assert "salary" not in cleaned["summary"].lower()
    assert "other companies" not in cleaned["summary"].lower()
    assert cleaned["education"]


def test_a_spanish_shaped_reply_still_keeps_experience_courses_and_skills(profile):
    cleaned = sanitize(profile, _job(), {"resumen": "Analista.", "experiencia": [], "habilidades": []})
    assert len(cleaned["experience"]) >= 2
    assert cleaned["skills"]
    assert cleaned["education"]
    assert cleaned["summary"]


def test_pdf_contains_the_cleaned_cv_only(profile, tmp_path):
    path = write_cv(profile, _job(), tmp_path, generator=lambda *_: _draft(), translate=False)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    assert "PROFILE" in text or "Profile" in text
    assert profile.nombre in text
    assert "Education" in text
    assert "Empresa Ejemplo" in text
    assert "Startup Deportiva" in text
    assert "Inventada" not in text
    assert "Kubernetes" not in text
    assert "9000" not in text
    assert "salary" not in text.lower()
