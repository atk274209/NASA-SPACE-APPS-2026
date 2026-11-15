"""AM companion persona (master prompt Module O): loader, prompt section, opener, one-question check.

The persona file (config/persona/am_companion.v1.yaml) sets style only; the safety rules in
am.ai.companion.SYSTEM_PROMPT always come first and win.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from am.config import REPO_ROOT

PERSONA_PATH = REPO_ROOT / "config" / "persona" / "am_companion.v1.yaml"


@dataclass(frozen=True)
class Persona:
    version: str
    data: dict[str, Any]

    def prompt_section(self) -> str:
        d = self.data
        lines = [f"PERSONA ({self.version}). Style only: every rule above always wins.", d["identity"].strip(), "",
                 "Tone:"] + [f"- {x}" for x in d["tone"]] + ["", "Conversation:"] + [f"- {x}" for x in d["conversation"]]
        if d.get("support"):
            lines += ["", "Support and motivation:"] + [f"- {x}" for x in d["support"]]
        lines += ["", "Examples of the style (not facts about this astronaut):"]
        for ex in d.get("examples", []):
            lines.append(f'- Astronaut: "{ex["astronaut"]}" / AM: "{ex["am"]}"')
        return "\n".join(lines)

    def opener(self, hour: int, lang: str, seed: int) -> str:
        part = "morning" if 5 <= hour < 11 else "day" if 11 <= hour < 17 else "evening" if 17 <= hour < 22 else "night"
        options = self.data["openers"][part][lang if lang in ("en", "tr") else "en"]
        return options[seed % len(options)]


@lru_cache
def load_persona(path: Path = PERSONA_PATH) -> Persona:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("version", "identity", "tone", "conversation", "openers"):
        if key not in data:
            raise ValueError(f"persona file {path} is missing '{key}'")
    return Persona(version=data["version"], data=data)


_SENTENCE = re.compile(r"[^.!?…\n]+[.!?…]*|\n")


def limit_questions(text: str, max_questions: int = 1) -> tuple[str, int]:
    """Keep at most `max_questions` question sentences (persona: one open question at a time).

    Statements are kept; only extra question sentences after the first are dropped. Returns the
    text and how many questions were removed.
    """
    parts = _SENTENCE.findall(text)
    kept, asked, removed = [], 0, 0
    for p in parts:
        if p.strip().endswith("?"):
            if asked >= max_questions:
                removed += 1
                continue
            asked += 1
        kept.append(p)
    if not removed:
        return text, 0
    out = re.sub(r"[ \t]+\n", "\n", "".join(kept)).strip()
    return out, removed
