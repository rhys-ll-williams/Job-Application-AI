"""Cheap, deterministic anti-hallucination check for LLM-written text.

A 4B model happily invents a tool, an employer or a percentage. This flags any
number or mid-sentence proper noun / technology name in the output that does not
appear in the source material (the candidate profile plus the job posting).
It is deliberately conservative: a rejected draft is regenerated, never edited.
"""
from __future__ import annotations

import re

from .forms.text import norm

_NUM = re.compile(r"£?\d[\d,]*(?:\.\d+)?%?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./&'’-]*")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
# capitalised words that are fine without a source
_ALLOWED = {"british", "english", "uk", "united", "kingdom", "england", "scotland", "wales", "i", "im", "ive", "id", "ill",
            "monday", "friday", "dear", "sincerely", "regards", "yours", "faithfully", "thank", "thanks", "hiring", "manager",
            "team", "ltd", "limited", "plc", "cv", "hr"}


def _vocab(*sources: str) -> set[str]:
    return set(norm(" ".join(sources)).split())


def ungrounded(text: str, *sources: str) -> list[str]:
    """Return the suspicious tokens in `text` (empty list = looks grounded)."""
    vocab = _vocab(*sources)
    src_blob = " ".join(sources)
    bad: list[str] = []
    for m in _NUM.finditer(text):
        tok = m.group().strip(".,")
        digits = re.sub(r"[£,%]", "", tok)
        if digits and digits not in src_blob.replace(",", ""):
            bad.append(tok)
    for sent in _SENT.split(text):
        words = _WORD.findall(sent)
        for w in words[1:]:  # skip the sentence-initial word
            if not w[0].isupper():
                continue
            n = norm(w.strip(".,'’"))
            if not n or n in _ALLOWED:
                continue
            base = n[:-1] if n.endswith("s") and len(n) > 3 else n  # crude plural / possessive
            if n not in vocab and base not in vocab:
                bad.append(w)
    return sorted(set(bad))
