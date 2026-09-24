"""Multi-page application form agent.

Loop per page:  detect blockers -> snapshot fields -> fill -> pick Next/Submit -> click -> wait for change.
It works the same on LinkedIn's Easy Apply modal, Indeed Apply, and arbitrary company/ATS sites,
because it reasons about the live DOM rather than site-specific selectors.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from .answers import answer_field
from .dom import Field, Snapshot, fill, read_combobox_options, snapshot
from .guards import detect_blocker, looks_successful, pick_nav_button
from .text import norm
from .types import Answer, AnswerContext

log = logging.getLogger(__name__)
TEXTLIKE = {"text", "email", "tel", "number", "url", "textarea"}
_COOKIE_REJECT = re.compile(r"^(reject( all| non-?essential)?|decline( all)?|(use )?(only )?(strictly )?(necessary|essential)( cookies)?( only)?|"
                            r"deny|refuse|no thanks|got it|dismiss|close)$", re.I)


@dataclass
class ApplyResult:
    status: str  # submitted | unconfirmed | dry_run | declined | needs_human | failed
    note: str = ""
    pages: int = 0
    log: list[dict] = field(default_factory=list)


def _default_confirm(prompt: str) -> bool:
    import sys
    if not sys.stdin or not sys.stdin.isatty():
        return False
    return input(f"\a{prompt} Submit? [y/N] ").strip().lower() in {"y", "yes"}


class FormAgent:
    def __init__(self, page: Page, ctx: AnswerContext, *, scope: str | None = None,
                 confirm: Callable[[str], bool] | None = None, notify: Callable[[str], None] = print,
                 describe: str = "this application", max_seconds: int = 900):
        self.page, self.ctx, self.cfg = page, ctx, ctx.apply_cfg
        self.scope, self.notify, self.describe = scope, notify, describe
        self.confirm = confirm or _default_confirm
        self.deadline = time.time() + max_seconds
        self.log: list[dict] = []
        self.pages = 0

    # ------------------------------------------------------------ helpers ---
    def _result(self, status: str, note: str = "") -> ApplyResult:
        return ApplyResult(status, note, self.pages, self.log)

    def _pause(self, lo: float = 0.15, hi: float = 0.5) -> None:
        self.page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))

    def _settle(self) -> None:
        for state, t in (("domcontentloaded", 6000), ("networkidle", 2500)):
            try:
                self.page.wait_for_load_state(state, timeout=t)
            except PWError:
                pass
        self.page.wait_for_timeout(400)

    def _record(self, f: Field, a: Answer, filled: bool) -> None:
        v = a.value if not isinstance(a.value, str) else a.value[:160]
        self.log.append({"label": f.label, "kind": f.kind, "required": f.required, "value": v,
                         "source": a.source, "confident": a.confident, "filled": filled, "note": a.note})

    def dismiss_cookies(self) -> None:
        """Privacy-first: reject/necessary-only, never 'accept all'."""
        try:
            for b in self.page.locator("button:visible, a[role=button]:visible").all()[:60]:
                txt = (b.inner_text(timeout=300) or "").strip()
                if _COOKIE_REJECT.match(txt) and b.evaluate("e => !!e.closest('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],[id*=onetrust i],[class*=banner i]')"):
                    b.click(timeout=1500)
                    self.page.wait_for_timeout(400)
                    return
        except PWError:
            pass

    def _handoff(self, reason: str, done: Callable[[], bool]) -> bool:
        """Hand the browser to the human and wait for them to clear the problem. Never bypasses it."""
        if self.cfg.handoff != "wait":
            return False
        limit = min(self.cfg.handoff_timeout_s, max(0, self.deadline - time.time()))
        self.notify(f"\a[jobbot] Needs you: {reason} ({self.describe}). Sort it in the browser window - "
                    f"I'll carry on automatically (waiting up to {int(limit)}s).")
        end = time.time() + limit
        while time.time() < end:
            try:
                if done():
                    return True
            except PWError:
                pass
            self.page.wait_for_timeout(1500)
        return False

    # ------------------------------------------------------------ filling ---
    @staticmethod
    def _variant(f: Field, value, retry: int):
        """Alternate formats to try when the site rejects the first attempt."""
        if not isinstance(value, str) or retry == 0:
            return value
        if f.kind == "tel":
            digits = re.sub(r"\D", "", value)
            national = digits.lstrip("0")
            return ["", f"+44{national}", f"0{national}"][min(retry, 2)]
        if f.kind == "date" and retry >= 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            y, m, d = value.split("-")
            return f"{d}/{m}/{y}"
        return value

    def _handle(self, f: Field, retry: int, filled: set[str], unsure: list[tuple[Field, str]]) -> bool:
        """Fill one field. Returns True if something was written."""
        sig = f.sig
        force = retry > 0 and f.invalid
        if f.has_value and not force:
            if f.kind in TEXTLIKE:
                a = answer_field(f, self.ctx, allow_llm=False)
                if not (a.usable and a.source in {"profile", "bank"} and norm(str(a.value)) != norm(f.value)):
                    filled.add(sig)
                    return False
                # site pre-filled something different from our profile (e.g. old phone number): correct it
            else:
                filled.add(sig)
                return False

        options = None
        if f.kind == "combobox":
            read = read_combobox_options(self.page, f)
            # Long/virtualised lists (countries, phone codes) can't be validated up front: for those we type the
            # answer and pick from the filtered results instead. Short lists (yes/no, notice period) are validated.
            options = read if 0 < len(read) <= 12 else None
        a = answer_field(f, self.ctx, options=options, allow_llm=f.required)
        filled.add(sig)

        if not a.usable:
            self._record(f, a, False)
            if f.required:
                unsure.append((f, a.note or "no answer available"))
            return False
        if not a.confident and f.required and self.cfg.skip_if_unsure:
            self._record(f, a, False)
            unsure.append((f, a.note or "low-confidence answer"))
            return False
        if not a.confident and not f.required:
            self._record(f, a, False)
            return False

        value = self._variant(f, a.value, retry)
        ok = fill(self.page, f, value)
        if ok and f.kind == "file" and value == self.ctx.docs.cv_path:
            self.ctx.docs.cv_uploaded = True
        self._record(f, a, ok)
        if not ok and f.required:
            unsure.append((f, "widget rejected the value"))
        if ok:
            self._pause()
        return ok

    def _fill_page(self, snap: Snapshot, retry: int) -> tuple[Snapshot, list[tuple[Field, str]]]:
        filled: set[str] = set()
        unsure: list[tuple[Field, str]] = []
        cur = snap
        for _ in range(6):  # fixed point: conditional questions appear as earlier ones are answered
            todo = sorted((f for f in cur.fields if f.sig not in filled), key=lambda f: f.kind != "file")
            if not todo:
                break
            progressed = False
            for f in todo:
                try:
                    wrote = self._handle(f, retry, filled, unsure)
                except PWError as exc:
                    log.warning("field %r failed: %s", f.label, str(exc).splitlines()[0])
                    filled.add(f.sig)
                    wrote = False
                progressed |= wrote
                if wrote and f.kind == "file":
                    self.page.wait_for_timeout(1800)  # ATS resume-parsing may re-render and prefill the form
                    break
            if not progressed:
                break
            cur = snapshot(self.page, self.scope)
        return cur, unsure

    # --------------------------------------------------------- navigation ---
    def _click(self, btn) -> bool:
        loc = btn.locator()
        for kwargs in ({}, {"force": True}):
            try:
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=4000, **kwargs)
                return True
            except PWError:
                continue
        try:
            loc.evaluate("e => e.click()")
            return True
        except PWError:
            return False

    def _wait_change(self, before_url: str, before_fp: str, timeout_s: float = 10.0) -> None:
        end = time.time() + timeout_s
        while time.time() < end:
            self.page.wait_for_timeout(600)
            if self.page.url != before_url or detect_blocker(self.page) or looks_successful(self.page, self.scope):
                return
            s = snapshot(self.page, self.scope)
            if s.fingerprint != before_fp or s.errors:
                return

    # ---------------------------------------------------------------- run ---
    def run(self) -> ApplyResult:
        self.dismiss_cookies()
        attempts: dict[str, int] = {}
        empty_polls = 0
        while time.time() < self.deadline and self.pages <= self.cfg.max_pages_per_application:
            self._settle()

            blocker = detect_blocker(self.page)
            if blocker:
                if not self._handoff(f"{blocker} check", lambda: detect_blocker(self.page) is None):
                    return self._result("needs_human", f"{blocker} wall")
                continue
            if looks_successful(self.page, self.scope):
                return self._result("submitted", "confirmation message seen")

            snap = snapshot(self.page, self.scope)
            if not snap.fields and not snap.buttons:
                empty_polls += 1
                if empty_polls > 4:
                    return self._result("failed", "page has no form or buttons")
                self.page.wait_for_timeout(1500)
                continue
            empty_polls = 0

            fp = snap.fingerprint
            attempts[fp] = attempts.get(fp, 0) + 1
            if attempts[fp] > 3:
                return self._result("failed", "stuck on a page: " + ("; ".join(snap.errors[:3]) or "no visible error"))

            snap, unsure = self._fill_page(snap, attempts[fp] - 1)
            if unsure:
                names = "; ".join(f"{f.label or f.name or f.kind} ({why})" for f, why in unsure[:6])
                sigs = {f.sig for f, _ in unsure}

                def resolved() -> bool:
                    now = snapshot(self.page, self.scope)
                    return all(x.has_value for x in now.fields if x.sig in sigs)

                if self.cfg.skip_if_unsure and not self._handoff(f"can't answer required question(s): {names}", resolved):
                    return self._result("needs_human", f"unsure about required fields: {names}")
                snap = snapshot(self.page, self.scope)

            btn, kind = pick_nav_button(snap.buttons)
            if btn is None:
                if looks_successful(self.page, self.scope):
                    return self._result("submitted", "confirmation message seen")
                return self._result("failed", "no Next/Submit button found")

            if kind == "submit":
                if self.cfg.dry_run:
                    return self._result("dry_run", "filled every page; final submit not pressed (dry_run)")
                if not self.cfg.auto_submit and not self.confirm(f"Ready to submit {self.describe}. Check the browser window."):
                    return self._result("declined", "you chose not to submit")
                before_url, before_fp = self.page.url, snap.fingerprint
                if not self._click(btn):
                    return self._result("failed", f"could not click {btn.label!r}")
                self._wait_change(before_url, before_fp, 15)
                if detect_blocker(self.page):
                    if not self._handoff("check appeared after submit", lambda: detect_blocker(self.page) is None):
                        return self._result("needs_human", "captcha after submit")
                    self.page.wait_for_timeout(3000)
                if looks_successful(self.page, self.scope):
                    return self._result("submitted", "confirmation message seen")
                after = snapshot(self.page, self.scope)
                if after.errors or after.fingerprint == before_fp:
                    continue  # validation problem - loop tries variants, then gives up
                return self._result("unconfirmed", "clicked submit but saw no confirmation - please verify manually")

            before_url, before_fp = self.page.url, snap.fingerprint
            if not self._click(btn):
                return self._result("failed", f"could not click {btn.label!r}")
            self.pages += 1
            self._wait_change(before_url, before_fp)

        return self._result("failed", "timed out or exceeded max pages")
