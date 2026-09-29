from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ProfileError(Exception):
    pass


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


class Profile:
    """Perfil profesional + aspiraciones, cargado desde `perfil.yaml`."""

    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise ProfileError("El perfil debe ser un mapa YAML (clave: valor).")
        self.raw = data
        asp = data.get("aspiraciones") or {}
        busq = data.get("busqueda") or {}
        seg = data.get("seguimiento") or {}

        self.nombre: str = data.get("nombre", "")
        self.titulo_actual: str = data.get("titulo_actual", "")
        self.anios_experiencia: int = int(data.get("anios_experiencia") or 0)
        self.resumen: str = data.get("resumen", "") or ""
        self.experiencia: list[dict] = data.get("experiencia") or []
        self.habilidades: list[str] = _as_list(data.get("habilidades"))
        self.idiomas: list[str] = _as_list(data.get("idiomas"))

        self.roles_objetivo: list[str] = _as_list(asp.get("roles_objetivo"))
        self.seniority: list[str] = [s.lower() for s in _as_list(asp.get("seniority"))]
        self.modalidad: list[str] = [m.lower() for m in _as_list(asp.get("modalidad"))]
        self.ubicaciones: list[str] = _as_list(asp.get("ubicaciones"))
        self.industrias: list[str] = _as_list(asp.get("industrias_preferidas"))
        self.evitar: list[str] = _as_list(asp.get("evitar"))
        self.aprendiendo: list[str] = _as_list(asp.get("aprendiendo"))
        self.remoto_excluyente: bool = bool(asp.get("remoto_excluyente", False))
        self.relocation: bool = bool(asp.get("relocation", False))
        self.relocation_destinos: list[str] = _as_list(asp.get("relocation_destinos"))
        self.empresas_evitar: list[str] = _as_list(asp.get("empresas_evitar"))
        self.tipo_contrato: list[str] = [c.lower() for c in _as_list(asp.get("tipo_contrato"))]
        self.disponibilidad: str = asp.get("disponibilidad") or ""
        self.viaje: str = (asp.get("viaje") or "").lower()
        self.motivacion: str = asp.get("motivacion") or ""
        self.autorizacion_trabajo: str = asp.get("autorizacion_trabajo") or ""
        self.moneda: str = (asp.get("moneda") or "").upper()
        salario = asp.get("salario_minimo")
        self.salario_minimo: float | None = float(salario) if salario not in (None, "", 0, "0") else None

        contacto = data.get("contacto") or {}
        self.email: str = contacto.get("email") or ""
        self.telefono: str = contacto.get("telefono") or ""
        self.linkedin: str = contacto.get("linkedin") or ""
        self.ubicacion_actual: str = data.get("ubicacion_actual") or ""

        self.consultas: list[dict] = busq.get("consultas") or []
        self.publicado_ultimos_dias: int = int(busq.get("publicado_ultimos_dias") or 7)
        self.max_resultados: int = int(busq.get("max_resultados_por_consulta") or 25)

        self.dias_aviso_followup: int = int(seg.get("dias_sin_respuesta_para_avisar") or 10)
        self.dias_sin_respuesta: int = int(seg.get("dias_para_marcar_sin_respuesta") or 30)
        self.puntaje_minimo: float = float(seg.get("puntaje_minimo_para_recomendar") or 80)
        notif = data.get("notificaciones") or {}
        self.email_activo: bool = bool(notif.get("email", True))
        self.umbral_email: float = float(notif.get("umbral_match", 85))

        if not self.habilidades:
            raise ProfileError("El perfil necesita al menos una entrada en `habilidades`.")
        if not self.roles_objetivo:
            raise ProfileError("El perfil necesita al menos un rol en `aspiraciones.roles_objetivo`.")

    @property
    def texto(self) -> str:
        """Representación textual completa del perfil, usada para similitud TF-IDF y para el LLM."""
        chunks = [self.titulo_actual, self.resumen]
        for exp in self.experiencia:
            chunks.append(" ".join(str(exp.get(k, "")) for k in ("puesto", "empresa", "descripcion")))
        chunks.append(" ".join(self.habilidades))
        chunks.append(" ".join(self.roles_objetivo))
        chunks.append(" ".join(self.industrias))
        chunks.append(self.motivacion)
        chunks.append(self.autorizacion_trabajo)
        return "\n".join(c for c in chunks if c)

    @classmethod
    def load(cls, path: Path) -> "Profile":
        if not path.exists():
            raise ProfileError(
                f"No encontré el perfil en {path}. Ejecutá `python -m job_agent perfil init` y completalo."
            )
        with path.open(encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})
