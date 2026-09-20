"""Infer the job function from title + skills.

The create form has no Function field (prototypes/screen-06-create-job.html `FN`),
so the server derives it the same way the prototype does.
"""

import re

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("finance", re.compile(r"financ|account|treasur|fp&a|cfo|audit", re.I)),
    ("engineering", re.compile(r"engineer|developer|backend|frontend|devops|qa\b|sre", re.I)),
    ("data", re.compile(r"data|analyst|analytics", re.I)),
    ("design", re.compile(r"design|ux|ui\b", re.I)),
    ("legal", re.compile(r"legal|complian|counsel", re.I)),
    ("marketing", re.compile(r"market|growth|brand", re.I)),
    ("sales", re.compile(r"sales|business develop|country manager|account exec", re.I)),
    ("operations", re.compile(r"operation|logistic|supply", re.I)),
]


def guess_function(title: str, skills: list[str] | None = None) -> str:
    haystack = f"{title} {' '.join(skills or [])}"
    for value, pattern in RULES:
        if pattern.search(haystack):
            return value
    return "other"
