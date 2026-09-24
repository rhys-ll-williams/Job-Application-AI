"""Key used to remember answers by question text."""
from .text import norm


def norm_key(question: str) -> str:
    return norm(question)
