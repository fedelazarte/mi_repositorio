from docx import Document

from job_agent.cv_writer import render_cv, write_cv
from job_agent.models import Job


def _job() -> Job:
    return Job(
        id="4471583731",
        title="Senior Data Scientist",
        company="Acme",
        location="Londres, Reino Unido",
        url="https://www.linkedin.com/jobs/view/4471583731/",
        description="Buscamos Data Scientist con Python, SQL y Kubernetes. Trabajo remoto.",
    )


def test_cv_highlights_job_skills_and_does_not_invent(profile):
    text = render_cv(profile, _job())
    assert "Senior Data Scientist — Acme" in text
    assert "Para este puesto: " in text
    puesto = text.split("Para este puesto: ", 1)[1].split("\n", 1)[0].lower()
    assert "python" in puesto and "sql" in puesto
    assert "kubernetes" not in puesto
    assert "Empresa Ejemplo S.A." in text
    assert "Inventada" not in text


def test_write_cv_saves_markdown_and_docx(profile, tmp_path):
    md_path, docx_path = write_cv(profile, _job(), tmp_path)
    assert md_path.parent == tmp_path
    assert md_path.suffix == ".md" and docx_path.suffix == ".docx"
    assert "4471583731" in md_path.name
    paragraphs = [p.text for p in Document(docx_path).paragraphs]
    assert any(profile.nombre in p for p in paragraphs)
    assert any("Acme" in p for p in paragraphs)
