"""Typed configuration and candidate profile, loaded from YAML."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PREFER_NOT = "prefer_not_to_say"


# ---------------------------------------------------------------- profile ---
class Personal(BaseModel):
    first_name: str
    last_name: str
    preferred_name: str = ""
    email: str
    phone: str  # national format, e.g. 07123 456789
    phone_country_code: str = "+44"
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    county: str = ""
    postcode: str = ""
    country: str = "United Kingdom"
    linkedin: str = ""
    github: str = ""
    website: str = ""
    current_title: str = ""
    current_company: str = ""
    years_experience: float | None = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class WorkAuth(BaseModel):
    right_to_work_uk: bool = True
    requires_sponsorship: bool = False
    nationality: str = ""
    security_clearance: str = "none"  # e.g. none / BPSS / SC / DV


class Preferences(BaseModel):
    notice_period: str = "1 month"
    earliest_start_date: str = ""
    salary_expectation: int | None = None  # GBP per year
    salary_minimum: int | None = None
    willing_to_relocate: bool = False
    work_arrangement: str = "hybrid"  # onsite / hybrid / remote
    driving_licence: bool = False
    dbs_check_ok: bool = True
    over_18: bool = True
    how_did_you_hear: str = "LinkedIn"
    previously_employed_by_applicant_company: bool = False


class Demographics(BaseModel):
    """Only ever filled from what the user writes here.

    Everything defaults to 'prefer_not_to_say'. The agent never infers these
    from the CV or the candidate's name.
    """

    gender: str = PREFER_NOT
    gender_same_as_birth: str = PREFER_NOT
    ethnicity: str = PREFER_NOT
    sexual_orientation: str = PREFER_NOT
    religion: str = PREFER_NOT
    disability: str = PREFER_NOT
    age_range: str = PREFER_NOT
    date_of_birth: str = ""  # only used if a form insists and this is set
    marital_status: str = PREFER_NOT
    caring_responsibilities: str = PREFER_NOT
    veteran: str = PREFER_NOT
    pregnancy_or_maternity: str = PREFER_NOT
    socio_economic_background: str = PREFER_NOT
    school_type: str = PREFER_NOT
    free_school_meals: str = PREFER_NOT
    first_generation_university: str = PREFER_NOT
    disability_adjustments: str = ""  # free text, blank means none requested


class Job(BaseModel):
    title: str
    company: str
    location: str = ""
    start: str = ""
    end: str = "Present"
    bullets: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str
    start: str = ""
    end: str = ""
    grade: str = ""
    details: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    description: str = ""
    bullets: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)
    url: str = ""


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class AnswerRule(BaseModel):
    """Hand-written answer to a recurring custom question (regex on the label)."""

    match: str
    answer: str


class Profile(BaseModel):
    personal: Personal
    work_auth: WorkAuth = Field(default_factory=WorkAuth)
    preferences: Preferences = Field(default_factory=Preferences)
    demographics: Demographics = Field(default_factory=Demographics)
    summary: str = ""
    experience: list[Job] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    answer_bank: list[AnswerRule] = Field(default_factory=list)
    extra_facts: dict[str, str] = Field(default_factory=dict)
    master_cv_pdf: str = ""  # optional: fallback CV to upload if tailoring fails


# ----------------------------------------------------------------- config ---
class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "qwen3:4b"
    num_ctx: int = 8192
    temperature: float = 0.2
    think: bool = False
    timeout_s: int = 300


class BrowserConfig(BaseModel):
    headless: bool = False  # keep False: you may need to step in for a CAPTCHA/login
    user_data_dir: str = "data/browser_profile"
    slow_mo_ms: int = 60
    channel: str = ""  # "chrome" to use your installed Chrome instead of Playwright's Chromium
    locale: str = "en-GB"
    timezone: str = "Europe/London"


class SearchQuery(BaseModel):
    keywords: str
    location: str = "United Kingdom"


class SearchConfig(BaseModel):
    sources: list[Literal["linkedin", "indeed"]] = ["linkedin", "indeed"]
    queries: list[SearchQuery] = Field(default_factory=list)
    max_jobs_per_query: int = 25
    posted_within_days: int = 7
    remote_only: bool = False
    easy_apply_only: bool = False  # LinkedIn Easy Apply / Indeed Apply only


class FilterConfig(BaseModel):
    include_title_keywords: list[str] = Field(default_factory=list)
    exclude_title_keywords: list[str] = Field(default_factory=lambda: ["senior", "principal", "director", "head of"])
    exclude_companies: list[str] = Field(default_factory=list)
    min_match_score: int = 60  # 0-100, from the LLM + keyword scorer


class ApplyConfig(BaseModel):
    auto_submit: bool = False  # False: stop on the final review page and ask you
    dry_run: bool = False  # True: fill everything but never press final submit
    max_per_day: int = 15
    max_pages_per_application: int = 15
    handoff: Literal["wait", "skip"] = "wait"  # what to do on CAPTCHA / login / account walls
    handoff_timeout_s: int = 300
    skip_if_unsure: bool = True  # skip the job rather than guess a required field
    min_delay_s: float = 2.0
    max_delay_s: float = 6.0
    tailor_cv: bool = True
    rewrite_bullets: bool = False  # LLM re-words bullets (accepted only if it adds no new facts); slower
    write_cover_letter: bool = True
    # Tick "I agree to the privacy policy / terms" boxes that are needed to submit.
    # Marketing / talent-pool / newsletter opt-ins are always declined.
    accept_consent_checkboxes: bool = True


class Config(BaseModel):
    profile_path: str = "profile.yaml"
    db_path: str = "data/jobbot.sqlite3"
    output_dir: str = "data/applications"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    filters: FilterConfig = Field(default_factory=FilterConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(path: str | Path = "config.yaml") -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found - run `python -m jobbot init` first")
    return Config(**_load_yaml(p))


def load_profile(path: str | Path) -> Profile:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found - copy profile.example.yaml to profile.yaml and edit it")
    return Profile(**_load_yaml(p))
