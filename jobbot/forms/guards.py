"""Page-level judgement: blockers (CAPTCHA / login walls), success, and navigation buttons.

The bot never tries to defeat a CAPTCHA. It reports the blocker so the caller can
hand the browser to the human or skip the job.
"""
from __future__ import annotations

import re

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from .dom import Button

_CAPTCHA_FRAME = re.compile(r"recaptcha|hcaptcha|turnstile|captcha|challenges\.cloudflare|arkoselabs|funcaptcha", re.I)
_CAPTCHA_TEXT = re.compile(r"verify (that )?you are (a )?human|are you a (robot|human)|checking your browser|just a moment\.\.\.|"
                           r"security check|press (and|&) hold|complete the (captcha|security)|unusual traffic", re.I)
_SUCCESS = re.compile(
    r"application (has been |was |is )?(successfully )?(submitted|received|sent|complete)|"
    r"thank(s| you) for (applying|your (application|interest|submission))|"
    r"successfully (applied|submitted)|we('ve| have) received your application|"
    r"your application (has been|was) (sent|submitted|received)|application sent|"
    r"you('ve| have) (successfully )?applied|application complete", re.I)
_LOGIN = re.compile(r"sign in|log ?in|create (an )?account|register|sign up", re.I)

SUBMIT_RE = re.compile(r"submit|send application|finish|complete application|confirm (and|&) (apply|send)|^apply( now)?$|^send$", re.I)
NEXT_RE = re.compile(r"^(next|continue|proceed|save (and|&) (continue|next)|review( (your )?application)?|go to (the )?next|"
                     r"next step|continue to .+|continue application|save (and|&) proceed|next page)\b", re.I)
IGNORE_RE = re.compile(r"back|previous|cancel|discard|close|dismiss|save for later|save draft|sign in|log ?in|log ?out|share|upload|"
                       r"\badd\b|remove|edit|delete|choose file|attach|browse|search|clear|reset|autofill|manage|help|privacy|cookie|"
                       r"accept all|reject|decline|settings|menu|sign up|create account|register|apply with|use my|linkedin|indeed|"
                       r"google|facebook|apple|more|show|hide|preview|download|print|english|language|translate", re.I)


def detect_blocker(page: Page) -> str | None:
    """Return 'captcha', 'login' or None."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        for fr in page.frames[1:]:
            url = fr.url or ""
            if not _CAPTCHA_FRAME.search(url):
                continue
            if re.search(r"[?&]size=invisible", url):
                continue  # reCAPTCHA/hCaptcha v3-style badge: scores silently, nothing for a human to do
            el = fr.frame_element()
            box = el.bounding_box()
            on_screen = bool(box) and 0 <= box["x"] < vp["width"] and 0 <= box["y"] < vp["height"] + 4000
            if on_screen and box["width"] >= 200 and box["height"] >= 50 and el.is_visible():
                return "captcha"
        text = page.inner_text("body", timeout=3000)[:4000]
        if _CAPTCHA_TEXT.search(text):
            return "captcha"
        if page.locator("input[type=password]:visible").count() and _LOGIN.search(text):
            return "login"
    except PWError:
        return None
    return None


def looks_successful(page: Page, scope: str | None = None) -> bool:
    try:
        target = page.locator(scope).first if scope and page.locator(scope).count() else page.locator("body")
        text = target.inner_text(timeout=3000)[:6000]
    except PWError:
        return False
    return bool(_SUCCESS.search(text))


def classify_button(b: Button) -> str:
    """'submit' | 'next' | 'ignore'."""
    t = re.sub(r"\s+", " ", b.label).strip()
    if not t or len(t) > 60:
        return "ignore"
    if re.search(r"(with|using|via) (google|linkedin|facebook|apple|indeed|microsoft|github)|apply with", t, re.I):
        return "ignore"  # social sign-in, not form navigation
    if re.search(r"submit", t, re.I) and not re.search(r"save|draft|later", t, re.I):
        return "submit"
    if IGNORE_RE.search(t) and not NEXT_RE.search(t):
        return "ignore"
    if NEXT_RE.search(t):
        return "next"
    if SUBMIT_RE.search(t):
        return "submit"
    return "ignore"


def pick_nav_button(buttons: list[Button]) -> tuple[Button | None, str]:
    """Pick the button that advances the form. Prefers next over a lone 'apply', submit over next only if no next."""
    scored: list[tuple[int, Button, str]] = []
    for i, b in enumerate(buttons):
        kind = classify_button(b)
        if kind == "ignore":
            continue
        rank = (3 if kind == "next" else 2) + (1 if b.primary or b.type == "submit" else 0)
        scored.append((rank * 100 + i, b, kind))  # later in DOM wins ties (primary action sits last)
    if not scored:
        return None, "ignore"
    _, b, kind = max(scored, key=lambda s: s[0])
    return b, kind
