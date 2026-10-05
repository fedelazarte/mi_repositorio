from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from . import config, llm
from .db import Database
from .matcher import score_job, score_jobs
from .models import CLOSED_STATUSES, STATUSES, Job
from .priority_companies import priority_company
from .profile import Profile, ProfileError
from .schedule import install_daily
from .sources.linkedin import LinkedInError, LinkedInGuestSource
from .notify import job_link, notify_high_matches, recipient, send_test_email
from .onboarding import run_onboarding
from .tracker import auto_expire, funnel_stats, pending_follow_ups

log = logging.getLogger("job_agent")

# Por debajo de esto el listado es ruido. --min lo baja o lo sube para una corrida.
MIN_MATCH_MOSTRADO = 80


# ------------------------------------------------------------------ helpers --
def _short(text: str | None, width: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_table(headers: list[str], rows: list[list[str]], widths: list[int]) -> None:
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*[_short(h, w) for h, w in zip(headers, widths)]))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[_short(str(c), w) for c, w in zip(row, widths)]))


def _min_a_mostrar(profile: Profile, explicito: float | None) -> float:
    if explicito is not None:
        return explicito
    return max(MIN_MATCH_MOSTRADO, profile.puntaje_minimo)


def _open_for_highlight() -> list[str]:
    return [status for status in STATUSES if status not in CLOSED_STATUSES and status not in ("postulado", "en_revision", "entrevista")]


def _is_auto_reject(row) -> bool:
    try:
        gaps = row["gaps"]
    except (KeyError, IndexError, TypeError):
        return False
    return bool(gaps) and "Rechazo automático" in str(gaps)


def select_destacadas(rows, limit: int = 5) -> list:
    """Las mejores ofertas cuya empresa está en la lista prioritaria. `rows` ya viene ordenado por puntaje."""
    picked = []
    for row in rows:
        if _is_auto_reject(row):
            continue
        if priority_company(row["company"]):
            picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def select_para_aplicar(rows, *, minimo: float = 90, limit: int = 3) -> list:
    """Las mejores ofertas abiertas, de cualquier empresa, a las que vale la pena postularse."""
    picked = []
    for row in rows:
        if _is_auto_reject(row):
            continue
        if float(row["score"]) < minimo:
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def _print_oferta(row) -> None:
    place = row["location"] or ""
    print(f"  {row['score']:.0f}  [{row['id']}] {row['title']} — {row['company']} ({place})")
    link = job_link(row)
    if link:
        print(f"       {link}")


def _print_destacadas(db: Database) -> None:
    rows = db.list_matches(min_score=0, statuses=_open_for_highlight(), limit=5000)
    picked = select_destacadas(rows)
    print("\nDestacadas Tier 1")
    if not picked:
        print("  Ninguna empresa de la lista entre las ofertas abiertas.")
    for row in picked:
        _print_oferta(row)
    aplicar = select_para_aplicar(rows)
    print("\nAplicar sí o sí")
    if not aplicar:
        print("  Ninguna oferta abierta llega a 90.")
        return
    for row in aplicar:
        _print_oferta(row)


def _load_profile() -> Profile:
    try:
        return Profile.load(config.PROFILE_PATH)
    except ProfileError as exc:
        sys.exit(f"Error en el perfil: {exc}")


def _open_db() -> Database:
    return Database(config.DB_PATH)


def _score_and_store(db: Database, jobs: list[Job], profile: Profile, use_llm: bool) -> list:
    results = score_jobs(jobs, profile)
    if use_llm:
        results = [llm.score_with_llm(j, profile, r) for j, r in zip(jobs, results)]
    for r in results:
        db.upsert_match(r)
    return results


