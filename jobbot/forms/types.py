"""Shared types for the answer engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import ApplyConfig, Profile
from ..llm import LLMClient


@dataclass
class JobInfo:
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    url: str = ""


@dataclass
class Documents:
    cv_path: str = ""
    cover_path: str = ""
    cover_text: str = ""
    cv_uploaded: bool = False  # set by the agent once the CV has gone into a file input


@dataclass
class Answer:
    """What to put in a field. value=None means 'leave it alone'."""

    value: str | list[str] | bool | None
    source: str  # profile | demographic | bank | memory | llm | none
    confident: bool = True
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.value is not None and self.value != ""


@dataclass
class AnswerContext:
    profile: Profile
    job: JobInfo
    docs: Documents
    llm: LLMClient | None
    apply_cfg: ApplyConfig = field(default_factory=ApplyConfig)
    db: Any = None  # jobbot.db.DB (kept loose to avoid an import cycle in tests)
