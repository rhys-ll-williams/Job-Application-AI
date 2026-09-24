"""LLM fallback for questions the deterministic rules can't answer.

Guard rails for a 4B model:
  * JSON-schema constrained output for choices and short answers
  * the candidate brief contains facts only - demographics are never sent
  * a 'confidence' flag lets the caller skip instead of guess
"""
from __future__ import annotations

import logging
import re

from ..config import Profile
from ..grounding import ungrounded
from ..llm import LLMError
from .types import Answer, AnswerContext, JobInfo

log = logging.getLogger(__name__)

SYSTEM = (
    "You complete a UK job application on behalf of the candidate described in CANDIDATE. "
    "Rules: use ONLY facts stated in CANDIDATE. Never invent employers, qualifications, dates, numbers, "
    "certifications or skills. If CANDIDATE does not contain the answer, set confidence to \"low\". "
    "Write in first person and British English. Be concise."
)
_REASONING = re.compile(r"^\s*(hmm|okay|ok,|alright|let me|the user|so,? the|first,? i|i need to|looking at)\b", re.I)
_BAD = re.compile(r"\[(your|insert|name|company|date)[^\]]*\]|as an ai|language model|lorem ipsum|\bn/?a\b|<[^>]+>", re.I)


def profile_brief(p: Profile, max_chars: int = 3500, include_summary: bool = True) -> str:
    lines = [f"Name: {p.personal.full_name}"]
    if p.personal.current_title:
        lines.append(f"Current role: {p.personal.current_title} at {p.personal.current_company}")
    if p.personal.years_experience is not None:
        lines.append(f"Years of experience: {p.personal.years_experience:g}")
    lines.append(f"Location: {p.personal.city}, {p.personal.country}")
    lines.append(f"Right to work in UK: {'yes' if p.work_auth.right_to_work_uk else 'no'}; "
                 f"needs sponsorship: {'yes' if p.work_auth.requires_sponsorship else 'no'}")
    lines.append(f"Notice period: {p.preferences.notice_period}; work arrangement preference: {p.preferences.work_arrangement}")
    if p.summary and include_summary:
        lines.append(f"Summary: {p.summary}")
    for g in p.skills:
        lines.append(f"Skills ({g.category}): {', '.join(g.items)}")
    for j in p.experience:
        lines.append(f"Role: {j.title}, {j.company} ({j.start}-{j.end})")
        lines.extend(f"  - {b}" for b in j.bullets[:4])
    for e in p.education:
        lines.append(f"Education: {e.degree}, {e.institution} {e.grade}".strip())
    for pr in p.projects:
        lines.append(f"Project: {pr.name} - {pr.description}")
    if p.certifications:
        lines.append("Certifications: " + "; ".join(p.certifications))
    if p.languages:
        lines.append("Languages: " + ", ".join(p.languages))
    for k, v in p.extra_facts.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)[:max_chars]


def _job_brief(job: JobInfo, max_desc: int = 1500) -> str:
    if not (job.title or job.company):
        return ""
    return f"\nJOB: {job.title} at {job.company}\n{job.description[:max_desc]}"


def choose(label: str, options: list[str], ctx: AnswerContext, *, multi: bool = False) -> Answer:
    """Pick from a closed list of options."""
    if ctx.llm is None:
        return Answer(None, "none", confident=False, note="no LLM available")
    numbered = "\n".join(f"{i}: {o}" for i, o in enumerate(options))
    schema = {"type": "object",
              "properties": {"index": {"type": "integer"} if not multi else {"type": "array", "items": {"type": "integer"}},
                             "confidence": {"type": "string", "enum": ["high", "low"]}},
              "required": ["index", "confidence"]}
    user = (f"CANDIDATE:\n{profile_brief(ctx.profile)}{_job_brief(ctx.job, 800)}\n\n"
            f"QUESTION: {label}\nOPTIONS:\n{numbered}\n\n"
            f"Reply with the {'indexes' if multi else 'index'} of the option that is true for the candidate.")
    try:
        data = ctx.llm.json(SYSTEM, user, schema, max_tokens=60)
    except LLMError as exc:
        return Answer(None, "llm", confident=False, note=str(exc))
    raw = data.get("index")
    idxs = raw if isinstance(raw, list) else [raw]
    picked = [options[i] for i in idxs if isinstance(i, int) and 0 <= i < len(options)]
    if not picked:
        return Answer(None, "llm", confident=False, note="LLM returned an invalid index")
    return Answer(picked if multi else picked[0], "llm", confident=data.get("confidence") == "high")


