# Agente de búsqueda de empleo (LinkedIn)

Un agente de línea de comandos que, a partir de **tu experiencia laboral y tus aspiraciones de carrera**:

1. arma un perfil robusto a partir de tu CV, tu perfil de LinkedIn y un cuestionario corto,
2. busca roles abiertos en LinkedIn (búsqueda pública, sin necesidad de login),
3. puntúa cada oferta de 0 a 100 explicando **por qué** encaja y **qué te falta**,
4. te avisa por mail cuando una oferta matchea de verdad (85% o más),
5. lleva el **seguimiento de cada postulación** (postulado → en revisión → entrevista → oferta / rechazo / sin respuesta),
6. te dice cada día **qué acción tomar** (mandar follow-up, marcar como enfriada, postularte a un buen match que se te está pasando),
7. mide tu embudo (tasa de respuesta, de entrevista, y qué puntaje de match tenían las ofertas que sí te respondieron) para que calibres tu búsqueda.

Todo se guarda localmente en un archivo SQLite (`job_agent.db`); tu perfil vive en `perfil.yaml`. Ninguno de los dos se sube al repo (`.gitignore`).

## Instalación

```bash
cd agente_empleo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Paso 0: conocer al candidato

Antes de la búsqueda continua, `conocer` junta tres cosas y escribe `perfil.yaml`:

1. **Un archivo de CV** (`.txt`, `.md`, `.pdf` o `.docx`): nombre, titular, experiencia, educación, habilidades e idiomas.
2. **Tu perfil de LinkedIn**, de una de estas tres formas:
   - el PDF que genera LinkedIn desde tu perfil (*Más → Guardar como PDF*), con `--export perfil.pdf`. Es inmediato y trae nombre, titular, extracto, experiencia, educación, aptitudes principales e idiomas;
   - el ZIP de *Ajustes → Privacidad de los datos → Obtener una copia de tus datos*, también con `--export`. Tarda hasta 24 h pero es el más completo (todas las habilidades y la descripción de cada rol);
   - la URL pública (`https://www.linkedin.com/in/tu-usuario/`), que se lee como la vería un visitante sin sesión. LinkedIn esconde gran parte del perfil y a veces bloquea la consulta.
3. **Un cuestionario** sobre lo que el CV no dice: si el remoto es excluyente, si te interesa relocation y a dónde, salario mínimo, tipo de contrato, disponibilidad, viajes, seniority, industrias, palabras y empresas a evitar, qué estás aprendiendo y qué tiene que tener el próximo rol.

```bash
python -m job_agent conocer --cv cv.pdf --export Profile.pdf              # PDF "Guardar como PDF"
python -m job_agent conocer --cv cv.pdf --export LinkedInExport.zip       # ZIP "copia de tus datos"
python -m job_agent conocer --cv cv.pdf --linkedin https://www.linkedin.com/in/tu-usuario/
python -m job_agent conocer --cv cv.pdf --respuestas respuestas.yaml      # sin preguntas por consola
```

La primera vez, la biografía del `perfil.ejemplo.yaml` no se mezcla con la tuya: solo se conservan búsquedas y reglas que ya hubieras editado. Si más adelante actualizás el CV, `--sobrescribir` pisa la biografía guardada. No subas el CV al repo (`cv.*` está en `.gitignore`).

El cuestionario deja estas restricciones, y el matcher las aplica:

| Respuesta | Efecto en el match |
|---|---|
| Remoto excluyente | Una oferta presencial o híbrida pierde 40 puntos; si no aclara modalidad, pierde 15. |
| Sin relocation | Una ubicación fuera de donde vivís o de tus preferencias pierde puntos extra. |
| Relocation a ciertos destinos | Esas ciudades suman como ubicación válida. |
| Salario mínimo | Si la oferta publica un tope en la misma moneda y está debajo, pierde 25 puntos. |
| Empresas a evitar | Esa empresa pierde 40 puntos. |
| No querés viajar | Si piden viajes frecuentes, pierde 12 puntos. |

Si no hay consultas de búsqueda armadas, `conocer` genera una por cada rol objetivo (y otra con filtro remoto si aceptás remoto).

## Paso 1: revisar el perfil

Si preferís no pasar por `conocer`, podés arrancar del ejemplo y editarlo a mano:

```bash
python -m job_agent perfil init     # crea perfil.yaml a partir de perfil.ejemplo.yaml
```

`conocer` es el camino recomendado. Las secciones que más pesan:

```bash
python -m job_agent perfil init     # crea perfil.yaml a partir de perfil.ejemplo.yaml
```

Editá `perfil.yaml`. Las secciones que más pesan en el matching:

