"""A simple, manual, page-at-a-time form filler.

Unlike `jobbot.pipeline` (search -> score -> apply autonomously across many jobs),
this is for one job at a time: you paste in the advert, it tailors a CV and cover
letter, then you drive the browser yourself - log in, open the application, click
Apply, solve any CAPTCHA. Whenever you land on a page (including later pages of a
multi-step form), you press Enter and it fills in whatever it can on that page from
your profile, the job advert, and qwen3:4b, using the SAME job context throughout so
you never re-paste the advert or lose track of which application you're doing. It
never clicks Next or Submit - you stay in control of navigation and submission.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, Playwright

from .browser import BrowserSession
from .config import Config, Profile
from .cv.cover_letter import write_cover_letter
from .cv.render import application_dir, render_cover_letter, render_cv
from .cv.tailor import tailor
from .forms.guards import detect_blocker
from .forms.pagefill import PageFillResult, fill_page
from .forms.types import AnswerContext, Documents, JobInfo
from .llm import LLMClient

_BLOCKER_NOTE = {"captcha": "this page looks like a CAPTCHA / verification check",
                "login": "this page looks like a login wall"}


@dataclass
class JobSession:
    ctx: AnswerContext
    out_dir: Path


def prepare_session(cfg: Config, profile: Profile, llm: LLMClient | None, pw: Playwright,
                    pdf_browser, title: str, company: str, description: str, location: str = "",
                    notify: Callable[[str], None] = print) -> JobSession:
    """Tailor the CV and cover letter for this job and return a ready-to-use answer context."""
    info = JobInfo(title=title, company=company, location=location, description=description)
    out = application_dir(cfg.output_dir, info)
    notify(f"[jobbot] tailoring CV and cover letter for {title} at {company}...")
    cv = tailor(profile, info, llm, rewrite_bullets=cfg.apply.rewrite_bullets)
    docs = Documents(cv_path=profile.master_cv_pdf)
    if cfg.apply.tailor_cv:
        docs.cv_path = str(render_cv(pw, cv, out / "cv.pdf", pdf_browser))
    if cfg.apply.write_cover_letter:
        letter = write_cover_letter(profile, info, cv, llm)
        docs.cover_text = letter.text(profile, info)
        docs.cover_path = str(render_cover_letter(pw, profile, letter, out / "cover_letter.pdf", pdf_browser))
        (out / "cover_letter.txt").write_text(docs.cover_text, encoding="utf-8")
    notify(f"[jobbot] documents ready in {out}")
    ctx = AnswerContext(profile=profile, job=info, docs=docs, llm=llm, apply_cfg=cfg.apply, db=None)
    return JobSession(ctx, out)


def active_page(browser: BrowserSession, notify: Callable[[str], None] = print) -> Page | None:
    """The tab to operate on: the most recently opened one (a click on 'Apply' often opens a new tab)."""
    pages = [p for p in browser.context.pages if not p.is_closed()]
    if not pages:
        return None
    if len(pages) > 1:
        notify(f"[jobbot] {len(pages)} tabs open - using the most recent: {pages[-1].url}")
    return pages[-1]


def fill_once(page: Page, job: JobSession, notify: Callable[[str], None] = print) -> PageFillResult:
    """Fill whatever is on the current page right now. Never clicks anything."""
    blocker = detect_blocker(page)
    if blocker:
        notify(f"[jobbot] {_BLOCKER_NOTE.get(blocker, blocker)} - that's for you to handle; I won't try to get past it.")
    result = fill_page(page, job.ctx, skip_if_unsure=True)
    notify(f"[jobbot] filled {result.filled_count}/{len(result.fields)} fields on this page")
    for r in result.fields:
        if r.filled:
            notify(f"  [{r.source:<8}] {r.label[:55]:<55} -> {str(r.value)[:45]!r}")
    if result.unsure:
        notify("[jobbot] needs your attention (required, could not answer):")
        for f, why in result.unsure:
            notify(f"   - {(f.label or f.name or f.kind)[:70]}: {why}")
    return result


def run_interactive(cfg: Config, profile: Profile, llm: LLMClient | None, *, title: str, company: str,
                    description: str, location: str = "", url: str = "",
                    notify: Callable[[str], None] = print) -> None:
    with BrowserSession(cfg.browser) as bs:
        job = prepare_session(cfg, profile, llm, bs.pw, bs.pdf_browser, title, company, description, location, notify)
        page = bs.new_page()
        if url:
            page.goto(url, wait_until="domcontentloaded")
        else:
            notify("[jobbot] browser open - log in and navigate to the application yourself, then come back here.")

        notify("\n[jobbot] on each page of the form:\n"
              "  Enter = fill this page   u <url> = open a url   n = new job   q = quit\n")
        while True:
            try:
                cmd = input("> ").strip()
            except EOFError:
                break
            if cmd in {"q", "quit", "exit"}:
                break
            if cmd == "n":
                title = input("job title: ").strip()
                company = input("company: ").strip()
                notify("paste the job description, then press Ctrl+Z then Enter (Windows) or Ctrl+D (Unix):")
                description = sys.stdin.read()
                location = input("location (optional): ").strip()
                job = prepare_session(cfg, profile, llm, bs.pw, bs.pdf_browser, title, company, description, location, notify)
                continue
            if cmd.startswith("u "):
                target = active_page(bs, notify) or bs.new_page()
                target.goto(cmd[2:].strip(), wait_until="domcontentloaded")
                page = target
                continue
            page = active_page(bs, notify) or page
            fill_once(page, job, notify)
    notify("[jobbot] bye - remember to check every answer before you submit anything.")