def short_text(label: str, ctx: AnswerContext, *, numeric: bool = False, maxlength: int | None = None) -> Answer:
    if ctx.llm is None:
        return Answer(None, "none", confident=False, note="no LLM available")
    schema = {"type": "object",
              "properties": {"answer": {"type": "string"}, "confidence": {"type": "string", "enum": ["high", "low"]}},
              "required": ["answer", "confidence"]}
    hint = " Answer with digits only." if numeric else ""
    user = (f"CANDIDATE:\n{profile_brief(ctx.profile)}{_job_brief(ctx.job, 800)}\n\n"
            f"FORM FIELD: {label}\nGive the exact text to type into this field (a few words at most).{hint}")
    try:
        data = ctx.llm.json(SYSTEM, user, schema, max_tokens=80)
    except LLMError as exc:
        return Answer(None, "llm", confident=False, note=str(exc))
    text = str(data.get("answer", "")).strip()
    if numeric:
        m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
        text = m.group(0) if m else ""
    if not text or _BAD.search(text):
        return Answer(None, "llm", confident=False, note="unusable LLM answer")
    if maxlength:
        text = text[:maxlength]
    return Answer(text, "llm", confident=data.get("confidence") == "high")


def long_text(label: str, ctx: AnswerContext, *, maxlength: int | None = None) -> Answer:
    """Free-text questions ('Why do you want this role?', 'Tell us about a challenge')."""
    if ctx.llm is None:
        return Answer(None, "none", confident=False, note="no LLM available")
    limit = maxlength or 1200
    words = max(30, min(140, limit // 7))
    brief, job = profile_brief(ctx.profile), _job_brief(ctx.job)
    # JSON-schema output matters here: unconstrained, Qwen3-4B "thinks aloud" in the answer text
    # even with think=false and can burn the whole token budget before answering.
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    extra = ""
    text, problem = "", "no attempt"
    for _ in range(3):
        user = (f"CANDIDATE:\n{brief}{job}\n\nAPPLICATION QUESTION: {label}\n\n"
                f"Write the candidate's answer in about {words} words. First person, specific, no headings, no bullet "
                f"points, no placeholders. Only mention employers, tools, qualifications and figures that appear in CANDIDATE "
                f"or JOB.{extra}")
        try:
            text = str(ctx.llm.json(SYSTEM, user, schema, max_tokens=int(words * 2.4) + 60).get("answer", "")).strip()
        except LLMError as exc:
            return Answer(None, "llm", confident=False, note=str(exc))
        if len(text) < 20 or _BAD.search(text) or _REASONING.match(text):
            problem, extra = "unusable text", "\nWrite only the answer itself, not notes about the task."
            continue
        bad = ungrounded(text, brief, ctx.job.title, ctx.job.company, ctx.job.description, label)
        if bad:
            problem, extra = f"ungrounded terms {bad[:4]}", f"\nDo NOT mention: {', '.join(bad[:6])} - they are not in CANDIDATE or JOB."
            continue
        if len(text) > limit:
            cut = text[:limit]
            text = cut[: cut.rfind(".") + 1] or cut
        return Answer(text, "llm", confident=True)
    return Answer(None, "llm", confident=False, note=f"LLM answer rejected: {problem}")
