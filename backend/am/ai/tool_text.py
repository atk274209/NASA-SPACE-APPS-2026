"""Small local models sometimes write a tool call as text, or ask the astronaut to "call the X tool".

The astronaut must never be asked to run a tool: tools are AMC's to call. This module finds both
patterns so the companion can (a) run a tool call written as JSON text itself, (b) tell the model to
call the tool properly and try again, or (c) fall back to a deterministic status summary.
"""

from __future__ import annotations

import json
import re
from typing import Any

NUDGE = ("SYSTEM CORRECTION: Do not ask the astronaut to call, run or use any tool, and do not mention tool "
         "names to them. The tools are yours: call them yourself through the tool interface (for example "
         "get_health_overview for the latest heart rate and blood pressure), then answer the astronaut in plain "
         "words. If no tool fits, answer from the data you already have.")

_ASK_PATTERNS = [
    re.compile(r"\b(?:call|run|use|invoke|execute|trigger)\s+(?:the\s+)?[\"'`“”]?[a-z]+(?:_[a-z]+)+[\"'`“”]?", re.I),
    re.compile(r"\b(?:call|run|use|invoke|execute)\s+(?:the\s+|a\s+|this\s+|that\s+)?[\"'`“”]?[\w ]{0,40}[\"'`“”]?\s+"
               r"(?:tool|function)\b", re.I),
    re.compile(r"\b(?:tool|function)\s+(?:call\s+)?for\s+me\b", re.I),
    re.compile(r"\bget_[a-z_]+\b"),  # a function name has no place in a reply to the astronaut
    re.compile(r"(?:arac\w*|fonksiyon\w*)\s+(?:çağır|cagir|çalıştır|calistir)\w*", re.I),
]


def asks_user_to_run_tool(text: str) -> bool:
    return any(p.search(text or "") for p in _ASK_PATTERNS)


def _json_objects(text: str) -> list[Any]:
    """Every top-level JSON object found in the text (fenced or bare)."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    out.append(json.loads(text[start:i + 1]))
                except ValueError:
                    pass
                start = None
    return out


def parse_text_tool_calls(text: str, known: set[str]) -> list[dict[str, Any]]:
    """Tool calls written as JSON text, e.g. {"name": "get_alerts", "parameters": {...}}. Known tools only."""
    calls = []
    for obj in _json_objects(text or ""):
        if not isinstance(obj, dict):
            continue
        fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
        name = fn.get("name")
        args = fn.get("arguments", fn.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if name in known and isinstance(args, dict):
            calls.append({"name": name, "arguments": args})
    return calls