def _notify(db: Database, profile: Profile, enabled: bool) -> None:
    if not enabled:
        return
    result = notify_high_matches(db, profile)
    if result.sent:
        cuantas = "1 oferta" if len(result.sent) == 1 else f"{len(result.sent)} ofertas"
        print(f"\nMail enviado a {result.to}: {cuantas} con match ≥ {profile.umbral_email:.0f}.")
    elif result.reason == "sin_smtp":
        cuantas = "1 oferta supera" if len(result.pending) == 1 else f"{len(result.pending)} ofertas superan"
        print(f"\n{cuantas} el {profile.umbral_email:.0f}, pero falta configurar el mail.")
        print("Definí JOB_AGENT_SMTP_HOST, JOB_AGENT_SMTP_USER y JOB_AGENT_SMTP_PASSWORD.")
        for row in result.pending:
            print(f"  {row['score']:.0f}  {row['title']} — {row['company']}")
    elif result.reason == "destinatario":
        print("\nEl destinatario no es un mail válido. Tiene que ser una sola dirección, sin espacios.")
        print("Corregí `contacto.email` en perfil.yaml, o la variable JOB_AGENT_EMAIL_TO si la definiste.")
    elif result.reason == "sin_email":
        cuantas = "1 oferta" if len(result.pending) == 1 else f"{len(result.pending)} ofertas"
        print(f"\nHay {cuantas} con match ≥ {profile.umbral_email:.0f} y el perfil no tiene contacto.email.")
        print("Agregalo con `conocer` o en perfil.yaml.")
    elif result.reason == "credenciales":
        print("\nEl servidor de mail rechazó el usuario o la contraseña. Nada se marcó como enviado.")
        print("Si es Gmail: usá una clave de aplicación (16 letras), sin espacios o entre comillas,")
        print("y JOB_AGENT_SMTP_USER tiene que ser la dirección completa. Probá con `notificar --prueba`.")
    elif result.reason == "error_smtp":
        print(f"\nNo pude enviar {len(result.pending)} aviso(s). Revisá la config SMTP (-v para el detalle).")


def _print_match_row(row) -> None:
    print(f"\n[{row['id']}] {row['title']} — {row['company']}  ({row['location']})")
    print(f"  Match: {row['score']:.0f}/100   Estado: {row['status']}   Publicada: {row['posted_at'] or '?'}")
    if row["url"]:
        print(f"  {row['url']}")
    for reason in json.loads(row["reasons"] or "[]"):
        print(f"   + {reason}")
    for gap in json.loads(row["gaps"] or "[]"):
        print(f"   - {gap}")
    if row["advice"]:
        print(f"   * Consejo: {row['advice']}")


# ----------------------------------------------------------------- commands --
def cmd_perfil(args) -> None:
    if args.accion == "init":
        if config.PROFILE_PATH.exists() and not args.forzar:
            sys.exit(f"Ya existe {config.PROFILE_PATH}. Usá --forzar para sobreescribirlo.")
        shutil.copy(config.EXAMPLE_PROFILE_PATH, config.PROFILE_PATH)
        print(f"Perfil de ejemplo creado en {config.PROFILE_PATH}. Editalo con tus datos y aspiraciones.")
        return
    p = _load_profile()
    print(f"{p.nombre} — {p.titulo_actual} ({p.anios_experiencia} años)")
    print(f"Roles objetivo : {', '.join(p.roles_objetivo)}")
    print(f"Seniority      : {', '.join(p.seniority) or '-'}")
    print(f"Modalidad      : {', '.join(p.modalidad) or '-'}")
    print(f"Ubicaciones    : {', '.join(p.ubicaciones) or '-'}")
    print(f"Habilidades    : {', '.join(p.habilidades)}")
    print(f"Idiomas        : {', '.join(p.idiomas) or '-'}")
    print(f"Aprendiendo    : {', '.join(p.aprendiendo) or '-'}")
    print(f"Evitar         : {', '.join(p.evitar) or '-'}")
    print(f"Consultas      : {len(p.consultas)} (últimos {p.publicado_ultimos_dias} días)")
    remoto = "sí, excluyente" if p.remoto_excluyente else "no"
    relocation = "sí" if p.relocation else "no"
    if p.relocation_destinos:
        relocation += f" ({', '.join(p.relocation_destinos)})"
    print(f"Remoto excl.   : {remoto}")
    print(f"Relocation     : {relocation}")
    print(f"Salario mínimo : {f'{p.salario_minimo:.0f} {p.moneda}' if p.salario_minimo else '-'}")
    print(f"Avisos por mail: {p.email or '(sin mail)'} cuando el match sea ≥ {p.umbral_email:.0f}")


