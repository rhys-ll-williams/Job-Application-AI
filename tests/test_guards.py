"""Blocker detection must flag real challenges but ignore invisible reCAPTCHA badges."""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import mock_ats  # noqa: E402

from jobbot.forms.guards import classify_button, detect_blocker, pick_nav_button  # noqa: E402
from jobbot.forms.dom import Button  # noqa: E402


@pytest.fixture(scope="module")
def page_and_base():
    srv, base = mock_ats.start()
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        yield b.new_page(viewport={"width": 1200, "height": 800}), base
        b.close()
    srv.shutdown()


@pytest.mark.parametrize("path,expected", [
    ("/captcha/invisible", None),
    ("/captcha/visible", "captcha"),
    ("/captcha/cloudflare", "captcha"),
    ("/classic/step/1", None),
])
def test_detect_blocker(page_and_base, path, expected):
    page, base = page_and_base
    page.goto(base + path)
    page.wait_for_timeout(600)
    assert detect_blocker(page) == expected


def B(text, **kw):
    return Button(id=1, text=text, aria="", type=kw.get("type", ""), primary=kw.get("primary", False), frame=None)


def test_button_classification():
    assert classify_button(B("Save and continue")) == "next"
    assert classify_button(B("Next")) == "next"
    assert classify_button(B("Review")) == "next"
    assert classify_button(B("Submit application")) == "submit"
    assert classify_button(B("Submit your application")) == "submit"
    for ignored in ["Back", "Save for later", "Cancel", "Continue with Google", "Apply with LinkedIn", "Sign in", "Attach", "Dropbox", "Toggle flyout"]:
        assert classify_button(B(ignored)) == "ignore", ignored
    btn, kind = pick_nav_button([B("Back"), B("Save for later"), B("Save and continue", primary=True)])
    assert kind == "next" and btn.text == "Save and continue"
