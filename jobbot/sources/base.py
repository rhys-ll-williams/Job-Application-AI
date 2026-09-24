"""Shared types and helpers for job-board adapters."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from ..config import SearchConfig, SearchQuery
from ..forms.types import JobInfo


@dataclass
class Posting:
    source: str
    external_id: str
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    apply_kind: str = "unknown"  # easy_apply | external | unknown
    closed: bool = False

    def info(self) -> JobInfo:
        return JobInfo(self.title, self.company, self.location, self.description, self.url)


@dataclass
class ApplyTarget:
    """Where the application form lives after pressing Apply."""

    page: Page
    scope: str | None = None  # CSS selector limiting the form (e.g. LinkedIn's modal)
    kind: str = "external"


class Source(Protocol):
    name: str

    def ensure_ready(self, page: Page, notify: Callable[[str], None], wait_s: int) -> bool: ...

    def search(self, page: Page, query: SearchQuery, cfg: SearchConfig) -> Iterator[Posting]: ...

    def details(self, page: Page, posting: Posting) -> Posting: ...

    def begin_apply(self, page: Page, posting: Posting) -> ApplyTarget | None: ...


def first_text(page: Page, selectors: list[str], timeout: int = 1500) -> str:
    """Text of the first selector that exists - sites rename CSS classes often, so try several."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count():
                t = loc.inner_text(timeout=timeout).strip()
                if t:
                    return re.sub(r"\s+\n", "\n", t)
        except PWError:
            continue
    return ""


def hrefs(page: Page, css: str) -> list[str]:
    try:
        return page.eval_on_selector_all(css, "els => els.map(e => e.href || '')")
    except PWError:
        return []


def click_and_follow(page: Page, click: Callable[[], None], wait_ms: int = 9000) -> Page:
    """Click something that may open a new tab; return whichever page now holds the destination."""
    ctx = page.context
    before = set(ctx.pages)
    click()
    end = wait_ms
    while end > 0:
        page.wait_for_timeout(500)
        end -= 500
        new = [p for p in ctx.pages if p not in before]
        if new:
            new[-1].wait_for_load_state("domcontentloaded")
            return new[-1]
        if page.url and "linkedin.com/jobs" not in page.url and "indeed." not in page.url:
            break
    return page