def cmd_buscar(args) -> None:
    profile = _load_profile()
    db = _open_db()
    source = LinkedInGuestSource()
    queries = profile.consultas
    if args.keywords:
        queries = [{"keywords": args.keywords, "location": args.location or "", "remoto": args.remoto}]
    if not queries:
        sys.exit("No hay consultas: agregalas en `busqueda.consultas` del perfil o pasá --keywords.")

    if not (profile.raw.get("fuentes") or {}).get("actualizado"):
        print("Aviso: todavía no corriste `conocer` (CV + LinkedIn + preguntas). El match usa solo lo que haya en perfil.yaml.\n")
    print(f"Buscando en LinkedIn con {len(queries)} consulta(s)...")
    jobs = source.search_many(queries, posted_within_days=profile.publicado_ultimos_dias, limit=args.limite or profile.max_resultados)
    new_jobs = [j for j in jobs if db.upsert_job(j)]
    print(f"Encontradas {len(jobs)} ofertas, {len(new_jobs)} nuevas.")

    # Sólo bajamos la descripción de las que todavía no la tienen (cada una es un request).
    to_enrich = [j for j in jobs if not db.has_description(j.id)]
    if args.sin_detalle:
        to_enrich = []
    if to_enrich:
        print(f"Descargando el detalle de {len(to_enrich)} ofertas (pausa de {config.REQUEST_DELAY:.0f}s entre cada una)...")
    for i, job in enumerate(to_enrich, 1):
        try:
            source.enrich(job)
            db.upsert_job(job)
        except LinkedInError as exc:
            log.warning("No pude bajar el detalle de %s: %s", job.id, exc)
        if i % 10 == 0:
            print(f"  {i}/{len(to_enrich)}")

    # Re-puntuamos con los datos frescos de la base (algunas ya tenían descripción).
    fresh = [db.get_job(j.id) for j in jobs]
    fresh = [j for j in fresh if j is not None]
    _score_and_store(db, fresh, profile, args.llm)

    minimo = _min_a_mostrar(profile, args.min)
    print(f"\nMejores matches (>= {minimo:.0f}) todavía sin postular:")
    open_statuses = [s for s in STATUSES if s not in CLOSED_STATUSES and s not in ("postulado", "en_revision", "entrevista")]
    rows = db.list_matches(min_score=minimo, statuses=open_statuses, limit=args.top)
    if not rows:
        print(f"  (ninguna con {minimo:.0f} o más; `matches --min 60` muestra el resto)")
    for row in rows:
        _print_match_row(row)
    _notify(db, profile, not args.sin_mail)
    if not getattr(args, "sin_destacadas", False):
        _print_destacadas(db)
    db.close()


def cmd_importar(args) -> None:
    profile = _load_profile()
    db = _open_db()
    source = LinkedInGuestSource()
    for ref in args.urls:
        try:
            job = source.fetch(ref)
        except LinkedInError as exc:
            print(f"No pude importar {ref}: {exc}")
            continue
        db.upsert_job(job)
        _score_and_store(db, [job], profile, args.llm)
        _print_match_row(db.get_match_row(job.id))
    _notify(db, profile, not args.sin_mail)
    db.close()


def cmd_agregar(args) -> None:
    """Carga manual de una oferta (de cualquier portal) pegando su descripción."""
    profile = _load_profile()
    db = _open_db()
    description = Path(args.descripcion).read_text(encoding="utf-8") if args.descripcion and Path(args.descripcion).exists() else (args.descripcion or "")
    if not description and not sys.stdin.isatty():
        description = sys.stdin.read()
    job_id = args.id or f"manual-{abs(hash((args.titulo, args.empresa))) % 10**8}"
    job = Job(id=job_id, title=args.titulo, company=args.empresa, location=args.ubicacion or "",
              url=args.url or "", source="manual", description=description, posted_at=date.today().isoformat())
    db.upsert_job(job)
    _score_and_store(db, [job], profile, args.llm)
    _print_match_row(db.get_match_row(job.id))
    _notify(db, profile, not args.sin_mail)
    db.close()


