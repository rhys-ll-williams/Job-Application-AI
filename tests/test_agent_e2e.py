"""End-to-end: real Chromium + real Ollama model against the mock ATS.

Run:  python -m pytest tests/test_agent_e2e.py -s        (needs `ollama serve` and qwen3:4b pulled)
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parent))
import mock_ats  # noqa: E402

from jobbot.config import ApplyConfig, LLMConfig, load_profile  # noqa: E402
from jobbot.forms.agent import FormAgent  # noqa: E402
from jobbot.forms.types import AnswerContext, Documents, JobInfo  # noqa: E402
from jobbot.llm import LLMError, OllamaLLM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JOB = JobInfo(title="Junior Developer", company="Acme Robotics", location="Manchester",
              description="Acme Robotics builds warehouse robots. You will write Python services, work with PostgreSQL, "
                          "and help improve testing. Hybrid working in Manchester.")


@pytest.fixture(scope="module")
def llm():
    client = OllamaLLM(LLMConfig())
    try:
        client.check()
    except LLMError as exc:
        pytest.skip(str(exc))
    return client


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    d = tmp_path_factory.mktemp("docs")
    for name in ("cv.pdf", "cover.pdf"):
        w = PdfWriter(); w.add_blank_page(width=595, height=842)
        with open(d / name, "wb") as fh:
            w.write(fh)
    return Documents(cv_path=str(d / "cv.pdf"), cover_path=str(d / "cover.pdf"))


@pytest.fixture(scope="module")
def server():
    srv, base = mock_ats.start()
    yield base
    srv.shutdown()


@pytest.mark.parametrize("flow,path", [("classic", "/classic/step/1"), ("spa", "/spa")])
def test_full_application(flow, path, server, llm, docs):
    mock_ats.SUBMISSIONS.clear(); mock_ats.COOKIE_CHOICES.clear()
    ctx = AnswerContext(profile=load_profile(ROOT / "profile.example.yaml"), job=JOB, docs=docs, llm=llm,
                        apply_cfg=ApplyConfig(auto_submit=True, handoff="skip"))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(server + path)
        result = FormAgent(page, ctx, describe=flow).run()
        browser.close()

    for row in result.log:
        print(f"  [{row['source']:<11}] {row['label'][:55]:<55} -> {str(row['value'])[:60]!r}")
    print(flow, result.status, result.note, "pages:", result.pages)

    assert result.status == "submitted", (result.status, result.note)
    assert len(mock_ats.SUBMISSIONS) == 1
    s = mock_ats.SUBMISSIONS[0]
    assert s["first_name"] == "Alex" and s["last_name"] == "Morgan"
    assert s["email"] == "alex.morgan@example.com"
    assert s["country"] == "United Kingdom"
    assert s["cv"] == "<file:cv.pdf>" and s["cover_letter"] == "<file:cover.pdf>"
    assert s["rtw"] == "yes" and s["spons"] == "no"
    assert s["notice"] == "1 month" and s["salary"] == "£30,001 - £35,000"
    assert s["how"] == "LinkedIn"
    assert "marketing" not in s  # marketing opt-in declined
    assert s["consent"] == "1"
    # UK diversity monitoring: every question answered 'prefer not to say'
    assert s["gender"] == "Prefer not to say"
    assert s["eth"] == "5" and s["dis"] == "p"
    assert s["religion"] == "I would rather not say" and s["orientation"] == "Prefer not to say"
    assert len(s["why"]) > 40  # LLM-written free text
    assert mock_ats.COOKIE_CHOICES == ["reject"]  # never 'accept all'
