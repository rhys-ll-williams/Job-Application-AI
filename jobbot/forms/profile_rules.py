"""Deterministic label -> profile-value rules. Fast, free and never hallucinate.

The LLM is only consulted for questions these rules can't answer.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from ..config import Profile
from .text import norm
from .types import Answer

R = lambda p: re.compile(p, re.I)  # noqa: E731


def yn(flag: bool) -> str:
    return "yes" if flag else "no"


def _start_date(p: Profile) -> str:
    if p.preferences.earliest_start_date:
        return p.preferences.earliest_start_date
    m = re.search(r"(\d+)\s*(week|month|day)", p.preferences.notice_period, re.I)
    days = 0
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
    return (date.today() + timedelta(days=days)).isoformat()


def _digits(n: int | None) -> str | None:
    return str(n) if n else None


# Rules that decide eligibility / consent: checked BEFORE demographics so that
# e.g. "Are you over the age of 18?" is not mistaken for an age-range question.
ELIGIBILITY: list[tuple[re.Pattern, callable]] = [
    (R(r"(right|eligib|entitle|authori[sz]|permit|legal).{0,40}work.{0,30}(uk|united kingdom|britain|country)|"
       r"work.{0,20}(in|within) the (uk|united kingdom)|right to work"),
     lambda p: yn(p.work_auth.right_to_work_uk)),
    (R(r"sponsor|visa (status|requirement)|require.{0,20}(a )?visa|immigration"),
     lambda p: yn(p.work_auth.requires_sponsorship)),
    (R(r"(over|at least|above|older than|aged?)\s*(the age of )?18|18 years|age of 18|legal working age"),
     lambda p: yn(p.preferences.over_18)),
    (R(r"security clearance|clearance level|\bsc\b clear|\bbpss\b|\bdv\b clear"),
     lambda p: p.work_auth.security_clearance),
    (R(r"(willing|consent|agree|happy|able).{0,40}(dbs|background check|vetting|screening|reference check)"),
     lambda p: yn(p.preferences.dbs_check_ok)),
    (R(r"relocat"), lambda p: yn(p.preferences.willing_to_relocate)),
    (R(r"driving licen[cs]e|driver'?s? licen[cs]e|clean licen[cs]e"), lambda p: yn(p.preferences.driving_licence)),
    (R(r"(previously|ever|formerly)\s+(worked|employed)|former employee|current employee|worked (for|at) us"),
     lambda p: yn(p.preferences.previously_employed_by_applicant_company)),
]

GENERAL: list[tuple[re.Pattern, callable]] = [
    (R(r"^(your |candidate )?(full )?name$|^name\b(?!.*(company|employer|school|reference))|legal name"),
     lambda p: p.personal.full_name),
    (R(r"first name|given name|forename"), lambda p: p.personal.first_name),
    (R(r"last name|surname|family name"), lambda p: p.personal.last_name),
    (R(r"preferred (first )?name|nickname|known as"), lambda p: p.personal.preferred_name or p.personal.first_name),
    (R(r"e-?mail"), lambda p: p.personal.email),
    # "United Kingdom +44", not bare "+44": dropdowns also list Guernsey/Jersey/Isle of Man under +44
    (R(r"country (dial )?code|dialling code|phone.*country"), lambda p: f"{p.personal.country} {p.personal.phone_country_code}"),
    (R(r"phone (device )?type|type of phone|device type"), lambda p: "Mobile"),
    (R(r"phone|mobile|telephone|contact number|cell"), lambda p: p.personal.phone),
    (R(r"address.{0,10}(line )?(1|one)\b|street|^address$|home address|address line"), lambda p: p.personal.address_line1),
    (R(r"address.{0,10}(line )?(2|two)\b"), lambda p: p.personal.address_line2),
    (R(r"\bcity\b|\btown\b|location \(city\)|current location|where are you (based|located)"), lambda p: p.personal.city),
    (R(r"^(county|state|province|region)\b|\bcounty\b|state province"), lambda p: p.personal.county),
    (R(r"post ?code|postal code|zip"), lambda p: p.personal.postcode),
    (R(r"^country|country of residence|which country"), lambda p: p.personal.country),
    (R(r"linked ?in"), lambda p: p.personal.linkedin),
    (R(r"github|git hub"), lambda p: p.personal.github),
    (R(r"website|portfolio|personal (site|url)|blog|other link|^url$"), lambda p: p.personal.website or p.personal.github or p.personal.linkedin),
    (R(r"current (job )?title|current (role|position)|job title"), lambda p: p.personal.current_title),
    (R(r"current (company|employer|organi[sz]ation)|most recent (company|employer)|^(company|employer)( name)?$"),
     lambda p: p.personal.current_company),
    (R(r"years?.{0,15}(of )?(relevant |professional |work )?experience|experience.{0,10}years"),
     lambda p: _digits(int(p.personal.years_experience)) if p.personal.years_experience is not None else None),
    (R(r"(current|present|previous|last) (salary|pay|remuneration|compensation)"), lambda p: None),  # not in profile: never guess
    (R(r"salary|compensation|remuneration|expected pay|pay expectation|desired (pay|rate)"),
     lambda p: _digits(p.preferences.salary_expectation)),
    (R(r"notice period|notice required|how much notice"), lambda p: p.preferences.notice_period),
    (R(r"start date|earliest.{0,15}start|when can you start|availab(le|ility) to start|date available"), _start_date),
    (R(r"how did you (hear|find|come across)|where did you (hear|find|see)|referral source|source of application"),
     lambda p: p.preferences.how_did_you_hear),
    (R(r"work(ing)? (arrangement|pattern|model)|remote|hybrid|on-?site"), lambda p: p.preferences.work_arrangement),
]

# Checkbox intent
_MARKETING = R(r"newsletter|marketing|promotional|\bfollow\b|receive (e-?mails|updates|alerts|news)|job alerts?|talent (community|pool|network)|"
               r"keep (my|your) (details|data|cv|profile)|future (opportunit|role|vacanc)|contact me (about|regarding) other|"
               r"share my (data|details|information) with (third|partner)|sms|text messages?")
_CONSENT = R(r"\bagree\b|\baccept\b|\bconsent\b|\bconfirm\b|\bcertify\b|\backnowledge\b|privacy|terms|data protection|gdpr|"
             r"read and understood|\bdeclare\b|true and (accurate|correct)|i understand")


def checkbox_answer(label: str, profile: Profile, accept_consent: bool) -> Answer | None:
    if _MARKETING.search(label):
        return Answer(False, "profile", note="declined marketing opt-in")
    if _CONSENT.search(label):
        if accept_consent:
            return Answer(True, "profile", note="ticked required consent")
        return Answer(None, "profile", confident=False, note="consent checkbox needs a human (accept_consent_checkboxes is off)")
    return None


_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_SKILL = r"(?P<skill>[a-z0-9+#.][a-z0-9+#. /-]{1,30}?)"
_END = r"\s*(?:\?|$|\(| and | or |,| do you| have you| are you| that )"
_SKILL_Q = [
    re.compile(r"experience\b.{0,30}?\b(?:with|in|using|of|on|working with)\s+" + _SKILL + _END),  # "experience do you have with X"
    re.compile(r"\byears?\s+(?:of\s+)?" + _SKILL + r"\s+experience"),                            # "years of X experience"
]
_YEARS_Q = re.compile(r"(?:years?|how (?:long|many)).{0,30}experience|experience.{0,30}\byears?\b|\byears?\b.{0,20}\bexperience\b")
# captured "skills" that are really generic phrases -> let the total-years rule answer instead
_NOT_A_SKILL = set("a an the this that similar role roles field industry position related sector environment total professional relevant "
                   "work working commercial overall our your job company us business area capacity current previous prior any "
                   "hands on practical full time paid of in with for to and or on years year experience".split())


def _ym(s: str) -> float | None:
    """'Sep 2024' / '2024' / 'Present' -> fractional year."""
    s = s.strip().lower()
    if s in {"present", "current", "now", ""}:
        t = date.today()
        return t.year + (t.month - 1) / 12
    m = re.search(r"(20\d\d|19\d\d)", s)
    if not m:
        return None
    mon = next((v for k, v in _MONTHS.items() if k in s), 1)
    return int(m.group(1)) + (mon - 1) / 12


def profile_text(p: Profile) -> str:
    parts = [p.summary, *p.certifications, *p.languages, *p.extra_facts.values()]
    for g in p.skills:
        parts += g.items
    for j in p.experience:
        parts += [j.title, *j.bullets, *j.tech]
    for pr in p.projects:
        parts += [pr.name, pr.description, *pr.bullets, *pr.tech]
    for e in p.education:
        parts += [e.degree, *e.details]
    return norm(" ".join(x for x in parts if x))


def has_skill(p: Profile, skill: str) -> bool:
    s = norm(skill)
    return bool(s) and f" {s} " in f" {profile_text(p)} "


def skill_years(p: Profile, skill: str) -> int:
    """Years across the roles whose tech list / bullets mention the skill (0 if never used at work)."""
    s = norm(skill)
    total = 0.0
    for j in p.experience:
        blob = norm(" ".join([*j.tech, *j.bullets, j.title]))
        if f" {s} " in f" {blob} ":
            a, b = _ym(j.start), _ym(j.end)
            if a is not None and b is not None and b >= a:
                total += b - a
    return int(round(total))


def experience_answer(label: str, profile: Profile, kind: str, has_options: bool) -> Answer | None:
    """'Years of experience with X' -> years from your roles; 'Do you have experience with X?' -> yes/no from your profile."""
    text = norm(label)
    skill = ""
    for rx in _SKILL_Q:
        m = rx.search(text + " ")
        if m:
            skill = m.group("skill").strip()
            break
    if not skill or set(skill.split()) & _NOT_A_SKILL or len(skill.split()) > 3:
        return None
    if _YEARS_Q.search(text):
        y = skill_years(profile, skill)
        if y == 0 and has_skill(profile, skill):
            y = 1  # listed (e.g. a project or course) but not dated in a role
        return Answer(str(y), "profile", note=f"{y}y with {skill} (from your roles)")
    if has_options or kind in {"checkbox_group"}:
        if re.match(r"(do|have|are|can|did) you", text):
            return Answer("yes" if has_skill(profile, skill) else "no", "profile", note=f"{skill}: {'in' if has_skill(profile, skill) else 'not in'} your profile")
    return None


def _first(rules, label: str, profile: Profile) -> str | None:
    for rx, fn in rules:
        if rx.search(label):
            v = fn(profile)
            return v if v not in (None, "") else None
    return None


def eligibility_answer(label: str, profile: Profile) -> Answer | None:
    v = _first(ELIGIBILITY, label, profile)
    return Answer(v, "profile") if v else None


def general_answer(label: str, profile: Profile, kind: str = "text") -> Answer | None:
    """Match plain profile fields. `kind` lets us avoid e.g. putting a phone in an email box."""
    text = norm(label)
    if kind == "email":
        return Answer(profile.personal.email, "profile")
    if kind == "tel" and not re.search(r"country|code", text):
        return Answer(profile.personal.phone, "profile")
    v = _first(GENERAL, text, profile)
    return Answer(v, "profile") if v else None


def bank_answer(label: str, profile: Profile) -> Answer | None:
    """User-written regex -> answer rules from profile.yaml."""
    for rule in profile.answer_bank:
        if re.search(rule.match, label, re.I):
            return Answer(rule.answer, "bank")
    text = norm(label)
    for key, val in profile.extra_facts.items():
        if norm(key) and norm(key) in text:
            return Answer(val, "bank")
    return None