def cmd_matches(args) -> None:
    profile = _load_profile()
    db = _open_db()
    minimo = _min_a_mostrar(profile, args.min)
    statuses = [args.estado] if args.estado else None
    rows = db.list_matches(min_score=minimo, statuses=statuses, limit=args.top, company=args.empresa)
    if not rows:
        print("No hay matches con esos filtros. Ejecutá `buscar` primero o bajá `--min`.")
    if args.detalle:
        for row in rows:
            _print_match_row(row)
    else:
        _print_table(
            ["ID", "Match", "Estado", "Título", "Empresa", "Ubicación", "Publicada"],
            [[r["id"], f"{r['score']:.0f}", r["status"], r["title"], r["company"], r["location"], r["posted_at"] or "?"] for r in rows],
            [12, 5, 12, 38, 24, 22, 10],
        )
        print("\nUsá `ver <ID>` para el detalle, `postular <ID>` cuando te postules.")
    _print_destacadas(db)
    db.close()


def cmd_ver(args) -> None:
    db = _open_db()
    row = db.get_match_row(args.id)
    if row is None:
        sys.exit(f"No conozco la oferta {args.id}.")
    if row["score"] is None:
        print(f"\n[{row['id']}] {row['title']} — {row['company']}  (sin puntuar)")
    else:
        _print_match_row(row)
    if row["notes"]:
        print(f"\nNotas:\n{row['notes']}")
    events = db.events_for(args.id)
    if events:
        print("\nHistorial:")
        for e in events:
            note = f" — {e['note']}" if e["note"] else ""
            print(f"  {e['at'][:16]}  {e['status_from'] or '·'} -> {e['status_to']}{note}")
    if args.descripcion:
        print(f"\nDescripción:\n{row['description'] or '(no descargada)'}")
    db.close()


def cmd_estado(args) -> None:
    db = _open_db()
    try:
        prev, new = db.set_status(args.id, args.estado, note=args.nota, next_action_at=args.proxima_accion)
    except KeyError:
        sys.exit(f"No conozco la oferta {args.id}. Importala primero con `importar <url>`.")
    row = db.get_match_row(args.id)
    print(f"{row['title']} — {row['company']}: {prev} -> {new}")
    db.close()


def cmd_postular(args) -> None:
    args.estado = "postulado"
    cmd_estado(args)
    print("Registrada la postulación. El agente te avisará si pasan días sin respuesta (`seguimiento`).")


def cmd_descartar(args) -> None:
    args.estado = "descartado"
    args.proxima_accion = None
    cmd_estado(args)


def cmd_pipeline(args) -> None:
    db = _open_db()
    statuses = [args.estado] if args.estado else [s for s in STATUSES if s not in ("descubierto", "descartado")]
    rows = db.list_applications(statuses=statuses)
    if not rows:
        print("Todavía no registraste postulaciones. Usá `postular <ID>`.")
    else:
        _print_table(
            ["ID", "Estado", "Match", "Título", "Empresa", "Postulada", "Últ. cambio", "Próx. acción"],
            [[r["id"], r["status"], f"{r['score']:.0f}" if r["score"] is not None else "-", r["title"], r["company"],
              (r["applied_at"] or "")[:10], (r["updated_at"] or "")[:10], (r["next_action_at"] or "")[:10]] for r in rows],
            [12, 13, 5, 34, 22, 10, 11, 12],
        )
    db.close()


