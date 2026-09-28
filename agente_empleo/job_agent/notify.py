"""Aviso por mail cuando una oferta matchea de verdad (85% o más, configurable).

Se manda un mail por oferta, una sola vez. Requiere SMTP en el entorno:

    JOB_AGENT_SMTP_HOST, JOB_AGENT_SMTP_PORT (587), JOB_AGENT_SMTP_USER,
    JOB_AGENT_SMTP_PASSWORD, JOB_AGENT_SMTP_FROM (opcional)

El destinatario es `contacto.email` del perfil, o `JOB_AGENT_EMAIL_TO` si está definido.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger(__name__)

from . import config
from .db import Database
from .profile import Profile


@dataclass
class NotifyResult:
    sent: list[str]
    pending: list
    to: str
    reason: str | None  # None si se envió (o no había nada); si no, por qué no se pudo


def render_match_email(profile: Profile, row) -> tuple[str, str]:
    score = float(row["score"])
    subject = f"Match {score:.0f}% — {row['title']} en {row['company']}"
    lines = [
        f"Hola {profile.nombre.split()[0] if profile.nombre else ''},".rstrip(),
        "",
        f"Encontré una oferta con match {score:.0f}/100, por encima de tu umbral de {profile.umbral_email:.0f}.",
        "",
        f"{row['title']} — {row['company']}",
        f"Ubicación: {row['location'] or 'sin especificar'}",
        row["url"] or "",
        "",
        "Por qué encaja:",
    ]
    for reason in json.loads(row["reasons"] or "[]"):
        lines.append(f"  + {reason}")
    gaps = json.loads(row["gaps"] or "[]")
    if gaps:
        lines.append("")
        lines.append("A tener en cuenta:")
        for gap in gaps:
            lines.append(f"  - {gap}")
    if row["advice"]:
        lines.extend(["", f"Consejo: {row['advice']}"])
    lines.extend(["", "Cuando te postules: `python -m job_agent postular " + row["id"] + "`", ""])
    return subject, "\n".join(line for line in lines if line is not None)


def smtp_send(to: str, subject: str, body: str) -> None:
    cfg = config.smtp_settings()
    if cfg is None:
        raise RuntimeError("SMTP no configurado")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["from"]
    message["To"] = to
    message.set_content(body)
    client = smtplib.SMTP_SSL if cfg["ssl"] else smtplib.SMTP
    with client(cfg["host"], cfg["port"], timeout=30) as smtp:
        smtp.ehlo()
        if cfg["tls"] and not cfg["ssl"]:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(message)


def notify_high_matches(db: Database, profile: Profile, sender=None) -> NotifyResult:
    """Manda mail por cada oferta nueva que supera el umbral. No repite avisos ya enviados."""
    pending = db.high_matches_not_notified(profile.umbral_email) if profile.email_activo else []
    to = os.environ.get("JOB_AGENT_EMAIL_TO") or profile.email
    if not profile.email_activo:
        return NotifyResult([], [], to, "desactivado")
    if not pending:
        return NotifyResult([], [], to, None)
    if not to:
        return NotifyResult([], pending, "", "sin_email")
    if sender is None:
        if config.smtp_settings() is None:
            return NotifyResult([], pending, to, "sin_smtp")
        sender = smtp_send
    sent: list[str] = []
    still_pending = []
    for row in pending:
        subject, body = render_match_email(profile, row)
        try:
            sender(to, subject, body)
        except Exception as exc:  # un fallo de SMTP no tiene que abortar la búsqueda
            log.warning("No pude avisar por %s (%s): %s", row["id"], to, exc)
            still_pending.append(row)
            continue
        db.mark_notified(row["id"], float(row["score"]), to)
        sent.append(row["id"])
    reason = "error_smtp" if still_pending else None
    return NotifyResult(sent, still_pending, to, reason)
