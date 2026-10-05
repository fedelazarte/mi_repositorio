from job_agent.matcher import score_job
from job_agent.models import Job
from job_agent.priority_companies import _COMPANIES, priority_company


def _job(company: str) -> Job:
    return Job(id="1", title="Data Analyst", company=company, location="Remote", url="u", description="Python y SQL.")


def test_list_has_the_agreed_set():
    assert len(_COMPANIES) == 351
    assert "Red Bull" in _COMPANIES
    assert "Uber" in _COMPANIES
    assert "Bolt" in _COMPANIES
    assert "Glovo" in _COMPANIES
    assert "Oracle Red Bull Racing" in _COMPANIES
    assert "Arsenal" in _COMPANIES
    assert "Los Angeles Lakers" in _COMPANIES


def test_priority_matches_the_company_and_not_a_lookalike():
    assert priority_company("Fanatics") == "Fanatics"
    assert priority_company("Red Bull GmbH") == "Red Bull"
    assert priority_company("Oracle Red Bull Racing") == "Oracle Red Bull Racing"
    assert priority_company("Red Bull New York") == "New York Red Bulls"
    assert priority_company("Uber Eats") == "Uber"
    assert priority_company("Bolt Operations") == "Bolt"
    assert priority_company("Army Research Lab") is None
    assert priority_company("Metabase") is None
    assert priority_company("Williams Sonoma") is None
    assert priority_company("Williams Racing") == "Williams"
    assert priority_company("Taller de Datos") is None


def test_destacadas_keeps_the_five_best_priority_companies():
    from job_agent.cli import select_destacadas

    rows = [
        {"company": "Taller chico", "score": 99},
        {"company": "Fanatics", "score": 96},
        {"company": "Otro", "score": 95},
        {"company": "Google", "score": 91},
        {"company": "Nike", "score": 90},
        {"company": "Uber", "score": 88},
        {"company": "Red Bull", "score": 87},
        {"company": "Bolt", "score": 80},
    ]
    picked = [row["company"] for row in select_destacadas(rows)]
    assert picked == ["Fanatics", "Google", "Nike", "Uber", "Red Bull"]


def test_priority_adds_points_without_hiding_other_companies(profile):
    fanatics = score_job(_job("Fanatics"), profile)
    small = score_job(_job("Taller de Datos"), profile)
    assert any(reason == "Empresa prioritaria: Fanatics" for reason in fanatics.reasons)
    assert fanatics.score == min(100.0, round(small.score + 8, 1))
    assert small.score > 0
    assert not any("prioritaria" in reason for reason in small.reasons)