def cmd_seguimiento(args) -> None:
    profile = _load_profile()
    db = _open_db()
    if args.auto:
        expired = auto_expire(db, profile)
        if expired:
            print(f"Marcadas como sin_respuesta ({len(expired)}): {', '.join(expired)}")
    items = pending_follow_ups(db, profile)
    if not items:
        print("Nada pendiente hoy. Corré `buscar` para encontrar ofertas nuevas.")
    else:
        labels = {1: "URGENTE", 2: "PRONTO", 3: "CUANDO PUEDAS"}
        print(f"Acciones sugeridas para hoy ({date.today().isoformat()}):\n")
        for f in items:
            print(f"[{labels[f.priority]}] [{f.job_id}] {f.title} — {f.company} ({f.status}, {f.days} días)")
            print(f"    -> {f.action}")
    db.close()


def cmd_stats(args) -> None:
    db = _open_db()
    s = funnel_stats(db)
    print(f"Ofertas conocidas      : {s['total_ofertas']}")
    for status, n in s["por_estado"].items():
        print(f"  {status:<14}: {n}")
    print(f"\nPostulaciones enviadas : {s['postulaciones']}")
    print(f"Respuestas recibidas   : {s['respuestas']}  (tasa {s['tasa_respuesta'] if s['tasa_respuesta'] is not None else '-'}%)")
    print(f"Entrevistas            : {s['entrevistas']}  (tasa {s['tasa_entrevista'] if s['tasa_entrevista'] is not None else '-'}%)")
    print(f"Ofertas recibidas      : {s['ofertas_recibidas']}    Rechazos: {s['rechazos']}")
    print("\nMatch promedio según resultado (para calibrar el puntaje):")
    print(f"  con respuesta positiva : {s['score_promedio_con_respuesta'] if s['score_promedio_con_respuesta'] is not None else '-'}")
    print(f"  rechazadas             : {s['score_promedio_rechazadas'] if s['score_promedio_rechazadas'] is not None else '-'}")
    print(f"  sin respuesta          : {s['score_promedio_sin_respuesta'] if s['score_promedio_sin_respuesta'] is not None else '-'}")
    db.close()


def cmd_exportar(args) -> None:
    db = _open_db()
    rows = db.list_applications()
    out = Path(args.archivo)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "estado", "match", "titulo", "empresa", "ubicacion", "url", "publicada", "postulada", "ultimo_cambio", "proxima_accion", "notas"])
        for r in rows:
            writer.writerow([r["id"], r["status"], r["score"], r["title"], r["company"], r["location"], r["url"],
                             r["posted_at"], r["applied_at"], r["updated_at"], r["next_action_at"], r["notes"]])
    print(f"Exportadas {len(rows)} filas a {out}")
    db.close()


def cmd_repuntuar(args) -> None:
    profile = _load_profile()
    db = _open_db()
    ids = [r["id"] for r in db.list_applications()]
    jobs = [db.get_job(i) for i in ids]
    jobs = [j for j in jobs if j is not None]
    _score_and_store(db, jobs, profile, args.llm)
    print(f"Re-puntuadas {len(jobs)} ofertas con el perfil actual.")
    _notify(db, profile, not args.sin_mail)
    db.close()


def cmd_diario(args) -> None:
    if args.instalar:
        try:
            result = install_daily(workdir=config.HOME, hour=args.hora, minute=args.minuto)
        except ValueError as exc:
            sys.exit(str(exc))
        if sys.platform == "darwin":
            uid = os.getuid()
            target = f"gui/{uid}"
            plist = result
            os.system(f"launchctl bootout {target} {plist} >/dev/null 2>&1")
            code = os.system(f"launchctl bootstrap {target} {plist}")
            print(f"Corrida diaria instalada a las {args.hora:02d}:{args.minuto:02d}.")
            print(f"  {plist}")
            print(f"  Log: {config.HOME / 'diario.log'}")
            if code != 0:
                print("No pude activarla con launchctl. El archivo quedó escrito; revisá el mensaje de arriba.")
            if "JOB_AGENT_SMTP_PASSWORD" not in os.environ:
                print("Aviso: no había contraseña SMTP en esta terminal. El mail de las 9 no va a salir hasta que reinstales con las variables cargadas (`source ~/.zshrc`).")
        else:
            print("Agregá esta línea con `crontab -e`:")
            print(result)
        return
    print(f"Corrida diaria ({date.today().isoformat()}): busco ofertas y reviso postulaciones.\n")
    args.keywords = None
    args.location = ""
    args.remoto = False
    args.limite = None
    args.sin_detalle = False
    args.llm = False
    args.min = None
    args.top = 10
    args.sin_mail = False
    args.sin_destacadas = True
    cmd_buscar(args)
    args.auto = True
    cmd_seguimiento(args)
    db = _open_db()
    _print_destacadas(db)
    db.close()


