"""Symptom detection in chat messages. Output is only ever a *proposal*.

AM never records a symptom from chat by itself: every detection becomes a pending
SymptomProposal that the astronaut confirms or dismisses. Detection therefore favours recall,
but it must not propose what the astronaut explicitly denies ("I don't have a headache",
"başım ağrımıyor"), so negation is handled deterministically.

Keyword rules (deterministic, no model needed):
  * Catalog synonyms are word-initial stems: "constipat" matches "constipated", "kabız"
    matches "kabızlık". A stem must start at a word boundary ("rash" never matches "crash").
  * Matching ignores case, including Turkish dotted/dotless i. Turkish letters in a stem also
    match their ASCII look-alikes because Turkish is often typed without diacritics
    ("basim agriyor"). The reverse is deliberately not true, so "sınırlı" (limited) never
    matches the stem "sinirli" (irritable).
  * English stems are negated by a negator within EN_NEGATION_WINDOW words before the match,
    in the same clause ("no headache", "I don't have a headache"; but not "no headache, but
    I feel nauseous" for the nausea).
  * Turkish stems are negated by a negative verb suffix in the rest of the matched word
    ("ağrımıyor", "kusmadım"), or by "yok"/"değil" or a negated "ol-" auxiliary within
    TR_NEGATION_WINDOW words after ("ateşim yok", "kabız değilim", "kabız olmadım").
  * The quote is the sentence that contains the match, cut from the message, so it is always
    an exact substring.

Optional LLM detection asks the local model for catalog codes plus a verbatim quote; quotes
that are not exact substrings of the message, unknown codes, and matches the keyword rules
consider denied are dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from am.ai.ollama import OllamaClient
from am.symptoms.catalog import BY_CODE, SYMPTOMS

EN_NEGATION_WINDOW = 3  # words before an English match (spec: "within 3 words before")
TR_NEGATION_WINDOW = 2  # words after a Turkish match (spec: "within 2 words after")
MAX_QUOTE_CHARS = 300  # a run-on "sentence" is cut to a window around the match
MIN_LLM_QUOTE_CHARS = 3  # an LLM quote shorter than this cannot show what was said
MAX_LLM_PROPOSALS = 5

# Capital "I" is ambiguous: English "i" or Turkish "ı". It folds to this placeholder, which
# stem patterns accept for both letters.
_AMBIGUOUS_I = ""
_APOSTROPHES = "’‘`´"

# Stem letter -> what it may match in the folded message (see the module docstring).
_STEM_CHAR = {
    "ı": f"[ıi{_AMBIGUOUS_I}]",
    "i": f"[i{_AMBIGUOUS_I}]",
    "ş": "[şs]",
    "ğ": "[ğg]",
    "ç": "[çc]",
    "ö": "[öo]",
    "ü": "[üu]",
    "'": "'?",
    " ": r"\s+",
    "-": r"[-\s]?",
}

_EN_NEGATORS = frozenset({
    "no", "not", "never", "without", "nor",
    "don't", "dont", "didn't", "didnt", "doesn't", "doesnt",
    "haven't", "havent", "hasn't", "hasnt", "isn't", "isnt", "wasn't", "wasnt", "aren't", "arent",
})
# A contrast word starts a new clause: negation before it does not reach past it.
_CLAUSE_BREAKERS = frozenset({"but", "however", "although", "though", "except", "ama", "fakat", "ancak", "lakin"})
_CLAUSE_CHARS = ".!?;:\n…"

# Turkish negative verb suffixes, ASCII-folded. The spec's set (mıyor, miyor, muyor, müyor,
# madı, medi, maz, mez) plus the spoken "-mıyo" form and the evidential/future negatives.
_TR_NEG_SUFFIXES = ("miyo", "muyo", "madi", "medi", "maz", "mez", "mami", "memi", "mayac", "meyec")
# Stems that end in the verbal noun -ma/-me ("kusma", "titreme") share those letters with the
# negative suffix, so "kusmadım" is "kus-ma-dı-m": check the stem's last two letters + the rest.
_TR_NEG_AFTER_MA = tuple(s for s in _TR_NEG_SUFFIXES if s.startswith(("ma", "me")))

# Whole words that contain a stem but mean something else.
_FALSE_FRIENDS = frozenset({"yalnizca"})  # Turkish "only", contains the loneliness stem "yalnız"

# Ways of asking AM what to do about a symptom (ASCII-folded, lower case). In tests with the
# local 7B model, a direct "what should I do?" drew self-care advice whatever the prompt said,
# so the companion answers these with fixed wording instead (see CompanionService).
ADVICE_REQUEST_PHRASES = (
    "what should i do", "what can i do", "what do i do", "what should i take", "what can i take",
    "should i take", "how do i treat", "how can i treat", "how to treat", "how do i get rid",
    "how can i get rid", "how do i stop", "how can i stop", "what helps", "what would help", "any advice",
    "any tips", "any remedy", "any remedies", "home remedy", "what medicine", "what medication",
    "which medicine", "which medication",
    "ne yapmaliyim", "ne yapayim", "ne yapabilirim", "ne yapmam lazim", "ne yapmam gerek", "ne yapsam",
    "nasil gecer", "nasil gecirebilirim", "nasil tedavi", "ne iyi gelir", "ne icmeliyim", "ne almaliyim",
    "hangi ilac", "ilac alayim", "ilac almali", "tavsiye", "oneri",
)

_WORD = re.compile(rf"[\w{_AMBIGUOUS_I}']+")
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)|\n+")
_ASCII = str.maketrans({"ı": "i", "İ": "i", "I": "i", _AMBIGUOUS_I: "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                        "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
                        **{a: "'" for a in _APOSTROPHES}})


@dataclass(frozen=True)
class Detection:
    """A symptom code with the exact text of the message that suggested it."""

    code: str
    quote: str


@dataclass(frozen=True)
class StemHit:
    code: str
    stem: str
    language: str  # en | tr
    start: int
    end: int
    negated: bool


def _fold(text: str) -> str:
    """Lower-case `text` one character for one character, so match offsets stay valid."""
    out = []
    for c in text:
        if c == "I":
            out.append(_AMBIGUOUS_I)
        elif c == "İ":
            out.append("i")
        elif c in _APOSTROPHES:
            out.append("'")
        else:
            low = c.lower()
            out.append(low if len(low) == 1 else c)
    return "".join(out)


def _ascii(text: str) -> str:
    """Fold Turkish letters to ASCII; used only to compare negation words and suffixes."""
    return text.translate(_ASCII).lower()


def _stem_regex(stem: str) -> re.Pattern[str]:
    body = "".join(_STEM_CHAR.get(c, re.escape(c)) for c in _fold(stem))
    return re.compile(rf"(?<![\w{_AMBIGUOUS_I}]){body}")


_STEMS: list[tuple[str, str, str, re.Pattern[str]]] = [
    (s.code, stem, lang, _stem_regex(stem))
    for s in SYMPTOMS
    for lang, stems in (("en", s.synonyms_en), ("tr", s.synonyms_tr))
    for stem in stems
]


def _is_word_char(c: str) -> bool:
    return c.isalnum() or c in (_AMBIGUOUS_I, "'")


def _word_end(folded: str, pos: int) -> int:
    while pos < len(folded) and _is_word_char(folded[pos]):
        pos += 1
    return pos


def _clause_start(folded: str, pos: int) -> int:
    return max(folded.rfind(c, 0, pos) for c in _CLAUSE_CHARS) + 1


def _clause_end(folded: str, pos: int) -> int:
    ends = [i for i in (folded.find(c, pos) for c in _CLAUSE_CHARS) if i != -1]
    return min(ends) if ends else len(folded)


def _negated_en(folded: str, start: int) -> bool:
    before = _WORD.findall(folded[_clause_start(folded, start):start])
    for word in reversed(before[-EN_NEGATION_WINDOW:]):
        w = _ascii(word)
        if w in _CLAUSE_BREAKERS:
            return False
        if w in _EN_NEGATORS:
            return True
    return False


def _negated_tr(folded: str, start: int, end: int) -> bool:
    word_end = _word_end(folded, end)
    rest = _ascii(folded[end:word_end])
    if any(s in rest for s in _TR_NEG_SUFFIXES):
        return True
    tail = _ascii(folded[start:end])[-2:]
    if tail in ("ma", "me") and (tail + rest).startswith(_TR_NEG_AFTER_MA):
        return True
    after = _WORD.findall(folded[word_end:_clause_end(folded, word_end)])
    for word in after[:TR_NEGATION_WINDOW]:
        w = _ascii(word)
        if w in _CLAUSE_BREAKERS:
            return False
        if w.startswith(("yok", "degil")):
            return True
        if w.startswith("ol") and any(s in w for s in _TR_NEG_SUFFIXES):  # "kabız olmadım"
            return True
    return False


def find_stems(message: str) -> list[StemHit]:
    """Every catalog stem found in `message`, in order, each marked negated or not."""
    folded = _fold(message)
    hits: list[StemHit] = []
    for code, stem, lang, rx in _STEMS:
        for m in rx.finditer(folded):
            if _ascii(folded[m.start():_word_end(folded, m.end())]) in _FALSE_FRIENDS:
                continue
            negated = _negated_en(folded, m.start()) if lang == "en" else _negated_tr(folded, m.start(), m.end())
            hits.append(StemHit(code, stem, lang, m.start(), m.end(), negated))
    return sorted(hits, key=lambda h: (h.start, h.code))


def sentence_at(message: str, start: int, end: int) -> str:
    """The sentence of `message` that contains [start, end), as an exact substring."""
    s, e = 0, len(message)
    for b in _SENTENCE_END.finditer(message):
        if b.end() <= start:
            s = b.end()
        elif b.start() >= end:
            e = b.start() if b.group().startswith("\n") else b.end()
            break
    if e - s > MAX_QUOTE_CHARS:  # keep a window around the match; still a slice of the message
        s = max(s, min(start - (MAX_QUOTE_CHARS - (end - start)) // 2, e - MAX_QUOTE_CHARS))
        e = min(e, s + MAX_QUOTE_CHARS)
    while s < e and message[s].isspace():
        s += 1
    while e > s and message[e - 1].isspace():
        e -= 1
    return message[s:e]


def asks_for_advice(message: str) -> bool:
    """True when the message asks what to do (treatment, remedies, self-care), in English or Turkish."""
    text = " ".join(_ascii(message).split())
    return any(p in text for p in ADVICE_REQUEST_PHRASES)


def detect_keywords(message: str) -> list[Detection]:
    """One detection per symptom code: its first match that is not negated."""
    out: list[Detection] = []
    seen: set[str] = set()
    for h in find_stems(message):
        if h.negated or h.code in seen:
            continue
        seen.add(h.code)
        out.append(Detection(h.code, sentence_at(message, h.start, h.end)))
    return out


# ---------------------------------------------------------------- optional LLM detection
def _llm_system_prompt() -> str:
    codes = "\n".join(f"  {s.code}: {s.label}" for s in SYMPTOMS)
    return (
        "You read ONE chat message from an astronaut and list the symptoms they say they have now.\n"
        'Answer only with JSON: {"symptoms": [{"code": "<code>", "quote": "<words copied from the message>"}]}\n'
        "Rules:\n"
        f"- Use only these codes:\n{codes}\n"
        '- "quote" must be copied exactly, character for character, from the message.\n'
        "- Leave out symptoms the astronaut denies (\"I don't have a headache\", \"başım ağrımıyor\"), asks about in "
        "general, mentions as a possibility, or attributes to someone else.\n"
        '- If there is no symptom, answer {"symptoms": []}.\n'
        "- Do not diagnose and do not add advice or any other text."
    )


def _llm_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"symptoms": {"type": "array", "items": {
            "type": "object",
            "properties": {"code": {"type": "string", "enum": [s.code for s in SYMPTOMS]},
                           "quote": {"type": "string"}},
            "required": ["code", "quote"]}}},
        "required": ["symptoms"],
    }


def _denied_in_message(message: str, code: str, quote: str) -> bool:
    """True when the quote covers keyword matches for `code` and every one of them is negated.

    A paraphrase without any catalog stem cannot be checked this way and is kept: it is only
    a proposal that the astronaut confirms or dismisses.
    """
    q0 = message.find(quote)
    inside = [h for h in find_stems(message) if h.code == code and q0 <= h.start < q0 + len(quote)]
    return bool(inside) and all(h.negated for h in inside)


def parse_llm_reply(message: str, content: str) -> list[Detection]:
    """Validate the model's structured reply; anything not verifiable against the message is dropped."""
    try:
        data = json.loads(content)
    except ValueError:
        return []
    items = data.get("symptoms") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[Detection] = []
    seen: set[str] = set()
    for item in items:
        if len(out) >= MAX_LLM_PROPOSALS:
            break
        if not isinstance(item, dict):
            continue
        code, quote = item.get("code"), item.get("quote")
        if not isinstance(code, str) or code not in BY_CODE or code in seen or not isinstance(quote, str):
            continue
        quote = quote.strip()
        if len(quote) < MIN_LLM_QUOTE_CHARS or quote not in message:  # must be verbatim
            continue
        if _denied_in_message(message, code, quote):
            continue
        seen.add(code)
        out.append(Detection(code, quote))
    return out


def detect_with_llm(llm: OllamaClient, model: str, message: str) -> list[Detection]:
    """Ask the local model for symptoms in one message. Raises LLMUnavailable like any chat call.

    Uses the client's default context size, the same as the companion chat: Ollama reloads
    the model whenever num_ctx changes, which would add seconds to every turn.
    """
    reply = llm.chat(model, [{"role": "system", "content": _llm_system_prompt()},
                             {"role": "user", "content": message}],
                     temperature=0.0, format=_llm_schema())
    return parse_llm_reply(message, reply.content)
