"""Cover letter writer: LLM draft -> grounding check -> deterministic fallback."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from ..config import Profile
from ..forms.llm_answers import profile_brief
from ..forms.types import JobInfo
from ..grounding import ungrounded
from ..llm import LLMClient, LLMError
from .tailor import TailoredCV

log = logging.getLogger(__name__)


@dataclass
class CoverLetter:
    paragraphs: list[str]
    from_llm: bool

    def text(self, p: Profile, job: JobInfo) -> str:
        head = f"{p.personal.full_name}\n{p.personal.email} | {p.personal.phone}\n\n{date.today().strftime('%d %B %Y').lstrip('0')}\n\n"
        return (head + "Dear Hiring Manager,\n\n" + "\n\n".join(self.paragraphs)
                + f"\n\nYours faithfully,\n{p.personal.full_name}")


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def _fallback(p: Profile, job: JobInfo, cv: TailoredCV) -> CoverLetter:
    """Plain but always truthful: built only from profile facts."""
    role = job.title or "advertised"
    first = p.summary.strip().split(". ")[0].rstrip(".") + "." if p.summary else ""
    p1 = f"I am writing to apply for the {role} position at {job.company or 'your company'}. {first}".strip()
    bullets = [b.rstrip(".") for j in cv.experience[:2] for b in j.bullets[:1]]
    who = f"As {p.personal.current_title} at {p.personal.current_company}, " if p.personal.current_title and p.personal.current_company else ""
    p2 = (who + "relevant experience includes: " + "; ".join(_lower_first(b) for b in bullets) + ".") if bullets else ""
    skills = ", ".join(cv.matched_keywords[:5])
    p3 = (f"I would welcome the opportunity to discuss how my skills in {skills} could contribute to your team. "
          if skills else "I would welcome the opportunity to discuss how I could contribute to your team. ") + "Thank you for considering my application."
    return CoverLetter([x for x in (p1, p2, p3) if x], from_llm=False)


def write_cover_letter(p: Profile, job: JobInfo, cv: TailoredCV, llm: LLMClient | None) -> CoverLetter:
    if llm is None or not job.title:
        return _fallback(p, job, cv)
    brief = profile_brief(p, 2800)
    evidence = [b for j in cv.experience[:2] for b in j.bullets[:2]]
    schema = {"type": "object", "required": ["opening", "evidence", "closing"],
              "properties": {"opening": {"type": "string"}, "evidence": {"type": "string"}, "closing": {"type": "string"}}}
    extra = ""
    for attempt in range(3):
        user = (f"CANDIDATE:\n{brief}\n\nROLE: {job.title} at {job.company}\nJOB POSTING:\n{job.description[:1800]}\n\n"
                f"BEST EVIDENCE TO USE:\n" + "\n".join(f"- {e}" for e in evidence) + "\n\n"
                "Write a UK cover letter body in three paragraphs (no greeting, no sign-off):\n"
                "opening: why this role and company suit the candidate (max 55 words)\n"
                "evidence: 2-3 sentences using the BEST EVIDENCE above, linked to what the posting asks for (max 85 words)\n"
                "closing: enthusiasm and a call to action (max 35 words)\n"
                "First person, British English, no clichés, no placeholders. Only claim what is in CANDIDATE." + extra)
        try:
            data = llm.json("You write concise, honest UK job application cover letters.", user, schema, max_tokens=520)
        except LLMError as exc:
            log.warning("cover letter LLM failed: %s", exc)
            break
        paras = [str(data.get(k, "")).strip() for k in ("opening", "evidence", "closing")]
        bad = ungrounded("\n".join(paras), brief, job.title, job.company, job.description)
        if all(len(x) > 25 for x in paras) and not bad:
            return CoverLetter(paras, from_llm=True)
        extra = f"\nDo NOT mention: {', '.join(bad[:8])} - not in CANDIDATE or JOB." if bad else "\nAll three paragraphs are required."
        log.info("cover letter attempt %d rejected (%s)", attempt + 1, bad[:4])
    return _fallback(p, job, cv)