def cmd_conocer(args) -> None:
    try:
        run_onboarding(
            cv_path=Path(args.cv) if args.cv else None,
            linkedin_url=args.linkedin,
            linkedin_export=Path(args.export) if args.export else None,
            answers_path=Path(args.respuestas) if args.respuestas else None,
            overwrite=args.sobrescribir,
        )
    except ProfileError as exc:
        sys.exit(f"Error: {exc}")


def cmd_notificar(args) -> None:
    profile = _load_profile()
    if args.prueba:
        cfg = config.smtp_settings()
        if cfg:
            print(f"Servidor {cfg['host']}:{cfg['port']}  usuario {cfg['user']}  contraseña de {len(cfg['password'])} caracteres", flush=True)
        try:
            print(f"Destinatario {recipient(profile)}", flush=True)
            to = send_test_email(profile)
        except Exception as exc:
            sys.exit(f"No pude mandar el mail de prueba: {exc}")
        print(f"Mail de prueba enviado a {to}. Revisá la bandeja (y spam).")
        return
    db = _open_db()
    _notify(db, profile, enabled=True)
    db.close()


# ------------------------------------------------------------------- parser --
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="job_agent", description="Agente de búsqueda de empleo en LinkedIn: matching y seguimiento.")
    p.add_argument("-v", "--verbose", action="store_true", help="mostrar logs detallados")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("perfil", help="crear o ver tu perfil")
    sp.add_argument("accion", choices=["init", "ver"], nargs="?", default="ver")
    sp.add_argument("--forzar", action="store_true")
    sp.set_defaults(func=cmd_perfil)

    sp = sub.add_parser("conocer", help="armar el perfil: CV + LinkedIn + preguntas")
    sp.add_argument("--cv", help="CV en .txt, .md, .pdf o .docx")
    sp.add_argument("--linkedin", help="URL pública del perfil (linkedin.com/in/...)")
    sp.add_argument("--export", help="ZIP de 'descargar mis datos' o PDF de 'guardar como PDF' de tu perfil de LinkedIn")
    sp.add_argument("--respuestas", help="YAML con las respuestas, para no preguntar por consola")
    sp.add_argument("--sobrescribir", action="store_true", help="pisar la biografía ya guardada con el CV/LinkedIn")
    sp.set_defaults(func=cmd_conocer)

    sp = sub.add_parser("buscar", help="buscar ofertas en LinkedIn y puntuarlas")
    sp.add_argument("--keywords", help="ignorar las consultas del perfil y buscar esto")
    sp.add_argument("--location", default="")
    sp.add_argument("--remoto", action="store_true")
    sp.add_argument("--limite", type=int, help="máximo de resultados por consulta")
    sp.add_argument("--top", type=int, default=10, help="cuántos matches mostrar")
    sp.add_argument("--min", type=float, default=None, help="puntaje mínimo a mostrar (80 si no se indica)")
    sp.add_argument("--sin-detalle", action="store_true", help="no descargar descripciones (más rápido, peor matching)")
    sp.add_argument("--llm", action="store_true", help="refinar el puntaje con el modelo local (Ollama)")
    sp.add_argument("--sin-mail", action="store_true", help="no avisar por mail aunque el match supere el umbral")
    sp.set_defaults(func=cmd_buscar)

    sp = sub.add_parser("importar", help="importar ofertas por URL o id de LinkedIn")
    sp.add_argument("urls", nargs="+")
    sp.add_argument("--llm", action="store_true")
    sp.add_argument("--sin-mail", action="store_true")
    sp.set_defaults(func=cmd_importar)

    sp = sub.add_parser("agregar", help="cargar a mano una oferta de cualquier portal")
    sp.add_argument("--titulo", required=True)
    sp.add_argument("--empresa", required=True)
    sp.add_argument("--ubicacion")
    sp.add_argument("--url")
    sp.add_argument("--id")
    sp.add_argument("--descripcion", help="texto o ruta a un .txt (o pasalo por stdin)")
    sp.add_argument("--llm", action="store_true")
    sp.add_argument("--sin-mail", action="store_true")
    sp.set_defaults(func=cmd_agregar)

    sp = sub.add_parser("matches", help="listar ofertas ordenadas por match")
    sp.add_argument("--min", type=float, default=None, help="puntaje mínimo a mostrar (80 si no se indica)")
    sp.add_argument("--top", type=int, default=30)
    sp.add_argument("--estado", choices=STATUSES)
    sp.add_argument("--empresa")
    sp.add_argument("--detalle", action="store_true", help="mostrar razones y brechas")
    sp.set_defaults(func=cmd_matches)

    sp = sub.add_parser("ver", help="detalle de una oferta y su historial")
    sp.add_argument("id")
    sp.add_argument("--descripcion", action="store_true")
    sp.set_defaults(func=cmd_ver)

    for name, help_text, func in (
        ("postular", "registrar que te postulaste", cmd_postular),
        ("descartar", "descartar una oferta", cmd_descartar),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("id")
        sp.add_argument("--nota")
        if name == "postular":
            sp.add_argument("--proxima-accion", dest="proxima_accion", help="fecha YYYY-MM-DD para recordarte algo")
        sp.set_defaults(func=func)

    sp = sub.add_parser("estado", help="cambiar el estado de una postulación")
    sp.add_argument("id")
    sp.add_argument("estado", choices=STATUSES)
    sp.add_argument("--nota")
    sp.add_argument("--proxima-accion", dest="proxima_accion", help="fecha YYYY-MM-DD (ej: día de la entrevista)")
    sp.set_defaults(func=cmd_estado)

    sp = sub.add_parser("pipeline", help="ver todas tus postulaciones")
    sp.add_argument("--estado", choices=STATUSES)
    sp.set_defaults(func=cmd_pipeline)

    sp = sub.add_parser("seguimiento", help="qué hacer hoy con tus postulaciones")
    sp.add_argument("--auto", action="store_true", help="marcar sin_respuesta las postulaciones enfriadas")
    sp.set_defaults(func=cmd_seguimiento)

    sp = sub.add_parser("stats", help="embudo y tasas de éxito")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("exportar", help="exportar todo a CSV")
    sp.add_argument("archivo", nargs="?", default="exportaciones/postulaciones.csv")
    sp.set_defaults(func=cmd_exportar)

    sp = sub.add_parser("repuntuar", help="volver a puntuar todo tras cambiar el perfil")
    sp.add_argument("--llm", action="store_true")
    sp.add_argument("--sin-mail", action="store_true")
    sp.set_defaults(func=cmd_repuntuar)

    sp = sub.add_parser("diario", help="buscar ofertas y revisar postulaciones; --instalar lo deja todos los días")
    sp.add_argument("--instalar", action="store_true", help="programar la corrida todos los días")
    sp.add_argument("--hora", type=int, default=9)
    sp.add_argument("--minuto", type=int, default=0)
    sp.set_defaults(func=cmd_diario)

    sp = sub.add_parser("notificar", help="enviar los mails de matches altos que todavía no se avisaron")
    sp.add_argument("--prueba", action="store_true", help="mandar un mail de prueba para verificar la configuración")
    sp.set_defaults(func=cmd_notificar)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    args.func(args)
