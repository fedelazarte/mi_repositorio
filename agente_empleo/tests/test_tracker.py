from datetime import datetime, timedelta

from job_agent.db import Database
from job_agent.models import Job, MatchResult
from job_agent.tracker import auto_expire, funnel_stats, pending_follow_ups


def _job(i: str, title="Data Scientist", posted_days_ago=1) -> Job:
    return Job(id=i, title=title, company=f"Empresa {i}", location="Remote", url=f"https://x/{i}",
               posted_at=(datetime.now() - timedelta(days=posted_days_ago)).date().isoformat())


def _backdate(db: Database, job_id: str, days: int, field: str = "applied_at"):
    when = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    db.conn.execute(f"UPDATE applications SET {field} = ? WHERE job_id = ?", (when, job_id))
    db.conn.commit()


def test_upsert_job_creates_application_and_tracks_status(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.upsert_job(_job("a")) is True
    assert db.upsert_job(_job("a")) is False  # ya existía
    assert db.get_match_row("a")["status"] == "descubierto"

    prev, new = db.set_status("a", "postulado", note="Apliqué con CV v3")
    assert (prev, new) == ("descubierto", "postulado")
    row = db.get_match_row("a")
    assert row["applied_at"] is not None
    assert "CV v3" in row["notes"]

    db.set_status("a", "entrevista", next_action_at="2030-01-01")
    events = db.events_for("a")
    assert [e["status_to"] for e in events] == ["postulado", "entrevista"]
    # applied_at no se pisa al cambiar de estado
    assert db.get_match_row("a")["applied_at"] == row["applied_at"]


def test_follow_ups_and_auto_expire(tmp_path, profile):
    db = Database(tmp_path / "t.db")
    for i in ("fresh", "stale", "cold", "hot_match", "interview"):
        db.upsert_job(_job(i, posted_days_ago=10))
        db.upsert_match(MatchResult(job_id=i, score=80))

    db.set_status("fresh", "postulado")
    db.set_status("stale", "postulado"); _backdate(db, "stale", profile.dias_aviso_followup + 1)
    db.set_status("cold", "postulado"); _backdate(db, "cold", profile.dias_sin_respuesta + 1)
    db.set_status("interview", "entrevista"); _backdate(db, "interview", 6, field="updated_at")

    actions = {f.job_id: f for f in pending_follow_ups(db, profile)}
    assert "fresh" not in actions
    assert "seguimiento" in actions["stale"].action and actions["stale"].priority == 1
    assert "sin_respuesta" in actions["cold"].action
    assert actions["interview"].priority == 1
    assert "postulate" in actions["hot_match"].action  # buen match, vieja y sin acción

    assert auto_expire(db, profile) == ["cold"]
    assert db.get_match_row("cold")["status"] == "sin_respuesta"
    assert db.get_match_row("stale")["status"] == "postulado"


def test_funnel_stats(tmp_path):
    db = Database(tmp_path / "t.db")
    for i, status, score in (("1", "postulado", 70), ("2", "rechazado", 50), ("3", "entrevista", 90), ("4", "descubierto", 40)):
        db.upsert_job(_job(i))
        db.upsert_match(MatchResult(job_id=i, score=score))
        if status != "descubierto":
            db.set_status(i, status)
    s = funnel_stats(db)
    assert s["total_ofertas"] == 4
    assert s["postulaciones"] == 3
    assert s["respuestas"] == 2
    assert s["tasa_respuesta"] == 66.7
    assert s["entrevistas"] == 1
    assert s["score_promedio_rechazadas"] == 50
    assert s["score_promedio_con_respuesta"] == 90
