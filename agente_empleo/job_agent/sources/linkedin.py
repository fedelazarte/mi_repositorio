"""Fuente de ofertas: búsqueda pública de LinkedIn (la que ve un visitante sin sesión).

LinkedIn no ofrece una API oficial de búsqueda de empleos para desarrolladores individuales.
Este módulo usa los mismos endpoints públicos que carga la página `linkedin.com/jobs/search`
para visitantes anónimos. Son endpoints no documentados: pueden cambiar o limitar el tráfico
en cualquier momento. Por eso:

* se respeta una pausa entre requests (`JOB_AGENT_REQUEST_DELAY`),
* ante un 429 se espera y se reintenta un número acotado de veces,
* cualquier fallo se reporta como advertencia y no rompe el resto del flujo,
* siempre podés importar ofertas a mano con `importar <url>`.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Optional
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from ..config import REQUEST_DELAY
from ..models import Job

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PUBLIC_JOB_URL = "https://www.linkedin.com/jobs/view/{job_id}/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}

_JOB_ID_PATTERNS = [
    re.compile(r"currentJobId=(\d{6,})"),
    re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{6,})"),
    re.compile(r"jobPosting[:/](\d{6,})"),
    re.compile(r"^(\d{6,})$"),
]


class LinkedInError(Exception):
    pass


def extract_job_id(url_or_id: str) -> Optional[str]:
    """Extrae el id numérico de una URL de oferta de LinkedIn (o devuelve el id si ya lo es)."""
    text = url_or_id.strip()
    for pattern in _JOB_ID_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def _clean_url(href: str) -> str:
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def parse_search_results(html: str) -> list[Job]:
    """Convierte el HTML del endpoint de búsqueda en una lista de `Job` (sin descripción)."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[Job] = []
    for card in soup.select("div.base-card, li"):
        urn_node = card if card.has_attr("data-entity-urn") else card.select_one("[data-entity-urn]")
        if not urn_node:
            continue
        job_id = urn_node["data-entity-urn"].rsplit(":", 1)[-1]
        if not job_id.isdigit() or any(j.id == job_id for j in jobs):
            continue
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        time_node = card.select_one("time")
        posted = time_node.get("datetime") if time_node and time_node.has_attr("datetime") else None
        jobs.append(
            Job(
                id=job_id,
                title=_text(card.select_one("h3.base-search-card__title, h3")),
                company=_text(card.select_one("h4.base-search-card__subtitle, h4")),
                location=_text(card.select_one("span.job-search-card__location")),
                url=_clean_url(link["href"]) if link and link.has_attr("href") else PUBLIC_JOB_URL.format(job_id=job_id),
                source="linkedin",
                posted_at=posted,
            )
        )
    return jobs


def parse_job_detail(html: str, job_id: str) -> dict:
    """Extrae descripción, criterios y datos de cabecera de la página de detalle de una oferta."""
    soup = BeautifulSoup(html, "html.parser")
    description_node = soup.select_one("div.show-more-less-html__markup, div.description__text")
    description = description_node.get_text("\n", strip=True) if description_node else ""

    seniority = employment_type = None
    for item in soup.select("li.description__job-criteria-item"):
        header = _text(item.select_one("h3")).lower()
        value = _text(item.select_one("span"))
        if not value:
            continue
        if "seniority" in header or "antig" in header or "experiencia" in header:
            seniority = value
        elif "employment" in header or "empleo" in header or "contrat" in header:
            employment_type = value

    title = _text(soup.select_one("h2.top-card-layout__title, h1.top-card-layout__title, h1"))
    company = _text(soup.select_one("a.topcard__org-name-link, span.topcard__flavor"))
    location = _text(soup.select_one("span.topcard__flavor--bullet"))
    time_node = soup.select_one("span.posted-time-ago__text")

    return {
        "id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "seniority": seniority,
        "employment_type": employment_type,
        "posted_hint": _text(time_node) or None,
    }


