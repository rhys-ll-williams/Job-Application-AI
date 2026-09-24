"""CV tailoring + PDF rendering (real Chromium, real Qwen; skips LLM parts if Ollama is unavailable)."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from jobbot.config import LLMConfig, load_profile
from jobbot.cv.cover_letter import write_cover_letter
from jobbot.cv.render import render_cover_letter, render_cv
from jobbot.cv.tailor import tailor
from jobbot.forms.types import JobInfo
from jobbot.llm import LLMError, OllamaLLM

ROOT = Path(__file__).resolve().parent.parent
JOB = JobInfo(title="Backend Python Developer", company="Acme Robotics", location="Manchester",
              description="We are hiring a Backend Python Developer. You will build FastAPI services, write SQL against PostgreSQL, "
                          "and containerise apps with Docker. Experience with pytest and CI is a plus. Kubernetes exposure welcome. "
                          "Hybrid in Manchester.")


@pytest.fixture(scope="module")
def llm():
    c = OllamaLLM(LLMConfig())
    try:
        c.check()
    except LLMError as exc:
        pytest.skip(str(exc))
    return c


def test_tailor_without_llm_reorders_and_never_invents():
    p = load_profile(ROOT / "profile.example.yaml")
    cv = tailor(p, JOB, llm=None)
    assert cv.matched_keywords and {"FastAPI", "PostgreSQL", "Docker", "pytest"} <= set(cv.matched_keywords)
    langs = next(g for g in cv.skills if g.category == "Frameworks & tools")
    assert langs.items[0] in {"FastAPI", "Docker", "pytest", "PostgreSQL"}  # matched skills first
    assert sorted(langs.items) == sorted(next(g for g in p.skills if g.category == "Frameworks & tools").items)  # none added/removed
    original = {b for j in p.experience for b in j.bullets}
    assert all(b in original for j in cv.experience for b in j.bullets)  # only real bullets
    assert cv.summary == p.summary.strip()


def test_pdf_and_llm_outputs(tmp_path, llm):
    p = load_profile(ROOT / "profile.example.yaml")
    cv = tailor(p, JOB, llm)
    letter = write_cover_letter(p, JOB, cv, llm)
    print("\nSUMMARY:", cv.summary, "\nMISSING:", cv.missing_keywords, "\nLETTER (llm=%s):" % letter.from_llm, *letter.paragraphs, sep="\n")
    with sync_playwright() as pw:
        cv_pdf = render_cv(pw, cv, tmp_path / "cv.pdf")
        cl_pdf = render_cover_letter(pw, p, letter, tmp_path / "cl.pdf")
    cv_text = "".join(pg.extract_text() for pg in PdfReader(cv_pdf).pages)
    assert "Alex Morgan" in cv_text and "FastAPI" in cv_text and "EXPERIENCE" in cv_text.upper()
    assert "Kubernetes" not in cv_text  # posting skill the candidate lacks must not appear
    assert len(PdfReader(cv_pdf).pages) == 1
    cl_text = "".join(pg.extract_text() for pg in PdfReader(cl_pdf).pages)
    assert "Dear Hiring Manager" in cl_text and "Acme Robotics" in cl_text
    assert "Kubernetes" not in cl_text