| Sección | Para qué se usa |
|---|---|
| `habilidades`, `idiomas` | Se cruzan contra lo que pide cada oferta. Lo que piden y no tenés aparece como brecha. |
| `resumen`, `experiencia` | Similitud textual (TF-IDF) con la descripción de la oferta. |
| `aspiraciones.roles_objetivo` | Se compara con el título de la oferta. Es la señal más fuerte. |
| `aspiraciones.seniority`, `modalidad`, `ubicaciones` | Premian o penalizan según lo que buscás. |
| `aspiraciones.evitar`, `empresas_evitar` | Palabras o empresas que restan puntos. |
| `aspiraciones.remoto_excluyente`, `relocation`, `salario_minimo` | Restricciones duras que salen del cuestionario. |
| `aspiraciones.aprendiendo` | Habilidades en las que estás trabajando: si una oferta las pide, no cuentan como brecha grave. |
| `notificaciones.umbral_match` | A partir de qué puntaje se manda mail (85 por defecto). |
| `busqueda.consultas` | Qué buscar en LinkedIn (keywords + ubicación + remoto). |
| `seguimiento` | Cada cuántos días avisar / dar por perdida una postulación. |

```bash
python -m job_agent perfil ver       # resumen de lo que entendió el agente
```

## Paso 2: buscar y matchear

```bash
python -m job_agent buscar                       # corre todas las consultas del perfil
python -m job_agent buscar --keywords "Analytics Engineer" --location Argentina --remoto
python -m job_agent matches                     # solo match de 80 o más
python -m job_agent matches --min 60             # bajar el corte para esta corrida
python -m job_agent matches --detalle --top 5    # con razones y brechas
python -m job_agent ver 4446531276 --descripcion # una oferta en detalle
python -m job_agent cv 4446531276               # CV orientado a esa oferta, en cvs/
```

Ejemplo real de salida:

```
[4446531276] Ssr. Data Scientist — Monks  (Buenos Aires, Argentina)
  Match: 64/100   Estado: descubierto   Publicada: 2026-09-28
  https://ar.linkedin.com/jobs/view/ssr-data-scientist-at-monks-4446531276
   + Habilidades en común (6/16): bigquery, estadistica, ingles, looker studio, python, sql
   + Piden algo que estás aprendiendo: airflow, dbt
   + El título coincide con tu rol objetivo 'Data Scientist'
   + Seniority 'semi senior' acorde a lo que buscás
   - Piden y no tenés: aws, azure, elt, etl, ga4, gcp, google analytics, terraform
```

También podés cargar ofertas que encontraste por tu cuenta (de LinkedIn o de cualquier otro portal):

```bash
python -m job_agent importar "https://www.linkedin.com/jobs/view/4469324280/"
python -m job_agent agregar --titulo "Data Analyst Sr" --empresa "Globant" --url https://... --descripcion oferta.txt
```

## Aviso por mail (solo matches altos)

Cada vez que `buscar`, `importar`, `agregar` o `repuntuar` encuentra una oferta **nueva** con match de **85 o más** (`notificaciones.umbral_match`), manda un mail con el puesto, la empresa, el link, las razones y las brechas. No repite el aviso de una oferta ya notificada.

```bash
export JOB_AGENT_SMTP_HOST=smtp.gmail.com
export JOB_AGENT_SMTP_PORT=587
export JOB_AGENT_SMTP_USER=tu@gmail.com
export JOB_AGENT_SMTP_PASSWORD=la-clave-de-aplicacion
export JOB_AGENT_SMTP_FROM=tu@gmail.com
# opcional, si el destinatario no es contacto.email del perfil:
export JOB_AGENT_EMAIL_TO=tu@gmail.com

python -m job_agent buscar
python -m job_agent notificar          # reintenta los que quedaron sin enviar
python -m job_agent buscar --sin-mail  # esta corrida no avisa
```

