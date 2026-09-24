"""Decide whether a job is worth applying to: hard filters + blended keyword/LLM score."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .config import FilterConfig, Profile
from .cv.tailor import _has, profile_terms
from .forms.llm_answers import profile_brief
from .forms.text import norm
from .forms.types import JobInfo
from .llm import LLMClient, LLMError

log = logging.getLogger(__name__)


@dataclass
class Match:
    score: int
    reason: str
    reject: bool = False


def hard_filter(job: JobInfo, flt: FilterConfig) -> str | None:
    """Return a rejection reason, or None if the job passes the cheap rules."""
    title = norm(job.title)
    for kw in flt.exclude_title_keywords:
        if _has(kw, title):
            return f"title contains excluded word {kw!r}"
    if flt.include_title_keywords and not any(_has(kw, title) for kw in flt.include_title_keywords):
        return "title matches none of include_title_keywords"
    if any(norm(c) == norm(job.company) for c in flt.exclude_companies):
        return "company excluded"
    return None


def _deal_breakers(job: JobInfo, p: Profile) -> list[str]:
    text = job.description
    out = []
    if p.work_auth.security_clearance.lower() == "none" and re.search(r"\b(sc|dv|security)\s*clearance|must (hold|have) .{0,20}clearance|eligible for .{0,15}clearance", text, re.I):
        out.append("needs security clearance")
    if p.work_auth.right_to_work_uk and re.search(r"(must|need to) (already )?(be|have).{0,30}(citizen|british national)|british citizens? only", text, re.I):
        out.append("restricted to UK nationals")
    m = re.search(r"(\d{1,2})\+?\s*(?:or more )?years?(?: of)?(?: relevant| professional| commercial)? experience", text, re.I)
    if m and p.personal.years_experience is not None and int(m.group(1)) >= p.personal.years_experience + 3:
        out.append(f"asks for {m.group(1)}+ years experience")
    return out


def score_job(job: JobInfo, p: Profile, llm: LLMClient | None) -> Match:
    terms = profile_terms(p)
    jd = norm(f"{job.title} {job.description}")
    hits = [t for t in terms if _has(t, jd)]
    det = min(100, 20 + 16 * len(hits))
    breakers = _deal_breakers(job, p)

    llm_score, why = None, ""
    if llm is not None and job.description:
        schema = {"type": "object", "required": ["score", "reason"],
                  "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}, "reason": {"type": "string"}}}
        try:
            d = llm.json("You screen job postings for a candidate. Be realistic and strict.",
                         f"CANDIDATE:\n{profile_brief(p, 2200)}\n\nJOB: {job.title} at {job.company}, {job.location}\n{job.description[:2600]}\n\n"
                         "Rate 0-100 how well the candidate fits this job (skills, seniority, domain). "
                         "80+ = strong fit, 50-79 = plausible, below 50 = poor fit. Give a one-sentence reason.",
                         schema, max_tokens=120)
            llm_score, why = int(d.get("score", 0)), str(d.get("reason", ""))[:200]
        except (LLMError, ValueError, TypeError) as exc:
            log.warning("scoring LLM failed: %s", exc)

    score = round(0.6 * llm_score + 0.4 * det) if llm_score is not None else det
    reason = f"skills matched: {', '.join(hits[:6]) or 'none'}" + (f"; {why}" if why else "")
    if breakers:
        return Match(min(score, 30), f"{reason}; deal-breaker: {', '.join(breakers)}", reject=True)
    return Match(score, reason)
