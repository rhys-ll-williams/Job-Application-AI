"""Decide what to put in each field.

Order of precedence (first hit wins):
  file uploads -> checkboxes -> user answer bank -> eligibility/consent rules
  -> UK diversity questions -> plain profile fields -> remembered answers -> LLM
"""
from __future__ import annotations

import logging
import re

from . import llm_answers, profile_rules
from .demographics import demographic_answer
from .dom import Field
from .text import best_option, is_placeholder, norm, pick_numeric_band
from .types import Answer, AnswerContext

log = logging.getLogger(__name__)
_LONG_HINT = re.compile(r"\bwhy\b|describe|tell us|explain|example|motivat|interest(ed)? in|what (makes|do you)|how (would|do|did)", re.I)
_COVER = re.compile(r"cover|covering|motivation|letter", re.I)
_CV = re.compile(r"\bcv\b|resume|résumé|curriculum", re.I)
_GENERIC_UPLOAD = re.compile(r"attach|upload|choose( a)? files?|browse|select( a)? files?|add files?|drop files?( here)?|files?")
_SALARY_OR_YEARS =re.compile(r"salary|compensation|pay|remuneration|years?", re.I)


def _file_answer(f: Field, ctx: AnswerContext) -> Answer:
    # Real sites often label the input just "Attach"/"Upload"; the html id/name ("resume", "cover_letter") is the tell.
    text = f"{f.label} {f.name} {f.html_id} {f.placeholder}".replace("_", " ").replace("-", " ")
    path = ctx.docs.cv_path or ctx.profile.master_cv_pdf
    if _COVER.search(text):
        if ctx.docs.cover_path:
            return Answer(ctx.docs.cover_path, "profile")
        return Answer(None, "none", confident=not f.required, note="cover letter required but none generated")
    generic = not norm(f.label) or bool(_GENERIC_UPLOAD.fullmatch(norm(f.label)))
    if _CV.search(text) or f.required or (generic and not ctx.docs.cv_uploaded):
        # ...and the first *unlabelled* upload on a form is almost always the CV
        if path:
            return Answer(path, "profile")
        return Answer(None, "none", confident=not f.required, note="CV upload but no CV file available")
    return Answer(None, "none", note="optional upload left empty")


def _fit_to_options(ans: Answer, f: Field, options: list[str]) -> Answer:
    """Make sure a profile-derived answer actually corresponds to one of the offered options."""
    if not ans.usable or not options:
        return ans
    text = str(ans.value)
    hit = best_option(text, options)
    if hit is None and re.fullmatch(r"\d+(\.\d+)?", text) and _SALARY_OR_YEARS.search(f.label):
        hit = pick_numeric_band(float(text), options)
    if hit is None:
        return Answer(None, ans.source, confident=False, note=f"{text!r} matches no option")
    return Answer(hit, ans.source, ans.confident, ans.note)


def _clean_for_kind(ans: Answer, f: Field) -> Answer:
    if not ans.usable or not isinstance(ans.value, str):
        return ans
    v = ans.value
    if f.kind == "number":
        m = re.search(r"\d+(?:\.\d+)?", v.replace(",", ""))
        return Answer(m.group(0), ans.source, ans.confident, ans.note) if m else Answer(None, ans.source, False, "non-numeric answer")
    if f.kind == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return Answer(None, ans.source, False, "date field needs an ISO date")
    if f.maxlength:
        v = v[: f.maxlength]
    return Answer(v, ans.source, ans.confident, ans.note)


def _remembered(f: Field, options: list[str], ctx: AnswerContext) -> Answer | None:
    if ctx.db is None:
        return None
    stored = ctx.db.get_answer(norm(f.label))
    if not stored:
        return None
    if options:
        hit = best_option(stored, options)
        return Answer(hit, "memory") if hit else None
    return Answer(stored, "memory")


def _llm(f: Field, options: list[str], ctx: AnswerContext) -> Answer:
    label = f.label or f.placeholder or f.name
    if f.kind in {"select", "radio", "combobox"} and options:
        return llm_answers.choose(label, options, ctx)
    if f.kind == "checkbox_group":
        return llm_answers.choose(label, options, ctx, multi=True)
    if f.kind == "number":
        return llm_answers.short_text(label, ctx, numeric=True, maxlength=f.maxlength)
    if f.kind == "textarea" or (f.kind == "text" and (_LONG_HINT.search(label) or (f.maxlength or 0) > 250)):
        return llm_answers.long_text(label, ctx, maxlength=f.maxlength)
    return llm_answers.short_text(label, ctx, maxlength=f.maxlength)


def answer_field(f: Field, ctx: AnswerContext, options: list[str] | None = None, allow_llm: bool = True) -> Answer:
    """Return the answer for one field. `options` overrides f.options (used for lazily-read comboboxes).

    With allow_llm=False only rules/memory-free sources are used - the agent passes this for optional
    fields so a 4B model isn't asked to improvise answers nobody requires."""
    p = ctx.profile
    label = f.label or f.placeholder or f.name
    opts = options if options is not None else [o.text for o in f.options]
    opts = [o for o in opts if o and not is_placeholder(o)]

    if f.kind == "file":
        return _file_answer(f, ctx)
    if f.kind == "password":
        return Answer(None, "none", confident=False, note="password field - login/registration wall")

    if f.kind == "textarea" and _COVER.search(label) and ctx.docs.cover_text:
        return Answer(ctx.docs.cover_text[: f.maxlength] if f.maxlength else ctx.docs.cover_text, "profile")

    if f.kind == "checkbox":
        a = profile_rules.checkbox_answer(label, p, ctx.apply_cfg.accept_consent_checkboxes)
        return a or Answer(None, "none", confident=not f.required, note=f"unknown checkbox {label!r}")

    chain = (
        lambda: profile_rules.bank_answer(label, p),
        lambda: profile_rules.eligibility_answer(label, p),
        lambda: profile_rules.experience_answer(label, p, f.kind, bool(opts)),
        lambda: demographic_answer(label, f.section, opts, f.required, p.demographics, f.kind),
        lambda: profile_rules.general_answer(label, p, f.kind),
    )
    for step in chain:
        a = step()
        if a is not None:
            a = _clean_for_kind(_fit_to_options(a, f, opts), f)
            if a.usable or a.source == "demographic":
                return a  # demographic 'None' is deliberate (leave blank) - don't fall through to the LLM
            # profile value didn't fit the widget: let memory / LLM try, but keep note
            break

    if not allow_llm:
        return Answer(None, "none", note="optional field with no profile answer - left blank")

    mem = _remembered(f, opts, ctx)
    if mem is not None:
        return _clean_for_kind(mem, f)

    a = _clean_for_kind(_llm(f, opts, ctx), f)
    if a.usable and a.confident and ctx.db is not None and isinstance(a.value, str) and f.kind != "combobox":
        ctx.db.save_answer(norm(f.label), f.label, a.value, "llm")
    return a
