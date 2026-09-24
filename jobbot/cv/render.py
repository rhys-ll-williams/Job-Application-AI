"""Render CV / cover letter to ATS-friendly PDFs (single column, real text, no images).

Uses headless Chromium via the *same* Playwright instance as the job browser, because
page.pdf() only works headless while the job-site browser is usually headed.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from jinja2 import Environment, select_autoescape
from playwright.sync_api import Browser, Playwright

from ..config import Profile
from ..forms.types import JobInfo
from .cover_letter import CoverLetter
from .tailor import TailoredCV

_env = Environment(autoescape=select_autoescape(default=True))

_CSS = """
@page { size: A4; margin: 16mm 16mm; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 10pt; line-height: 1.38; color: #111; }
h1 { font-size: 20pt; margin: 0 0 2px; letter-spacing: .3px; }
.contact { color: #333; margin-bottom: 10px; font-size: 9.5pt; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #444; padding-bottom: 2px; margin: 14px 0 6px; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.role { font-weight: bold; } .meta { color: #333; white-space: nowrap; }
ul { margin: 3px 0 8px 18px; padding: 0; } li { margin-bottom: 2px; }
p { margin: 4px 0; } .skills b { font-weight: bold; }
"""

_CV_TPL = _env.from_string("""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>{{ cv.name }} - CV</title><style>{{ css }}</style></head><body>
<h1>{{ cv.name }}</h1><div class="contact">{{ cv.contact_line }}</div>
{% if cv.summary %}<h2>Profile</h2><p>{{ cv.summary }}</p>{% endif %}
{% if cv.skills %}<h2>Skills</h2><div class="skills">{% for g in cv.skills %}<p><b>{{ g.category }}:</b> {{ g.items | join(', ') }}</p>{% endfor %}</div>{% endif %}
{% if cv.experience %}<h2>Experience</h2>{% for j in cv.experience %}
<div class="row"><span class="role">{{ j.title }}, {{ j.company }}{% if j.location %}, {{ j.location }}{% endif %}</span><span class="meta">{{ j.start }} - {{ j.end }}</span></div>
<ul>{% for b in j.bullets %}<li>{{ b }}</li>{% endfor %}</ul>{% endfor %}{% endif %}
{% if cv.projects %}<h2>Projects</h2>{% for pr in cv.projects %}
<p><span class="role">{{ pr.name }}</span>{% if pr.tech %} ({{ pr.tech | join(', ') }}){% endif %}{% if pr.description %} - {{ pr.description }}{% endif %}</p>
{% if pr.bullets %}<ul>{% for b in pr.bullets %}<li>{{ b }}</li>{% endfor %}</ul>{% endif %}{% endfor %}{% endif %}
{% if cv.education %}<h2>Education</h2>{% for e in cv.education %}
<div class="row"><span class="role">{{ e.degree }}, {{ e.institution }}{% if e.grade %} - {{ e.grade }}{% endif %}</span><span class="meta">{{ e.start }}{% if e.end %} - {{ e.end }}{% endif %}</span></div>
{% if e.details %}<ul>{% for d in e.details %}<li>{{ d }}</li>{% endfor %}</ul>{% endif %}{% endfor %}{% endif %}
{% if cv.certifications %}<h2>Certifications</h2><ul>{% for c in cv.certifications %}<li>{{ c }}</li>{% endfor %}</ul>{% endif %}
{% if cv.languages %}<h2>Languages</h2><p>{{ cv.languages | join(', ') }}</p>{% endif %}
</body></html>""")

_CL_TPL = _env.from_string("""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>Cover letter - {{ name }}</title><style>{{ css }}
body { font-size: 11pt; line-height: 1.5; } .hdr { margin-bottom: 18px; } p { margin: 0 0 10px; }</style></head><body>
<div class="hdr"><b>{{ name }}</b><br>{{ contact }}<br>{{ today }}</div>
<p>Dear Hiring Manager,</p>{% for para in paragraphs %}<p>{{ para }}</p>{% endfor %}
<p>Yours faithfully,<br>{{ name }}</p></body></html>""")


def slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n] or "x"


def _to_pdf(pw: Playwright, html_doc: str, out: Path, browser: Browser | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    own = browser is None
    browser = browser or pw.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.set_content(html_doc, wait_until="load")
        page.pdf(path=str(out), format="A4", print_background=False, prefer_css_page_size=True)
        page.close()
    finally:
        if own:
            browser.close()
    return out


def render_cv(pw: Playwright, cv: TailoredCV, out: Path, browser: Browser | None = None) -> Path:
    return _to_pdf(pw, _CV_TPL.render(cv=cv, css=_CSS), out, browser)


def render_cover_letter(pw: Playwright, p: Profile, letter: CoverLetter, out: Path, browser: Browser | None = None) -> Path:
    from datetime import date
    doc = _CL_TPL.render(name=p.personal.full_name, contact=f"{p.personal.email} | {p.personal.phone}",
                         today=date.today().strftime("%d %B %Y").lstrip("0"), paragraphs=letter.paragraphs, css=_CSS)
    return _to_pdf(pw, doc, out, browser)


def application_dir(base: str | Path, job: JobInfo, job_id: int | None = None) -> Path:
    d = Path(base) / f"{job_id or 0:05d}-{slug(job.company, 24)}-{slug(job.title, 30)}"
    d.mkdir(parents=True, exist_ok=True)
    return d
