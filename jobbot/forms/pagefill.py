"""Single-page form filling: snapshot -> answer -> write.

Shared by the autonomous `FormAgent` (jobbot.forms.agent) and the manual,
page-at-a-time filler (jobbot.formfiller). This module never clicks a button
and never navigates - it only fills whatever is on the page right now. What
happens next (click Next, wait for you, give up) is entirely up to the caller.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

from .answers import answer_field
from .dom import Field, fill, read_combobox_options, snapshot
from .text import norm
from .types import Answer, AnswerContext

TEXTLIKE = {"text", "email", "tel", "number", "url", "textarea"}
Variant = Callable[[Field, object, int], object]


@dataclass
class FieldResult:
    label: str
    kind: str
    required: bool
    value: object
    source: str
    confident: bool
    filled: bool
    note: str = ""


@dataclass
class PageFillResult:
    fields: list[FieldResult] = field(default_factory=list)
    unsure: list[tuple[Field, str]] = field(default_factory=list)

    @property
    def filled_count(self) -> int:
        return sum(1 for r in self.fields if r.filled)


def format_variant(f: Field, value, retry: int):
    """Alternate formats to try when the site rejects the first attempt (retry=0 = unchanged)."""
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


def _record(f: Field, a: Answer, ok: bool) -> FieldResult:
    v = a.value if not isinstance(a.value, str) else a.value[:160]
    return FieldResult(label=f.label or f.name or f.kind, kind=f.kind, required=f.required, value=v,
                       source=a.source, confident=a.confident, filled=ok, note=a.note)


def _handle_field(page: Page, f: Field, ctx: AnswerContext, retry: int, skip_if_unsure: bool,
                  pause_s: tuple[float, float], variant: Variant | None, result: PageFillResult) -> bool:
    """Fill one field. Returns True if something was written."""
    force = retry > 0 and f.invalid
    if f.has_value and not force:
        if f.kind in TEXTLIKE:
            a = answer_field(f, ctx, allow_llm=False)
            if not (a.usable and a.source in {"profile", "bank"} and norm(str(a.value)) != norm(f.value)):
                return False
            # else: site pre-filled something different from our profile (e.g. old phone number) - correct it
        else:
            return False

    options = None
    if f.kind == "combobox":
        read = read_combobox_options(page, f)
        # Long/virtualised lists (countries, phone codes) can't be validated up front: for those we type the
        # answer and pick from the filtered results instead. Short lists (yes/no, notice period) are validated.
        options = read if 0 < len(read) <= 12 else None
    a = answer_field(f, ctx, options=options, allow_llm=f.required)

    if not a.usable:
        result.fields.append(_record(f, a, False))
        if f.required:
            result.unsure.append((f, a.note or "no answer available"))
        return False
    if not a.confident and f.required and skip_if_unsure:
        result.fields.append(_record(f, a, False))
        result.unsure.append((f, a.note or "low-confidence answer"))
        return False
    if not a.confident and not f.required:
        result.fields.append(_record(f, a, False))
        return False

    value = variant(f, a.value, retry) if variant else a.value
    ok = fill(page, f, value)
    if ok and f.kind == "file" and value == ctx.docs.cv_path:
        ctx.docs.cv_uploaded = True
    result.fields.append(_record(f, a, ok))
    if not ok and f.required:
        result.unsure.append((f, "widget rejected the value"))
    if ok:
        page.wait_for_timeout(int(random.uniform(*pause_s) * 1000))
    return ok


def fill_page(page: Page, ctx: AnswerContext, scope: str | None = None, *, retry: int = 0,
             skip_if_unsure: bool = True, pause_s: tuple[float, float] = (0.15, 0.5),
             variant: Variant | None = None, max_passes: int = 6) -> PageFillResult:
    """Fill every answerable field currently visible on the page (or within `scope`).

    Runs several passes because answering one question can reveal another (e.g. a
    conditional follow-up after ticking "yes"). Never clicks Next/Submit - that is
    the caller's job, once it decides what should happen next.
    """
    result = PageFillResult()
    filled_sigs: set[str] = set()
    for _ in range(max_passes):
        snap = snapshot(page, scope)
        todo = sorted((f for f in snap.fields if f.sig not in filled_sigs), key=lambda f: f.kind != "file")
        if not todo:
            break
        progressed = False
        for f in todo:
            filled_sigs.add(f.sig)
            try:
                wrote = _handle_field(page, f, ctx, retry, skip_if_unsure, pause_s, variant, result)
            except PWError as exc:
                result.fields.append(FieldResult(f.label or f.name or f.kind, f.kind, f.required, None,
                                                 "none", False, False, str(exc).splitlines()[0]))
                wrote = False
            progressed |= wrote
            if wrote and f.kind == "file":
                page.wait_for_timeout(1800)  # ATS resume-parsing may re-render and prefill the form
                break
        if not progressed:
            break
    return result
