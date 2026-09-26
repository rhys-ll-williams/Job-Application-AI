"""The manual, page-at-a-time filler: real Chromium + real Ollama against the mock ATS.

Simulates a human driving the browser: fill_once() on page 1, then WE click "Next"
(standing in for the user), then fill_once() again on page 2 - checking that the same
job/CV/cover-letter context carries over and nothing is clicked automatically.
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parent))
import mock_ats  # noqa: E402

from jobbot.config import ApplyConfig, LLMConfig, load_profile  # noqa: E402
from jobbot.forms.dom import snapshot  # noqa: E402
from jobbot.formfiller import active_page, fill_once, prepare_session, resolve_job, scrape_advert  # noqa: E402
from jobbot.llm import LLMError, OllamaLLM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class FakeCfg:
    output_dir: str
    apply: ApplyConfig

    def __init__(self, out):
        self.output_dir = str(out)
        self.apply = ApplyConfig()


@pytest.fixture(scope="module")
def llm():
    client = OllamaLLM(LLMConfig())
    try:
        client.check()
    except LLMError as exc:
        pytest.skip(str(exc))
    return client


@pytest.fixture(scope="module")
def server():
    srv, base = mock_ats.start()
    yield base
    srv.shutdown()


def test_manual_filler_never_clicks_and_keeps_context(server, llm, tmp_path):
    mock_ats.SUBMISSIONS.clear()
    profile = load_profile(ROOT / "profile.example.yaml")
    cfg = FakeCfg(tmp_path)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        job = prepare_session(cfg, profile, llm, pw, browser, "Junior Developer", "Acme Robotics",
                              "Acme Robotics builds warehouse robots. Python, FastAPI, PostgreSQL, Docker.",
                              "Manchester", notify=lambda *a: None)
        assert Path(job.ctx.docs.cv_path).exists() and Path(job.ctx.docs.cover_path).exists()

        page = browser.new_page()
        page.goto(server + "/classic/step/1")

        r1 = fill_once(page, job, notify=lambda *a: None)
        assert r1.filled_count >= 6
        # never advanced the page itself
        assert page.url.endswith("/classic/step/1")
        assert not r1.unsure  # every required field on step 1 is answerable from the profile

        # required field left genuinely blank (no CV path) must be reported, not guessed
        job.ctx.docs.cv_path = ""
        job.ctx.docs.cv_uploaded = False
        page2 = browser.new_page()
        page2.goto(server + "/classic/step/1")
        r_blocked = fill_once(page2, job, notify=lambda *a: None)
        assert any("cv" in f.label.lower() or "cv" in (f.name or "").lower() or f.kind == "file" for f, _ in r_blocked.unsure)
        page2.close()
        job.ctx.docs.cv_path = str(Path(tmp_path) / "cv.pdf")  # restore for the rest of the walk

        # simulate the human clicking "Save and continue" themselves
        page.get_by_role("button", name="Save and continue").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith("/classic/step/2")

        r2 = fill_once(page, job, notify=lambda *a: None)
        assert r2.filled_count >= 4
        snap = snapshot(page)
        assert next(f.value for f in snap.fields if "sponsorship" in f.label.lower()).lower().startswith("no")

        browser.close()
    assert mock_ats.SUBMISSIONS == []  # the filler must never submit anything


class FakeBrowserSession:
    def __init__(self, ctx):
        self.context = ctx

    def new_page(self):
        return self.context.new_page()


def test_active_page_prefers_most_recent_tab(server):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        p1 = context.new_page()
        p1.goto(server + "/classic/step/1")
        p2 = context.new_page()
        p2.goto(server + "/classic/step/2")
        assert active_page(FakeBrowserSession(context), notify=lambda *a: None) is p2
        p2.close()
        assert active_page(FakeBrowserSession(context), notify=lambda *a: None) is p1
        browser.close()


def test_scrape_advert_generic_fallback(server):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        info = scrape_advert(page, server + "/advert/generic")
        assert info.title == "Backend Python Developer - Acme Robotics"
        assert info.company == "Acme Robotics"
        assert "FastAPI" in info.description and "PostgreSQL" in info.description
        browser.close()


def test_resolve_job_from_advert_url_needs_no_prompt(server):
    """A good scrape should need no input() / stdin at all - if it did, this test would hang."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        title, company, description, location = resolve_job(
            FakeBrowserSession(context), title="", company="", location="", description="",
            advert_url=server + "/advert/generic", notify=lambda *a: None)
        assert title == "Backend Python Developer - Acme Robotics"
        assert company == "Acme Robotics"
        assert "FastAPI" in description
        browser.close()


def test_resolve_job_falls_back_to_paste_when_scrape_is_too_thin(server, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("A real pasted job description, long enough to pass the check.\n"))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        # /cookie returns a two-byte "ok" body with no <title> - too thin to use as a description
        title, company, description, location = resolve_job(
            FakeBrowserSession(context), title="Junior Developer", company="Acme Robotics", location="",
            description="", advert_url=server + "/cookie?c=reject", notify=lambda *a: None)
        assert description.startswith("A real pasted job description")
        browser.close()
