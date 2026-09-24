"""Indeed UK adapter (Indeed Apply + apply-on-company-site).

Indeed sits behind Cloudflare and shows verification pages; the form agent's blocker detection
hands those to you rather than trying to get past them. All Indeed-specific selectors live here.
"""
from __future__ import annotations

import re
from typing import Callable, Iterator
from urllib.parse import quote_plus

from playwright.sync_api import Page

from ..config import SearchConfig, SearchQuery
from ..forms.guards import detect_blocker
from .base import ApplyTarget, Posting, click_and_follow, first_text, hrefs

BASE = "https://uk.indeed.com"
TITLE = ["h1[data-testid='jobsearch-JobInfoHeader-title']", "h1.jobsearch-JobInfoHeader-title", "h1"]
COMPANY = ["[data-testid='inlineHeader-companyName']", "[data-company-name='true']", "div[data-testid='jobsearch-CompanyInfoContainer'] a"]
LOCATION = ["[data-testid='inlineHeader-companyLocation']", "[data-testid='job-location']", "[data-testid='jobsearch-JobInfoHeader-companyLocation']"]
DESCRIPTION = ["#jobDescriptionText", "[data-testid='jobsearch-JobComponent-description']", "article"]
INDEED_APPLY = "#indeedApplyButton, button[id*='indeedApply' i], button[aria-label*='Apply now' i]"
COMPANY_SITE = "a:has-text('Apply on company site'), button:has-text('Apply on company site'), #applyButtonLinkContainer a"


class Indeed:
    name = "indeed"

    def ensure_ready(self, page: Page, notify: Callable[[str], None], wait_s: int) -> bool:
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if detect_blocker(page) == "captcha":
            notify("\a[jobbot] Indeed is showing a verification page - please complete it in the browser window. Waiting...")
            for _ in range(wait_s // 2):
                page.wait_for_timeout(2000)
                if detect_blocker(page) is None:
                    return True
            return False
        return True  # searching needs no login; applying works best when you are signed in (run `login`)

    def _search_url(self, q: SearchQuery, cfg: SearchConfig, start: int) -> str:
        url = f"{BASE}/jobs?q={quote_plus(q.keywords)}&l={quote_plus(q.location)}&fromage={cfg.posted_within_days}&sort=date&start={start}"
        if cfg.remote_only:
            url += "&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11"
        return url

    def search(self, page: Page, query: SearchQuery, cfg: SearchConfig) -> Iterator[Posting]:
        seen: set[str] = set()
        for start in range(0, max(cfg.max_jobs_per_query, 1), 10):
            page.goto(self._search_url(query, cfg, start), wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            if detect_blocker(page):
                return  # caller handles the hand-off on the next navigation
            keys = page.eval_on_selector_all("a[data-jk]", "els => els.map(e => e.getAttribute('data-jk'))")
            keys += [m.group(1) for h in hrefs(page, "a[href*='jk=']") if (m := re.search(r"[?&]jk=([0-9a-f]{8,})", h))]
            fresh = [k for k in dict.fromkeys(keys) if k and k not in seen]
            if not fresh:
                break
            for jk in fresh:
                seen.add(jk)
                yield Posting("indeed", jk, f"{BASE}/viewjob?jk={jk}")
                if len(seen) >= cfg.max_jobs_per_query:
                    return

    def details(self, page: Page, posting: Posting) -> Posting:
        page.goto(posting.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        posting.title = first_text(page, TITLE)
        posting.company = first_text(page, COMPANY)
        posting.location = first_text(page, LOCATION).split("\n")[0][:120]
        posting.description = first_text(page, DESCRIPTION, 3000)
        if page.locator(INDEED_APPLY).count():
            posting.apply_kind = "easy_apply"
        elif page.locator(COMPANY_SITE).count():
            posting.apply_kind = "external"
        else:
            posting.closed = True  # no apply button: expired, or already applied
        return posting

    def begin_apply(self, page: Page, posting: Posting) -> ApplyTarget | None:
        sel = INDEED_APPLY if posting.apply_kind == "easy_apply" else COMPANY_SITE
        btn = page.locator(sel).first
        dest = click_and_follow(page, lambda: btn.click())
        return ApplyTarget(dest, scope=None, kind=posting.apply_kind)

    def after_success(self, page: Page) -> None:
        pass