class LinkedInGuestSource:
    name = "linkedin"

    def __init__(self, session: Optional[requests.Session] = None, delay: float = REQUEST_DELAY, max_retries: int = 3):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        self.max_retries = max_retries
        self._last_request = 0.0

    # ---------------------------------------------------------------- HTTP --
    def _get(self, url: str, params: Optional[dict] = None) -> str:
        for attempt in range(1, self.max_retries + 1):
            wait = self.delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=20)
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise LinkedInError(f"Error de red hablando con LinkedIn: {exc}") from exc
                time.sleep(self.delay * attempt)
                continue
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 503, 999):
                backoff = self.delay * (2 ** attempt)
                log.warning("LinkedIn respondió %s; esperando %.0fs antes de reintentar", resp.status_code, backoff)
                time.sleep(backoff)
                continue
            if resp.status_code == 404:
                raise LinkedInError("La oferta no existe o ya fue cerrada (404).")
            raise LinkedInError(f"LinkedIn respondió HTTP {resp.status_code} para {url}")
        raise LinkedInError("LinkedIn limitó las consultas (rate limit). Probá de nuevo en unos minutos.")

    # -------------------------------------------------------------- search --
    def search(
        self,
        keywords: str,
        location: str = "",
        *,
        remote: bool = False,
        posted_within_days: Optional[int] = None,
        limit: int = 25,
    ) -> list[Job]:
        params: dict[str, str | int] = {"keywords": keywords, "location": location, "start": 0}
        if remote:
            params["f_WT"] = "2"
        if posted_within_days:
            params["f_TPR"] = f"r{posted_within_days * 86400}"

        found: list[Job] = []
        seen: set[str] = set()
        while len(found) < limit:
            params["start"] = len(found)
            html = self._get(SEARCH_URL, params=params)
            page = [j for j in parse_search_results(html) if j.id not in seen]
            if not page:
                break
            for job in page:
                seen.add(job.id)
                found.append(job)
                if len(found) >= limit:
                    break
        return found

    def search_many(self, queries: Iterable[dict], *, posted_within_days: int, limit: int) -> list[Job]:
        """Ejecuta varias consultas del perfil y devuelve las ofertas deduplicadas."""
        merged: dict[str, Job] = {}
        for q in queries:
            keywords = q.get("keywords", "")
            if not keywords:
                continue
            try:
                jobs = self.search(
                    keywords,
                    q.get("location", ""),
                    remote=bool(q.get("remoto")),
                    posted_within_days=q.get("publicado_ultimos_dias", posted_within_days),
                    limit=q.get("max_resultados", limit),
                )
            except LinkedInError as exc:
                log.warning("Consulta '%s' (%s) falló: %s", keywords, q.get("location", ""), exc)
                continue
            log.info("Consulta '%s' (%s): %d ofertas", keywords, q.get("location", ""), len(jobs))
            for job in jobs:
                merged.setdefault(job.id, job)
        return list(merged.values())

    # -------------------------------------------------------------- detail --
    def enrich(self, job: Job) -> Job:
        """Completa descripción, seniority y tipo de empleo de una oferta."""
        detail = parse_job_detail(self._get(JOB_URL.format(job_id=job.id)), job.id)
        job.description = detail["description"] or job.description
        job.seniority = detail["seniority"] or job.seniority
        job.employment_type = detail["employment_type"] or job.employment_type
        job.title = job.title or detail["title"]
        job.company = job.company or detail["company"]
        job.location = job.location or detail["location"]
        job.remote = _detect_remote(job)
        return job

    def fetch(self, url_or_id: str) -> Job:
        """Importa una oferta puntual a partir de su URL o id."""
        job_id = extract_job_id(url_or_id)
        if not job_id:
            raise LinkedInError(f"No pude reconocer un id de oferta de LinkedIn en: {url_or_id}")
        job = Job(id=job_id, title="", company="", location="", url=PUBLIC_JOB_URL.format(job_id=job_id))
        job = self.enrich(job)
        if not job.title and not job.description:
            raise LinkedInError("LinkedIn devolvió una página vacía para esa oferta (¿requiere sesión o fue cerrada?).")
        return job


_REMOTE_RE = re.compile(r"\b(remoto|remote|home ?office|teletrabajo|trabajo desde casa)\b", re.I)
_ONSITE_RE = re.compile(r"\b(presencial|on-?site|in office|oficina)\b", re.I)


def _detect_remote(job: Job) -> Optional[bool]:
    text = f"{job.title} {job.location} {job.description[:1500]}"
    if _REMOTE_RE.search(text):
        return True
    if _ONSITE_RE.search(text):
        return False
    return None
