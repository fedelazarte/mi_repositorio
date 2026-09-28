from job_agent.sources.linkedin import extract_job_id, parse_job_detail, parse_search_results

SEARCH_HTML = """
<ul>
<li>
  <div class="base-card" data-entity-urn="urn:li:jobPosting:4012345678">
    <a class="base-card__full-link" href="https://ar.linkedin.com/jobs/view/data-scientist-at-acme-4012345678?refId=abc&trackingId=xyz">
      <span class="sr-only">Data Scientist</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">  Data   Scientist </h3>
      <h4 class="base-search-card__subtitle"><a>Acme Corp</a></h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">Buenos Aires, Argentina</span>
        <time class="job-search-card__listdate" datetime="2026-09-25">hace 3 días</time>
      </div>
    </div>
  </div>
</li>
<li>
  <div class="base-card" data-entity-urn="urn:li:jobPosting:4012345678"></div>
</li>
<li>
  <div class="base-card" data-entity-urn="urn:li:jobPosting:4099999999">
    <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/4099999999/?trk=x"></a>
    <h3 class="base-search-card__title">Analytics Engineer</h3>
    <h4 class="base-search-card__subtitle">Globant</h4>
    <span class="job-search-card__location">Argentina (Remote)</span>
  </div>
</li>
</ul>
"""

DETAIL_HTML = """
<section class="top-card-layout">
  <h2 class="top-card-layout__title">Data Scientist</h2>
  <a class="topcard__org-name-link">Acme Corp</a>
  <span class="topcard__flavor--bullet">Buenos Aires, Argentina</span>
  <span class="posted-time-ago__text">hace 3 días</span>
</section>
<div class="show-more-less-html__markup">
  <p>Buscamos <strong>Data Scientist</strong> con Python y SQL.</p>
  <ul><li>Trabajo remoto</li><li>Inglés B2</li></ul>
</div>
<ul class="description__job-criteria-list">
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Nivel de antigüedad</h3>
    <span class="description__job-criteria-text">Intermedio</span>
  </li>
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Tipo de empleo</h3>
    <span class="description__job-criteria-text">Jornada completa</span>
  </li>
</ul>
"""


def test_parse_search_results_dedupes_and_cleans():
    jobs = parse_search_results(SEARCH_HTML)
    assert [j.id for j in jobs] == ["4012345678", "4099999999"]
    first = jobs[0]
    assert first.title == "Data Scientist"
    assert first.company == "Acme Corp"
    assert first.location == "Buenos Aires, Argentina"
    assert first.posted_at == "2026-09-25"
    assert first.url == "https://ar.linkedin.com/jobs/view/data-scientist-at-acme-4012345678"
    assert jobs[1].posted_at is None


def test_parse_job_detail():
    detail = parse_job_detail(DETAIL_HTML, "4012345678")
    assert detail["title"] == "Data Scientist"
    assert detail["company"] == "Acme Corp"
    assert detail["location"] == "Buenos Aires, Argentina"
    assert detail["seniority"] == "Intermedio"
    assert detail["employment_type"] == "Jornada completa"
    assert "Python y SQL" in detail["description"]
    assert "Trabajo remoto" in detail["description"]


def test_extract_job_id():
    assert extract_job_id("https://www.linkedin.com/jobs/view/4012345678/") == "4012345678"
    assert extract_job_id("https://ar.linkedin.com/jobs/view/data-scientist-at-acme-4012345678?refId=x") == "4012345678"
    assert extract_job_id("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=data") == "4012345678"
    assert extract_job_id("4012345678") == "4012345678"
    assert extract_job_id("https://www.linkedin.com/in/alguien/") is None
