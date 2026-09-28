from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    source: str = "linkedin"
    posted_at: Optional[str] = None  # ISO date (YYYY-MM-DD) cuando se conoce
    description: str = ""
    seniority: Optional[str] = None
    employment_type: Optional[str] = None
    remote: Optional[bool] = None

    @property
    def full_text(self) -> str:
        parts = [self.title, self.company, self.location, self.description or ""]
        if self.seniority:
            parts.append(self.seniority)
        if self.employment_type:
            parts.append(self.employment_type)
        return "\n".join(p for p in parts if p)


@dataclass
class MatchResult:
    job_id: str
    score: float  # 0 - 100
    reasons: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    method: str = "heuristico"
    advice: str = ""


# Estados del pipeline de postulación, en el orden "natural" del embudo.
STATUSES = [
    "descubierto",   # la encontró el agente, todavía no hiciste nada
    "interesado",    # la guardaste para postularte
    "postulado",     # enviaste la postulación
    "en_revision",   # el reclutador respondió / está en proceso
    "entrevista",    # tenés una o más entrevistas agendadas
    "oferta",        # te hicieron una oferta
    "rechazado",     # te dijeron que no
    "sin_respuesta", # pasó el tiempo y nadie contestó
    "descartado",    # decidiste no postularte
]

# Estados que implican que la postulación fue enviada en algún momento.
APPLIED_STATUSES = {"postulado", "en_revision", "entrevista", "oferta", "rechazado", "sin_respuesta"}
# Estados que implican que la empresa respondió.
RESPONDED_STATUSES = {"en_revision", "entrevista", "oferta", "rechazado"}
# Estados finales (no se espera más movimiento).
CLOSED_STATUSES = {"oferta", "rechazado", "sin_respuesta", "descartado"}
