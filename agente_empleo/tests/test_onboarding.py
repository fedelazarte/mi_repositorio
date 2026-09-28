import zipfile
from pathlib import Path

import yaml
from docx import Document

from job_agent.cv_parser import parse_cv_text
from job_agent.onboarding import run_onboarding
from job_agent.profile import Profile
from job_agent.sources.linkedin_profile import parse_export_zip, parse_public_profile

CV = """
Ana Pérez
Data Analyst
ana@example.com | https://www.linkedin.com/in/ana-perez | Ubicación: Buenos Aires

Resumen
Analista de datos con 4 años de experiencia en producto.

Experiencia
Data Analyst | Empresa Real S.A. | 2022 - actualidad
- Dashboards en Power BI y SQL sobre BigQuery
Analista de Datos Jr. | Startup Deportiva | 2020 - 2022
- Modelo de similitud con scikit-learn y pandas

Educación
Licenciatura en Estadística - UBA

Habilidades
Python, pandas, SQL, Power BI, Excel

Idiomas
Español nativo
Inglés B2
"""

PROFILE_HTML = """
<html><body>
<h1 class="top-card-layout__title">Ana Pérez</h1>
<h2 class="top-card-layout__headline">Data Analyst</h2>
<h3 class="top-card-layout__first-subline"><span>Buenos Aires, Argentina</span></h3>
<section class="core-section-container summary">
  <div class="core-section-container__content">Acerca de Analista de datos. Python y SQL.</div>
</section>
<section class="experience">
  <ul>
    <li class="experience-item">
      <h3><span class="experience-item__title">Data Analyst</span></h3>
      <h4 class="experience-item__subtitle"><span>Empresa Real S.A.</span></h4>
      <p class="experience-item__meta-item"><span>2022 - actualidad</span></p>
      <div class="experience-item__description">Power BI, BigQuery y A/B testing.</div>
    </li>
  </ul>
</section>
<section class="education">
  <ul><li><h3>UBA</h3><span>Licenciatura en Estadística</span></li></ul>
</section>
</body></html>
"""

ANSWERS = {
    "email": "ana@example.com",
    "roles_objetivo": ["Data Scientist", "Analytics Engineer"],
    "remoto_excluyente": True,
    "relocation": True,
    "relocation_destinos": ["España", "Ciudad de México"],
    "ubicacion_actual": "Buenos Aires",
    "seniority": ["senior"],
    "industrias": ["fintech"],
    "evitar": ["ventas", "pasantía"],
    "empresas_evitar": ["Empresa X"],
    "aprendiendo": ["dbt"],
    "tipo_contrato": ["full-time"],
    "disponibilidad": "30 días",
    "viaje": "no",
    "salario_minimo": 3000,
    "moneda": "USD",
    "autorizacion_trabajo": "Argentina",
    "motivacion": "Quiero construir modelos que se usen en producto.",
}


def test_parse_cv_text_extracts_history_and_skills():
    draft = parse_cv_text(CV)
    assert draft["nombre"] == "Ana Pérez"
    assert draft["titulo_actual"] == "Data Analyst"
    assert draft["anios_experiencia"] == 4
    assert draft["contacto"]["email"] == "ana@example.com"
    assert "ana-perez" in draft["contacto"]["linkedin"]
    assert draft["ubicacion_actual"] == "Buenos Aires"
    assert [e["empresa"] for e in draft["experiencia"]] == ["Empresa Real S.A.", "Startup Deportiva"]
    assert "python" in [h.lower() for h in draft["habilidades"]]
    assert any("scikit-learn" in h for h in draft["habilidades"])
    assert any("UBA" in e for e in draft["educacion"])
    assert any("ingl" in i.lower() for i in draft["idiomas"])


def test_docx_roundtrip(tmp_path: Path):
    path = tmp_path / "cv.docx"
    document = Document()
    for line in CV.strip().splitlines():
        document.add_paragraph(line)
    document.save(path)
    from job_agent.cv_parser import parse_cv_file

    draft = parse_cv_file(path)
    assert draft["nombre"] == "Ana Pérez"
    assert draft["experiencia"]


def test_public_profile_html():
    draft = parse_public_profile(PROFILE_HTML, "https://www.linkedin.com/in/ana-perez/")
    assert draft["nombre"] == "Ana Pérez"
    assert draft["titulo_actual"] == "Data Analyst"
    assert "Buenos Aires" in draft["ubicacion_actual"]
    assert "Python" in draft["resumen"] or "python" in draft["resumen"].lower()
    assert draft["experiencia"][0]["empresa"] == "Empresa Real S.A."
    assert "2022" in draft["experiencia"][0]["periodo"]
    assert any("power bi" in h for h in draft["habilidades"])
    assert any("UBA" in e for e in draft["educacion"])


def test_export_zip(tmp_path: Path):
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Profile.csv",
            "First Name,Last Name,Headline,Summary,Geo Location\nAna,Pérez,Data Analyst,Resumen del perfil,Buenos Aires\n",
        )
        archive.writestr(
            "Positions.csv",
            "Company Name,Title,Description,Started On,Finished On\n"
            "Empresa Real S.A.,Data Analyst,SQL y Python,Jan 2022,\n",
        )
        archive.writestr("Skills.csv", "Name\nPython\nSQL\n")
        archive.writestr(
            "Education.csv",
            "School Name,Degree Name,Start Date,End Date\nUBA,Licenciatura en Estadística,2016,2020\n",
        )
        archive.writestr("Languages.csv", "Name,Proficiency\nInglés,Full professional\n")
    draft = parse_export_zip(path)
    assert draft["nombre"] == "Ana Pérez"
    assert draft["experiencia"][0]["periodo"].endswith("actualidad")
    assert "Python" in draft["habilidades"] and "SQL" in draft["habilidades"]
    assert any("Inglés" in i for i in draft["idiomas"])
    assert any("UBA" in e for e in draft["educacion"])


def test_conocer_builds_profile_and_ignores_example_biography(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    cv = home / "cv.txt"
    cv.write_text(CV, encoding="utf-8")
    # Un perfil de ejemplo preexistente no tiene que contaminar la experiencia real.
    (home / "perfil.yaml").write_text(
        "nombre: Persona Ejemplo\nhabilidades: [COBOL]\naspiraciones:\n  roles_objetivo: [Cajero]\n",
        encoding="utf-8",
    )
    answers = home / "respuestas.yaml"
    answers.write_text(yaml.safe_dump(ANSWERS, allow_unicode=True), encoding="utf-8")
    data = run_onboarding(cv_path=cv, answers_path=answers, profile_path=home / "perfil.yaml", output_fn=lambda *_: None)
    profile = Profile.load(home / "perfil.yaml")
    assert profile.nombre == "Ana Pérez"
    assert "COBOL" not in profile.habilidades
    assert "cobol" not in [h.lower() for h in profile.habilidades]
    assert profile.remoto_excluyente is True
    assert profile.modalidad == ["remoto"]
    assert profile.relocation is True
    assert profile.relocation_destinos == ["España", "Ciudad de México"]
    assert profile.salario_minimo == 3000
    assert profile.email == "ana@example.com"
    assert profile.umbral_email == 85
    assert profile.viaje == "no"
    assert "Empresa Real S.A." in {e["empresa"] for e in profile.experiencia}
    assert any(q.get("remoto") for q in profile.consultas)
    assert data["fuentes"]["cv"] == "cv.txt"
