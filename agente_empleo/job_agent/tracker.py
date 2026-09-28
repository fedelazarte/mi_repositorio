"""Seguimiento de postulaciones: qué hacer hoy, qué se enfrió y cómo viene el embudo."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean

from .db import Database
from .models import APPLIED_STATUSES, RESPONDED_STATUSES, STATUSES
from .profile import Profile


@dataclass
class FollowUp:
    job_id: str
    title: str
    company: str
    status: str
    days: int
    action: str
    priority: int  # 1 = urgente, 3 = cuando puedas


def _days_since(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        then = datetime.combine(date.fromisoformat(iso[:10]), datetime.min.time())
    return (datetime.now() - then).days


def pending_follow_ups(db: Database, profile: Profile) -> list[FollowUp]:
    """Aplica las reglas de seguimiento sobre el pipeline y devuelve acciones sugeridas."""
    today = date.today()
    out: list[FollowUp] = []
    for row in db.list_applications():
        status = row["status"]
        days_updated = _days_since(row["updated_at"])
        days_applied = _days_since(row["applied_at"])
        base = dict(job_id=row["id"], title=row["title"], company=row["company"], status=status)

        if row["next_action_at"]:
            try:
                due = date.fromisoformat(row["next_action_at"][:10])
            except ValueError:
                due = None
            if due and due <= today:
                out.append(FollowUp(**base, days=(today - due).days,
                                    action=f"Acción agendada vencida ({due.isoformat()}): revisá tus notas", priority=1))
                continue

        if status == "postulado":
            if days_applied >= profile.dias_sin_respuesta:
                out.append(FollowUp(**base, days=days_applied, priority=2,
                                    action="Sin novedades hace mucho: marcá `sin_respuesta` o hacé un último intento"))
            elif days_applied >= profile.dias_aviso_followup:
                out.append(FollowUp(**base, days=days_applied, priority=1,
                                    action="Mandá un mensaje de seguimiento al reclutador (breve, reafirmando interés)"))
        elif status == "en_revision" and days_updated >= profile.dias_aviso_followup:
            out.append(FollowUp(**base, days=days_updated, priority=2,
                                action="Preguntá amablemente por los próximos pasos del proceso"))
        elif status == "entrevista" and days_updated >= 5:
            out.append(FollowUp(**base, days=days_updated, priority=1,
                                action="Pasaron días desde la entrevista: agradecé y pedí feedback / próximos pasos"))
        elif status == "oferta" and days_updated >= 3:
            out.append(FollowUp(**base, days=days_updated, priority=1,
                                action="Tenés una oferta esperando respuesta"))
        elif status in ("descubierto", "interesado"):
            score = row["score"] or 0
            days_posted = _days_since(row["posted_at"]) if row["posted_at"] else 0
            if score >= profile.puntaje_minimo and (status == "interesado" or days_posted >= 7):
                out.append(FollowUp(**base, days=days_posted, priority=3,
                                    action=f"Match {score:.0f}/100 y la oferta ya tiene {days_posted} días: postulate o descartala"))
    out.sort(key=lambda f: (f.priority, -f.days))
    return out


def auto_expire(db: Database, profile: Profile) -> list[str]:
    """Marca como `sin_respuesta` las postulaciones enfriadas. Devuelve los ids afectados."""
    expired = []
    for row in db.list_applications(statuses=["postulado"]):
        if _days_since(row["applied_at"]) >= profile.dias_sin_respuesta:
            db.set_status(row["id"], "sin_respuesta", note="Marcada automáticamente por falta de respuesta")
            expired.append(row["id"])
    return expired


def funnel_stats(db: Database) -> dict:
    counts = db.status_counts()
    applied = sum(counts.get(s, 0) for s in APPLIED_STATUSES)
    responded = sum(counts.get(s, 0) for s in RESPONDED_STATUSES)
    interviews = counts.get("entrevista", 0) + counts.get("oferta", 0)
    scores = db.scores_by_status()

    def avg(statuses):
        vals = [v for s in statuses for v in scores.get(s, [])]
        return round(mean(vals), 1) if vals else None

    return {
        "por_estado": {s: counts.get(s, 0) for s in STATUSES if counts.get(s, 0)},
        "total_ofertas": sum(counts.values()),
        "postulaciones": applied,
        "respuestas": responded,
        "tasa_respuesta": round(100 * responded / applied, 1) if applied else None,
        "entrevistas": interviews,
        "tasa_entrevista": round(100 * interviews / applied, 1) if applied else None,
        "ofertas_recibidas": counts.get("oferta", 0),
        "rechazos": counts.get("rechazado", 0),
        "score_promedio_con_respuesta": avg(RESPONDED_STATUSES - {"rechazado"}),
        "score_promedio_rechazadas": avg({"rechazado"}),
        "score_promedio_sin_respuesta": avg({"sin_respuesta"}),
    }
