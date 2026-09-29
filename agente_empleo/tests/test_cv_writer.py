from pypdf import PdfReader

from job_agent.cv_writer import write_cv
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


def _translate(texts: list[str]) -> list[str]:
    return [f"EN {text}" for text in texts]


def _pdf_text(path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_cv_is_an_english_pdf_and_does_not_invent(profile, tmp_path):
    path, failed = write_cv(profile, _job(), tmp_path, translator=_translate)
    assert path.suffix == ".pdf"
    assert not failed
    assert not list(tmp_path.glob("*.md"))
    assert not list(tmp_path.glob("*.docx"))
    text = _pdf_text(path)
    assert "PROFILE" in text
    assert "SKILLS" in text
    assert "EXPERIENCE" in text
    assert "For this role:" in text
    assert "Python" in text and "SQL" in text
    assert "Kubernetes" not in text
    assert "Empresa Ejemplo S.A." in text
    assert "Inventada" not in text
    assert "Experiencia" not in text
    assert "EN " in text
