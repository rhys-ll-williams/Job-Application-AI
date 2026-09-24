"""UK equal-opportunities / diversity-monitoring questions.

Rules:
  * Answers come only from `profile.demographics`, which the user fills in.
    Nothing is inferred from the CV, name or photo.
  * The default for every topic is 'prefer_not_to_say'.
  * If the user gave a specific value but no option matches it, fall back to
    'prefer not to say' (never guess a different category).
  * If a form offers no 'prefer not to say' option and the question is optional,
    it is left blank; if required, the caller is told it is unanswerable.
"""
from __future__ import annotations

import re

from ..config import PREFER_NOT, Demographics
from .text import PNTS_RE, best_option, norm
from .types import Answer

# (attribute on Demographics, label regex). Order matters: specific before general.
TOPICS: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.I)) for k, p in [
    ("gender_same_as_birth", r"(same as|match).*(sex|gender).*birth|assigned at birth|gender.*(registered|assigned)|identify.*(different|same).*birth|trans(gender)? (status|experience|history)"),
    ("pregnancy_or_maternity", r"pregnan|maternity"),
    ("sexual_orientation", r"sexual orientation|orientation|sexuality"),
    ("religion", r"religio|belief|faith"),
    ("free_school_meals", r"free school meals?"),
    ("first_generation_university", r"first (person|generation|in your family)|parent.{0,40}(university|degree|higher education|graduat)|attended university"),
    ("school_type", r"school.{0,20}(type|attended)|type of school|state school|independent school|fee[- ]paying|secondary school|schools? did you attend"),
    ("socio_economic_background", r"socio[- ]?economic|main (earner|household)|parent.{0,40}(occupation|job|employment)|household income|occupation of (your )?(parent|guardian)"),
    ("disability", r"disab|long[- ]term (health|condition|illness)|impairment|neurodiver|equality act"),
    ("caring_responsibilities", r"caring responsib|\bcarer\b|care for (a )?(child|family|relative|dependent)"),
    ("veteran", r"veteran|armed forces|military (service|background)|served in the"),
    ("marital_status", r"marital|marriage|civil partner|relationship status"),
    ("age_range", r"age (range|band|group|bracket)|how old|your age|which age|date of birth|\bdob\b|\bage\b"),
    ("ethnicity", r"ethnic|\brace\b|racial|ethnicity"),
    ("gender", r"\bgender\b|\bsex\b|gender identity"),
]]
_ADJUST_RE = re.compile(r"reasonable adjustment|adjustments?|accommodation|assistance (during|with) (the )?(interview|recruitment)|support you (need|require)", re.I)
_SECTION_RE = re.compile(r"equal opportunit|diversity|monitoring|\beeo?\b|inclusion|equality|about you|demographic|voluntary self", re.I)
_PRONOUN_RE = re.compile(r"pronoun", re.I)

_ETHNIC_OPTS = re.compile(r"\b(asian|black|african|caribbean|white|mixed|chinese|arab|indian|pakistani|bangladeshi|gypsy|traveller)\b", re.I)
_GENDER_OPTS = re.compile(r"^(male|female|man|woman|non[- ]?binary)$", re.I)


def detect_topic(label: str, section: str, options: list[str]) -> str | None:
    """Return the Demographics attribute this question is about, '_unknown_eeo', or None."""
    text = f"{label}".strip()
    for key, rx in TOPICS:
        if rx.search(text):
            return key
    # Generic label ("Please select") - infer from the options offered.
    if options:
        joined = " ".join(options)
        if len(_ETHNIC_OPTS.findall(joined)) >= 3:
            return "ethnicity"
        if sum(1 for o in options if _GENDER_OPTS.match(o.strip())) >= 2:
            return "gender"
    if _PRONOUN_RE.search(text):
        return "_pronouns"
    if section and _SECTION_RE.search(section) and not re.search(r"name|email|phone|address|cv|resume", text, re.I):
        return "_unknown_eeo"
    return None


def _pnts(options: list[str]) -> str | None:
    hits = [o for o in options if PNTS_RE.search(o)]
    return hits[0] if hits else None


def demographic_answer(label: str, section: str, options: list[str], required: bool,
                       demo: Demographics, kind: str = "text") -> Answer | None:
    """Answer a diversity-monitoring question, or return None if it isn't one."""
    topic = detect_topic(label, section, options)
    if topic is None:
        return None

    # Free-text 'adjustments' question sitting in a disability section
    if topic == "disability" and _ADJUST_RE.search(label) and kind in {"text", "textarea"}:
        return Answer(demo.disability_adjustments or "None requested", "demographic")

    if topic in {"_unknown_eeo", "_pronouns"}:
        value = _pnts(options)
        if value:
            return Answer(value, "demographic")
        return Answer(None, "demographic", confident=not required, note=f"unrecognised monitoring question {label!r}")

    stated = getattr(demo, topic, PREFER_NOT) or PREFER_NOT

    # Date-of-birth style fields
    if topic == "age_range" and re.search(r"date of birth|\bdob\b", label, re.I):
        if demo.date_of_birth and kind in {"date", "text"}:
            return Answer(demo.date_of_birth, "demographic")
        return Answer(None, "demographic", confident=not required, note="DOB requested but not provided")

    # Free text box (no options): only write something if the user gave a value.
    if not options:
        if stated != PREFER_NOT:
            return Answer(stated, "demographic")
        return Answer("Prefer not to say" if required else None, "demographic")

    if stated != PREFER_NOT:
        choice = best_option(stated, options)
        if choice:
            return Answer(choice, "demographic")
        # stated a value but the form's categories don't match it: don't guess
        fallback = _pnts(options)
        if fallback:
            return Answer(fallback, "demographic", note=f"no option matched {stated!r}; used prefer-not-to-say")
        return Answer(None, "demographic", confident=not required, note=f"no option matched {stated!r}")

    choice = _pnts(options)
    if choice:
        return Answer(choice, "demographic")
    return Answer(None, "demographic", confident=not required, note="no 'prefer not to say' option offered")
