"""Text normalisation and option matching shared by all answer strategies."""
from __future__ import annotations

import re

from rapidfuzz import fuzz, process

# Phrases forms use for "I'd rather not say" - matched against option text.
PNTS_RE = re.compile(
    r"prefer not|rather not|decline|do not wish|don'?t wish|not to (say|disclose|answer|state|specify)|"
    r"choose not|no answer|not disclose|withhold|i do not want|would not like|wish not",
    re.I,
)
_PLACEHOLDER_RE = re.compile(r"^(select|choose|please select|--|—|please choose|pick)\b", re.I)


def norm(s: str) -> str:
    s = (s or "").lower().replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9+ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_placeholder(text: str) -> bool:
    t = text.strip()
    return not t or bool(_PLACEHOLDER_RE.match(t))


def _yes_no(answer: str, options: list[str]) -> str | None:
    a = norm(answer)
    if a not in {"yes", "no", "true", "false"}:
        return None
    want_yes = a in {"yes", "true"}
    pat = re.compile(r"^(yes|i do|i am|i have|i can|i will|true|agree)\b" if want_yes
                     else r"^(no|i do not|i dont|i am not|i have not|i cannot|i wont|false)\b", re.I)
    hits = [o for o in options if pat.match(norm(o))]
    if len(hits) == 1:
        return hits[0]
    if hits:  # e.g. "Yes, I require sponsorship" vs "Yes" -> prefer the shortest
        return min(hits, key=len)
    return None


def best_option(answer: str, options: list[str], threshold: int = 72) -> str | None:
    """Pick the option that best expresses `answer`, or None if nothing is close enough.

    `answer` may be free text, 'yes'/'no', or the token 'prefer_not_to_say'.
    """
    cands = [o for o in options if not is_placeholder(o)]
    if not cands or not answer:
        return None
    a = norm(answer)

    if a in {"prefer not to say", "prefer not"} or answer == "prefer_not_to_say":
        hits = [o for o in cands if PNTS_RE.search(o)]
        return hits[0] if hits else None

    for o in cands:  # exact
        if norm(o) == a:
            return o
    yn = _yes_no(answer, cands)
    if yn:
        return yn
    contained = [o for o in cands if a and (a in norm(o) or (len(norm(o)) > 2 and norm(o) in a))]
    if len(contained) == 1:
        return contained[0]
    pool = contained or cands
    match = process.extractOne(a, [norm(o) for o in pool], scorer=fuzz.token_set_ratio)
    if match and match[1] >= threshold:
        return pool[match[2]]
    return None


def looks_like_pnts_option(text: str) -> bool:
    return bool(PNTS_RE.search(text))


def _numbers(s: str) -> list[float]:
    """Numbers in a string, expanding 30k -> 30000 and ignoring thousands commas."""
    out = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", s.lower()):
        v = float(m.group(1).replace(",", ""))
        out.append(v * 1000 if m.group(2) else v)
    return out


def pick_numeric_band(value: float, options: list[str]) -> str | None:
    """Choose the option whose range contains `value` ('£30,000 - £35,000', '3-5 years', '10+', 'under 1')."""
    for o in options:
        nums = _numbers(o)
        low = o.lower()
        if len(nums) >= 2 and nums[0] <= value <= nums[1]:
            return o
        if len(nums) == 1:
            n = nums[0]
            if re.search(r"\+|or more|and (above|over)|over|more than|at least|above", low) and value >= n:
                return o
            if re.search(r"under|less than|up to|below|fewer|max", low) and value <= n:
                return o
    return None