Sin esas variables la búsqueda sigue igual y te lista por consola los matches que habría mandado. Para Gmail hace falta una [clave de aplicación](https://myaccount.google.com/apppasswords), no la contraseña de la cuenta.

### Matching con LLM (opcional)

Con `OPENAI_API_KEY` en el entorno, `--llm` refina el puntaje con un modelo de lenguaje que entiende contexto
(por ejemplo, que "experiencia en herramientas de BI" cubre Power BI) y agrega un consejo para adaptar el CV a esa oferta:

```bash
export OPENAI_API_KEY=sk-...
python -m job_agent buscar --llm
python -m job_agent repuntuar --llm     # re-evaluar todo lo ya guardado
```

El puntaje final mezcla 40% heurística + 60% LLM. Cualquier API compatible con OpenAI sirve (`OPENAI_BASE_URL`, `JOB_AGENT_LLM_MODEL`).

## Paso 3: seguimiento de postulaciones

```bash
python -m job_agent postular 4446531276 --nota "CV versión data science, carta corta"
python -m job_agent estado 4446531276 en_revision --nota "Me escribió la recruiter"
python -m job_agent estado 4446531276 entrevista --proxima-accion 2026-10-03 --nota "Entrevista técnica 10hs"
python -m job_agent estado 4446531276 oferta        # o rechazado / sin_respuesta
python -m job_agent descartar 4469324280 --nota "Presencial en otra ciudad"

python -m job_agent pipeline            # todas tus postulaciones activas
python -m job_agent ver 4446531276      # historial completo de una postulación
```

Estados: `descubierto` → `interesado` → `postulado` → `en_revision` → `entrevista` → `oferta` | `rechazado` | `sin_respuesta`, más `descartado`.

### Qué hacer hoy

```bash
python -m job_agent seguimiento
```

```
Acciones sugeridas para hoy (2026-09-28):

[URGENTE] [4412...] Data Scientist — PwC (postulado, 11 días)
    -> Mandá un mensaje de seguimiento al reclutador (breve, reafirmando interés)
[URGENTE] [4446...] Ssr. Data Scientist — Monks (entrevista, 6 días)
    -> Pasaron días desde la entrevista: agradecé y pedí feedback / próximos pasos
[PRONTO]  [4399...] Analytics Engineer — Globant (postulado, 32 días)
    -> Sin novedades hace mucho: marcá `sin_respuesta` o hacé un último intento
[CUANDO PUEDAS] [4471...] ML Engineer — Svitla (descubierto, 9 días)
    -> Match 78/100 y la oferta ya tiene 9 días: postulate o descartala
```

`seguimiento --auto` marca automáticamente como `sin_respuesta` las postulaciones que superaron `dias_para_marcar_sin_respuesta`.

### Cómo viene el embudo

```bash
python -m job_agent stats
python -m job_agent exportar mis_postulaciones.csv
```

`stats` muestra cuántas postulaciones enviaste, tasa de respuesta y de entrevista, y el match promedio de las que
avanzaron vs. las rechazadas vs. las que nunca respondieron. Si las que avanzan tienen match > 75 y las que no, < 60,
ya sabés dónde poner el umbral (`puntaje_minimo_para_recomendar`) y a qué dedicarle tiempo.

## Rutina sugerida

```bash
# cada mañana (o con cron)
python -m job_agent buscar && python -m job_agent seguimiento --auto
```

Ejemplo de cron diario a las 9:00:

```
0 9 * * * cd /ruta/agente_empleo && JOB_AGENT_HOME=/ruta/datos .venv/bin/python -m job_agent buscar >> agente.log 2>&1
```

## Sobre LinkedIn: límites y responsabilidad

LinkedIn **no ofrece una API oficial de búsqueda de empleos** para desarrolladores individuales, y sus Términos de
Servicio restringen la extracción automatizada. Este agente usa los endpoints públicos que carga la página
`linkedin.com/jobs/search` para visitantes **sin sesión** (no usa tu cuenta ni tu contraseña), con estas precauciones:

* el mismo criterio vale para leer *tu* perfil: la página pública o el ZIP oficial de exportación, nunca tu contraseña ni la cookie de sesión;
* pausa entre requests (`JOB_AGENT_REQUEST_DELAY`, 2 s por defecto) y reintentos con espera ante `429`;
* solo se descarga el detalle de las ofertas que todavía no están en tu base;
* si LinkedIn cambia el HTML o bloquea, el error se informa y el resto del flujo (matching, seguimiento) sigue funcionando
  con las ofertas que cargues a mano (`importar` / `agregar`).

**No postula automáticamente**: la decisión y el envío son tuyos; el agente registra y hace seguimiento. Usalo con
volúmenes razonables (algunas consultas por día) y bajo tu propia responsabilidad.

## Estructura

```
agente_empleo/
├── perfil.ejemplo.yaml      # plantilla del perfil
├── job_agent/
│   ├── cli.py               # comandos
│   ├── onboarding.py        # CV + LinkedIn + cuestionario
│   ├── cv_parser.py         # lectura de txt, md, pdf y docx
│   ├── profile.py           # carga y validación del perfil
│   ├── matcher.py           # puntaje heurístico explicable
│   ├── notify.py            # mail cuando el match supera el umbral
│   ├── llm.py               # refinamiento opcional con LLM
│   ├── db.py                # SQLite: ofertas, matches, postulaciones, eventos
│   ├── tracker.py           # reglas de seguimiento y estadísticas del embudo
│   └── sources/
│       ├── linkedin.py         # búsqueda pública de ofertas e importación por URL
│       └── linkedin_profile.py # perfil público, PDF "guardar como PDF" o ZIP de "descargar mis datos"
└── tests/
```

```bash
python -m pytest            # tests (no tocan la red)
```
