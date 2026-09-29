"""Aviso por mail cuando una oferta matchea de verdad (85% o más, configurable).

Las ofertas nuevas que superan el umbral salen juntas en un solo mail, una sola vez. Requiere SMTP en el entorno:

    JOB_AGENT_SMTP_HOST, JOB_AGENT_SMTP_PORT (587), JOB_AGENT_SMTP_USER,
    JOB_AGENT_SMTP_PASSWORD, JOB_AGENT_SMTP_FROM (opcional)

El destinatario es `contacto.email` del perfil, o `JOB_AGENT_EMAIL_TO` si está definido.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def recipient(profile: Profile) -> str:
    """Destinatario: JOB_AGENT_EMAIL_TO si existe, si no el mail del perfil. Tiene que ser una sola dirección."""
    to = (os.environ.get("JOB_AGENT_EMAIL_TO") or profile.email or "").strip()
    if not to:
        raise RuntimeError("El perfil no tiene contacto.email y no está definido JOB_AGENT_EMAIL_TO.")
    if not _EMAIL_RE.match(to):
        source = "JOB_AGENT_EMAIL_TO" if os.environ.get("JOB_AGENT_EMAIL_TO") else "contacto.email en perfil.yaml"
        raise RuntimeError(
            f"El destinatario no es un mail válido ({to!r}, viene de {source}). "
            "Tiene que ser una sola dirección, sin espacios ni texto alrededor."
        )
    return to

from . import config
from .db import Database
from .profile import Profile


@dataclass
class NotifyResult:
    sent: list[str]
    pending: list
    to: str
    reason: str | None  # None si se envió (o no había nada); si no, por qué no se pudo


def job_link(row) -> str:
    """URL de la oferta. Si no quedó guardada, arma la de LinkedIn a partir del id numérico."""
    url = (row["url"] or "").strip()
    if url:
        return url
    job_id = str(row["id"])
    if job_id.isdigit():
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    return ""


def _offer_lines(row) -> list[str]:
    score = float(row["score"])
    link = job_link(row)
    lines = [
        f"{score:.0f}%  {row['title']} — {row['company']}",
        f"Link: {link}" if link else "Link: esta oferta no tiene URL guardada",
        f"Ubicación: {row['location'] or 'sin especificar'}",
        "Por qué encaja:",
    ]
    for reason in json.loads(row["reasons"] or "[]"):
        lines.append(f"  + {reason}")
    gaps = json.loads(row["gaps"] or "[]")
    if gaps:
        lines.extend(["", "A tener en cuenta:"])
        lines.extend(f"  - {gap}" for gap in gaps)
    if row["advice"]:
        lines.extend(["", f"Consejo: {row['advice']}"])
    lines.append(f"Postular: python -m job_agent postular {row['id']}")
    return lines


def render_match_email(profile: Profile, rows) -> tuple[str, str]:
    """Un solo mail con todas las ofertas de la corrida. `rows` puede ser una fila o una lista."""
    if isinstance(rows, dict) or not isinstance(rows, (list, tuple)):
        rows = [rows]
    else:
        rows = list(rows)
    threshold = profile.umbral_email
    if len(rows) == 1:
        row = rows[0]
        subject = f"Match {float(row['score']):.0f}% — {row['title']} en {row['company']}"
        intro = f"Encontré una oferta con match {float(row['score']):.0f}/100, por encima de tu umbral de {threshold:.0f}."
    else:
        subject = f"{len(rows)} ofertas con match de {threshold:.0f}% o más"
        intro = f"Encontré {len(rows)} ofertas con match de {threshold:.0f}% o más."
    greeting = f"Hola {profile.nombre.split()[0] if profile.nombre else ''},".rstrip()
    blocks = ["\n".join(_offer_lines(row)) for row in rows]
    body = "\n\n".join([greeting, intro, *blocks, ""])
    return subject, body


def smtp_send(to: str, subject: str, body: str) -> None:
    cfg = config.smtp_settings()
    if cfg is None:
        raise RuntimeError("SMTP no configurado")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["from"]
    message["To"] = to
    message.set_content(body)
    # Versión HTML para que el link sea clickeable aunque el cliente no detecte la URL.
    html_body = "<br>\n".join(html.escape(line) for line in body.split("\n"))
    html_body = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', html_body)
    message.add_alternative(f"<html><body>{html_body}</body></html>", subtype="html")
    client = smtplib.SMTP_SSL if cfg["ssl"] else smtplib.SMTP
    with client(cfg["host"], cfg["port"], timeout=30) as smtp:
        smtp.ehlo()
        if cfg["tls"] and not cfg["ssl"]:
            smtp.starttls()
            smtp.ehlo()
        smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(message)


def notify_high_matches(db: Database, profile: Profile, sender=None) -> NotifyResult:
    """Manda un solo mail con todas las ofertas nuevas que superan el umbral. No repite avisos ya enviados."""
    if not profile.email_activo:
        return NotifyResult([], [], "", "desactivado")
    pending = db.high_matches_not_notified(profile.umbral_email)
    if not pending:
        return NotifyResult([], [], "", None)
    try:
        to = recipient(profile)
    except RuntimeError:
        reason = "sin_email" if not (profile.email or os.environ.get("JOB_AGENT_EMAIL_TO")) else "destinatario"
        return NotifyResult([], pending, "", reason)
    if sender is None:
        if config.smtp_settings() is None:
            return NotifyResult([], pending, to, "sin_smtp")
        sender = smtp_send
    subject, body = render_match_email(profile, pending)
    try:
        sender(to, subject, body)
    except smtplib.SMTPAuthenticationError as exc:
        log.warning("El servidor rechazó las credenciales: %s", exc)
        return NotifyResult([], pending, to, "credenciales")
    except Exception as exc:  # un fallo de SMTP no tiene que abortar la búsqueda
        log.warning("No pude avisar de %d oferta(s) a %s: %s", len(pending), to, exc)
        return NotifyResult([], pending, to, "error_smtp")
    for row in pending:
        db.mark_notified(row["id"], float(row["score"]), to)
    return NotifyResult([row["id"] for row in pending], [], to, None)


def send_test_email(profile: Profile) -> str:
    """Manda un mail de prueba y devuelve el destinatario. Lanza la excepción de SMTP si falla."""
    to = recipient(profile)
    if config.smtp_settings() is None:
        raise RuntimeError("Faltan JOB_AGENT_SMTP_HOST, JOB_AGENT_SMTP_USER o JOB_AGENT_SMTP_PASSWORD.")
    smtp_send(
        to,
        "Prueba del agente de empleo",
        "Si estás leyendo esto, el agente ya puede avisarte cuando aparezca una oferta con match alto.\n",
    )
    return to
