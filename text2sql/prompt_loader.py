"""Load named prompts from prompts/prompts.md (parsed once and cached)."""
from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_FILE = Path(__file__).parent.parent / "prompts" / "prompts.md"
_cache: dict[str, str] | None = None


def _load_all() -> dict[str, str]:
    global _cache
    if _cache is None:
        text = _PROMPTS_FILE.read_text(encoding="utf-8")
        sections = re.split(r"^## ", text, flags=re.MULTILINE)
        _cache = {}
        for section in sections[1:]:  # skip preamble before first ##
            name, _, body = section.partition("\n")
            _cache[name.strip()] = body.strip()
    return _cache


def load_prompt(name: str) -> str:
    prompts = _load_all()
    if name not in prompts:
        raise KeyError(f"Prompt '{name}' not found in {_PROMPTS_FILE}")
    return prompts[name]
