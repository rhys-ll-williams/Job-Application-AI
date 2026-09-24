"""Tailor the CV to a job posting without inventing anything.

What tailoring means here:
  * skills that the posting mentions move to the front of each skill group
  * each role keeps its most relevant bullets (relevance = overlap with the posting)
  * only the most relevant projects are kept
  * the opening summary is rewritten by the LLM, then checked by the grounding filter
  * (optional) bullets are re-worded to the posting's vocabulary - accepted only if the
    rewrite introduces no new numbers, names or technologies
Nothing else is generated, so the CV stays truthful by construction.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..config import Education, Job, Profile, Project, SkillGroup
from ..forms.llm_answers import profile_brief
from ..forms.text import norm
from ..forms.types import JobInfo
from ..grounding import ungrounded
from ..llm import LLMClient, LLMError

log = logging.getLogger(__name__)


@dataclass
class TailoredCV:
    name: str
    contact_line: str
    summary: str
    experience: list[Job]
    education: list[Education]
    projects: list[Project]
    skills: list[SkillGroup]
    certifications: list[str]
    languages: list[str]
    matched_keywords: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)


def _has(phrase: str, blob_norm: str) -> bool:
    p = norm(phrase)
    return bool(p) and f" {p} " in f" {blob_norm} "


def profile_terms(p: Profile) -> list[str]:
    """Every skill/tool the candidate can legitimately claim."""
    terms: dict[str, str] = {}
    for g in p.skills:
        for s in g.items:
            terms.setdefault(norm(s), s)
    for j in p.experience:
        for s in j.tech:
            terms.setdefault(norm(s), s)
    for pr in p.projects:
        for s in pr.tech:
            terms.setdefault(norm(s), s)
    return list(terms.values())


def job_keywords(job: JobInfo, llm: LLMClient | None) -> list[str]:
    """Skills/requirements the posting emphasises (LLM), used for the 'missing' report only."""
    if llm is None or not job.description:
        return []
    schema = {"type": "object", "properties": {"skills": {"type": "array", "items": {"type": "string"}}}, "required": ["skills"]}
    try:
        data = llm.json("Extract requirements from job postings. Only list items that appear in the text.",
                        f"JOB POSTING:\n{job.title} at {job.company}\n{job.description[:3500]}\n\n"
                        "List up to 12 specific skills, tools, technologies or qualifications the posting asks for.",
                        schema, max_tokens=220)
    except LLMError:
        return []
    jd = norm(job.description)
    return [s.strip() for s in data.get("skills", []) if isinstance(s, str) and _has(s, jd)][:12]


def _bullet_score(text: str, matched: list[str], jd_words: set[str]) -> float:
    b = norm(text)
    score = sum(3 for m in matched if _has(m, b))
    score += 0.3 * len(set(b.split()) & jd_words - _STOP)
    return score


_STOP = set("a an the and or of to in for with on at by from as is are was were be been i my we our you your this that it its into using used use".split())


def _tailor_summary(p: Profile, job: JobInfo, matched: list[str], llm: LLMClient | None) -> str:
    base = p.summary.strip()
    if llm is None or not job.title:
        return base
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    brief = profile_brief(p, 2500, include_summary=False)  # else the model just copies it back
    facts = profile_brief(p, 3500)  # grounding vocabulary includes the original summary
    extra = ""
    for _ in range(3):
        try:
            text = str(llm.json(
                "You write the opening summary of a UK CV. Use ONLY facts in CANDIDATE. British English, no first-person pronouns "
                "(implied subject), no clichés like 'passionate' or 'results-driven'.",
                f"CANDIDATE:\n{brief}\n\nTARGET ROLE: {job.title} at {job.company}\nSKILLS THE ROLE WANTS THAT THE CANDIDATE HAS: "
                f"{', '.join(matched[:8]) or 'n/a'}\n\nWrite a 2-3 sentence summary (max 55 words) that leads with what is most "
                f"relevant to the target role. Do not mention the company name.{extra}",
                schema, max_tokens=170).get("summary", "")).strip()
        except LLMError:
            break
        bad = ungrounded(text, facts, job.title)
        if 20 <= len(text) <= 500 and not bad and norm(text) != norm(base):
            return text
        extra = f"\nDo NOT mention: {', '.join(bad[:6])}." if bad else "\nKeep it 2-3 sentences and write it fresh."
    return base


def _rewrite_bullet(bullet: str, job: JobInfo, allowed: str, llm: LLMClient) -> str:
    schema = {"type": "object", "properties": {"bullet": {"type": "string"}}, "required": ["bullet"]}
    try:
        new = str(llm.json(
            "You lightly re-word one CV bullet. Keep every fact. Do not add numbers, tools, employers or claims.",
            f"BULLET: {bullet}\nTARGET ROLE: {job.title}\nRe-word the bullet in one sentence (max 28 words) so it reads naturally "
            f"for the target role, starting with a strong past-tense verb.", schema, max_tokens=80).get("bullet", "")).strip()
    except LLMError:
        return bullet
    if 15 <= len(new) <= 260 and not ungrounded(new, bullet, allowed):
        return new
    return bullet


def tailor(p: Profile, job: JobInfo, llm: LLMClient | None, *, rewrite_bullets: bool = False,
           max_bullets: tuple[int, int] = (5, 3)) -> TailoredCV:
    jd = norm(f"{job.title} {job.description}")
    jd_words = set(jd.split())
    terms = profile_terms(p)
    matched = [t for t in terms if _has(t, jd)]
    wanted = job_keywords(job, llm)
    missing = [k for k in wanted if not any(norm(k) == norm(t) or _has(t, norm(k)) for t in terms)]

    experience: list[Job] = []
    for i, j in enumerate(p.experience):
        limit = max_bullets[0] if i == 0 else max_bullets[1]
        ranked = sorted(j.bullets, key=lambda b: -_bullet_score(b, matched, jd_words))[:limit]
        if rewrite_bullets and llm is not None:
            allowed = " ".join(j.tech + terms)
            ranked = [_rewrite_bullet(b, job, allowed, llm) for b in ranked]
        experience.append(j.model_copy(update={"bullets": ranked}))

    projects = sorted(p.projects, key=lambda pr: -_bullet_score(f"{pr.name} {pr.description} {' '.join(pr.tech)}", matched, jd_words))[:2]

    skills = []
    for g in p.skills:
        items = sorted(g.items, key=lambda s: (0 if _has(s, jd) else 1))
        skills.append(SkillGroup(category=g.category, items=items))

    contact = " | ".join(x for x in [p.personal.city, p.personal.email, p.personal.phone, p.personal.linkedin, p.personal.github] if x)
    return TailoredCV(
        name=p.personal.full_name, contact_line=contact, summary=_tailor_summary(p, job, matched, llm),
        experience=experience, education=p.education, projects=projects, skills=skills,
        certifications=p.certifications, languages=p.languages, matched_keywords=matched, missing_keywords=missing,
    )
