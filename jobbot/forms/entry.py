"""Get from a company job page to the actual application form.

Careers pages usually show the description first with an 'Apply' button; some ATSs
(Workday, iCIMS...) add an interstitial ('Apply manually' / 'Continue as guest').
We click through up to a few hops, following new tabs, until real form fields appear.
"""
from __future__ import annotations

import logging
import re

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from ..sources.base import click_and_follow
from .dom import snapshot

log = logging.getLogger(__name__)
ENTRY_RE = re.compile(
    r"^(apply( now| online| here| today| for this (job|role|position|vacancy|opportunity))?|start( your)? application|apply manually|"
    r"apply without (an )?(account|resume|cv)|continue (as (a )?guest|without (an )?account)|i'?m interested|begin( your)? application|"
    r"apply to this job|submit (your )?application|express interest)\s*[>»→]*$", re.I)


def _form_fields(page: Page) -> int:
    try:
        snap = snapshot(page)
    except PWError:
        return 0
    return sum(1 for f in snap.fields if f.kind != "password" and (f.label or f.name) and f.kind != "file") + sum(
        1 for f in snap.fields if f.kind == "file")


def reach_form(page: Page, max_hops: int = 3) -> Page:
    for _ in range(max_hops):
        page.wait_for_timeout(1200)
        if _form_fields(page) >= 3:
            return page
        clicked = False
        for role in ("button", "link"):
            cands = page.get_by_role(role, name=ENTRY_RE)
            n = cands.count()
            for i in range(min(n, 4)):
                el = cands.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    log.info("entry: clicking %r", el.inner_text(timeout=800).strip()[:40])
                    page = click_and_follow(page, lambda el=el: el.click(timeout=4000))
                    page.wait_for_load_state("domcontentloaded")
                    clicked = True
                    break
                except PWError:
                    continue
            if clicked:
                break
        if not clicked:
            break
    return page
