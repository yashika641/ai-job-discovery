"""Word-boundary-aware substring matching, shared by ranking.py (skill
keyword matching), pipeline.py (location marker matching), and
filtering.py (exclude-keyword matching).

A plain `term in text` check false-positives inside unrelated words —
"rag" inside "average"/"storage", "gpt" inside "chatgpt", "sales" inside
"salesforce", "us" inside "bonus". This anchors matches to word boundaries
instead, with an optional trailing "s" so simple plurals (e.g.
"agent"/"agents") still match.
"""

from __future__ import annotations

import re

_pattern_cache: dict[str, re.Pattern[str]] = {}


def _pattern_for(term: str) -> re.Pattern[str]:
    pattern = _pattern_cache.get(term)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(term.strip())}s?\b", re.IGNORECASE)
        _pattern_cache[term] = pattern
    return pattern


def contains_term(term: str, text: str) -> bool:
    """Whether `term` appears in `text` as a whole word (or phrase, for
    multi-word terms), plus an optional trailing "s", case-insensitively."""
    if not term.strip():
        return False
    return _pattern_for(term).search(text) is not None
