from job_agent.db import Database
from job_agent.models import Job, MatchResult
from job_agent.notify import notify_high_matches
from job_agent.profile import Profile


def _profile(profile) -> Profile:
    data = dict(profile.raw)
    data["contacto"] = {"email": "ana@example.com"}
    data["notificaciones"] = {"email": True, "umbral_match": 85}
    return Profile(data)


def _seed(db: Database):
    for job_id, score in (("high", 92), ("low", 70)):
        db.upsert_job(Job(id=job_id, title="Data Scientist", company="Acme", location="Remoto", url=f"https://ej/{job_id}"))
        db.upsert_match(MatchResult(job_id=job_id, score=score, reasons=["El título coincide"], gaps=["Falta dbt"]))


def test_mail_goes_out_once_and_only_above_threshold(tmp_path, profile, monkeypatch):
    monkeypatch.delenv("JOB_AGENT_SMTP_HOST", raising=False)
    db = Database(tmp_path / "t.db")
    _seed(db)
    person = _profile(profile)
    sent = []

    def sender(to, subject, body):
        sent.append((to, subject, body))

    first = notify_high_matches(db, person, sender=sender)
    assert first.sent == ["high"]
    assert sent[0][0] == "ana@example.com"
    assert "92%" in sent[0][1]
    assert "Falta dbt" in sent[0][2]
    assert "https://ej/high" in sent[0][2]

    second = notify_high_matches(db, person, sender=sender)
    assert second.sent == []
    assert len(sent) == 1


def test_invalid_recipient_is_not_sent(tmp_path, profile, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_EMAIL_TO", "ese mismo mail")
    db = Database(tmp_path / "t.db")
    _seed(db)
    sent = []
    result = notify_high_matches(db, _profile(profile), sender=lambda *args: sent.append(args))
    assert result.reason == "destinatario"
    assert result.sent == []
    assert sent == []


def test_bad_credentials_stop_after_first_attempt(tmp_path, profile):
    import smtplib

    db = Database(tmp_path / "t.db")
    _seed(db)
    db.upsert_job(Job(id="high2", title="Data Scientist", company="Beta", location="Remoto", url="https://ej/high2"))
    db.upsert_match(MatchResult(job_id="high2", score=95))
    attempts = []

    def sender(to, subject, body):
        attempts.append(subject)
        raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    result = notify_high_matches(db, _profile(profile), sender=sender)
    assert result.reason == "credenciales"
    assert len(attempts) == 1
    assert len(result.pending) == 2
    assert result.sent == []


def test_without_smtp_it_reports_pending_and_does_not_crash(tmp_path, profile, monkeypatch):
    monkeypatch.delenv("JOB_AGENT_SMTP_HOST", raising=False)
    db = Database(tmp_path / "t.db")
    _seed(db)
    result = notify_high_matches(db, _profile(profile))
    assert result.reason == "sin_smtp"
    assert [row["id"] for row in result.pending] == ["high"]
    assert notify_high_matches(db, _profile(profile)).pending  # sigue pendiente: no se marcó como enviado
