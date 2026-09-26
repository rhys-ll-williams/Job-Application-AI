"""LinkedIn Jobs adapter (Easy Apply + external apply).

NOTE: LinkedIn's terms prohibit automation and it changes its DOM often. Every site-specific
selector lives in this file; when something breaks, adjust the lists at the top.
The user logs in manually (`python -m jobbot login`); credentials are never handled here.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Iterator
from urllib.parse import quote_plus

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from ..config import SearchConfig, SearchQuery
from .base import ApplyTarget, Posting, click_and_follow, first_text, hrefs

TITLE = ["h1.t-24", ".job-details-jobs-unified-top-card__job-title h1", ".jobs-unified-top-card__job-title",
         ".top-card-layout__title", "h1"]
# Two different layouts in practice: the logged-in app (.job-details-...., .jobs-unified-....) and the public,
# logged-out job view (.topcard__...) that a pasted link usually opens to first. Both are tried.
COMPANY = [".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name",
           ".artdeco-entity-lockup__subtitle", ".topcard__org-name-link", "a[href*='/company/']"]
LOCATION = [".job-details-jobs-unified-top-card__primary-description-container", ".jobs-unified-top-card__bullet",
            ".artdeco-entity-lockup__caption", ".topcard__flavor--bullet"]
DESCRIPTION = ["#job-details", ".jobs-description__content", ".jobs-box__html-content", ".description__text", "article"]
MODAL = 'div[role="dialog"]'
_LOGIN_URL = re.compile(r"/login|/uas/|/checkpoint|authwall|/signup", re.I)


class LinkedIn:
    name = "linkedin"

    # ------------------------------------------------------------ session ---
    def ensure_ready(self, page: Page, notify: Callable[[str], None], wait_s: int) -> bool:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if not _LOGIN_URL.search(page.url):
            return True
        notify("\a[jobbot] LinkedIn: please log in in the browser window (I never type your password). Waiting...")
        end = time.time() + wait_s
        while time.time() < end:
            page.wait_for_timeout(2000)
            if not _LOGIN_URL.search(page.url):
                return True
        return False

    # ------------------------------------------------------------- search ---
    def _search_url(self, q: SearchQuery, cfg: SearchConfig, start: int) -> str:
        url = (f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(q.keywords)}&location={quote_plus(q.location)}"
               f"&f_TPR=r{cfg.posted_within_days * 86400}&sortBy=DD&start={start}")
        if cfg.easy_apply_only:
            url += "&f_AL=true"
        if cfg.remote_only:
            url += "&f_WT=2"
        return url

    def search(self, page: Page, query: SearchQuery, cfg: SearchConfig) -> Iterator[Posting]:
        seen: set[str] = set()
        for start in range(0, max(cfg.max_jobs_per_query, 1), 25):
            page.goto(self._search_url(query, cfg, start), wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            for _ in range(7):  # the list lazy-loads as you scroll its container
                try:
                    page.evaluate("() => { const el = document.querySelector('.jobs-search-results-list, .scaffold-layout__list');"
                                  " (el || document.scrollingElement).scrollBy(0, 900); }")
                except PWError:
                    break
                page.wait_for_timeout(600)
            ids = []
            for h in hrefs(page, "a[href*='/jobs/view/']"):
                m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d{6,})", h)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    ids.append(m.group(1))
            if not ids:
                break
            for jid in ids:
                yield Posting("linkedin", jid, f"https://www.linkedin.com/jobs/view/{jid}/")
                if len(seen) >= cfg.max_jobs_per_query:
                    return

    # ------------------------------------------------------------ details ---
    def details(self, page: Page, posting: Posting) -> Posting:
        page.goto(posting.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        body = first_text(page, ["main", "body"], 2500)[:3000].lower()
        if re.search(r"no longer accepting applications|job is no longer available|this job has expired", body):
            posting.closed = True
        posting.title = first_text(page, TITLE)
        posting.company = first_text(page, COMPANY)
        posting.location = first_text(page, LOCATION).split("\n")[0][:120]
        posting.description = first_text(page, DESCRIPTION, 3000)
        if page.get_by_role("button", name=re.compile(r"easy apply", re.I)).count() or page.locator("a[aria-label*='Easy Apply' i]").count():
            posting.apply_kind = "easy_apply"
        elif page.get_by_role("button", name=re.compile(r"^apply", re.I)).count() or page.get_by_role("link", name=re.compile(r"^apply", re.I)).count():
            posting.apply_kind = "external"
        else:
            posting.closed = True  # nothing to press (already applied, or closed)
        return posting

    # -------------------------------------------------------------- apply ---
    def begin_apply(self, page: Page, posting: Posting) -> ApplyTarget | None:
        if posting.apply_kind == "easy_apply":
            btn = page.get_by_role("button", name=re.compile(r"easy apply", re.I)).first
            if not btn.count():
                btn = page.locator("a[aria-label*='Easy Apply' i]").first
            btn.click()
            try:
                page.locator(MODAL).first.wait_for(state="visible", timeout=8000)
            except PWError:
                return None
            return ApplyTarget(page, scope=MODAL, kind="easy_apply")
        btn = page.get_by_role("button", name=re.compile(r"^apply", re.I)).first
        if not btn.count():
            btn = page.get_by_role("link", name=re.compile(r"^apply", re.I)).first
        dest = click_and_follow(page, lambda: btn.click())
        return ApplyTarget(dest, scope=None, kind="external")

    def after_success(self, page: Page) -> None:
        """Close LinkedIn's 'Application sent' dialog."""
        try:
            page.get_by_role("button", name=re.compile(r"^(done|dismiss)$", re.I)).first.click(timeout=2500)
        except PWError:
            pass
