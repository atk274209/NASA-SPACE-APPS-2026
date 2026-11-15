"""Comfort signals the astronaut states in conversation (English and Turkish), deterministic.

Only what was said: "I'm cold" -> temperature up; "the light is too bright" -> lighting down.
Ear or pressure complaints never become an adjustment: they are reported for a person to review
(pressure is cabin-wide and safety-critical). A negated sentence ("I'm not cold") is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from am.symptoms.detect import _ascii, sentence_at


@dataclass(frozen=True)
class ComfortHit:
    variable: str
    direction: str  # up | down | report
    quote: str


# The place words: a room-temperature or lighting complaint names the room, the air or the light.
_ROOM_EN = r"(?:room|cabin|quarters|module|in here|it|air)"
_ROOM_TR = r"(?:oda\w*|burasi|burada|ortam\w*|kabin\w*|hava\w*|icerisi|iceri)"
_MUCH_EN = r"(?:(?:so|too|really|very|a bit|kind of|pretty|quite|way too)\s+)?"
_MUCH_TR = r"(?:(?:cok|biraz|fazla|asiri|baya\w*|epey|gercekten)\s+)?"
_LIGHT_EN = r"(?:lights?|lighting|lamps?|screen light)"
_LIGHT_TR = r"(?:isik\w*|lamba\w*|aydinlatma\w*)"

_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("temperature", "up", re.compile(
        r"\b(i'?m cold|feel(?:ing)? cold|too cold|too cool|freezing|chilly|it'?s cold|cold in here|"
        rf"{_ROOM_EN}\s+(?:is|feels|got|gets)\s+{_MUCH_EN}(?:cold|cool|freezing|chilly)|"
        r"turn up the heat|heat (?:it|the room) up|warm (?:it|the room) up|warmer,? please|"
        r"usu\w*|soguk(?!\s+(?:su|icecek|cay|kahve|yemek|sut|ayran))|serin geldi|"
        r"(?:odayi|ortami|kabini)\s+isit\w*|isiyi\s+(?:art|yukselt|ac)\w*)")),
    ("temperature", "down", re.compile(
        r"\b(too hot|i'?m hot|feel(?:ing)? hot|it'?s hot|too warm|hot in here|warm in here|sweating|stuffy|"
        rf"{_ROOM_EN}\s+(?:is|feels|got|gets)\s+{_MUCH_EN}(?:hot|warm|stuffy|muggy|boiling)|"
        r"cool (?:it|the room|things) down|cooler,? please|turn down the heat|"
        rf"cok sicak|sicak geldi|sicakladim|{_ROOM_TR}\s+{_MUCH_TR}sicak\b|"
        r"bunal\w*|terl\w*|bogucu|havasiz|"
        r"(?:odayi|ortami|kabini)\s+sogut\w*|isiyi\s+(?:dusur|azalt|kis)\w*)")),
    ("lighting", "down", re.compile(
        r"\b(too bright|light is bright|bright light|glare|too much light|"
        rf"{_LIGHT_EN}\s+(?:is|are|feels?)\s+{_MUCH_EN}(?:bright|harsh|strong|intense|glaring)|"
        r"(?:light|brightness)\s+hurts?\s+my eyes|hurts? my eyes|"
        rf"(?:dim|lower|turn down|soften)\s+the\s+{_LIGHT_EN}|"
        r"isik cok|cok parlak|parlak geldi|cok aydinlik|asiri aydinlik|"
        rf"{_LIGHT_TR}\s+{_MUCH_TR}(?:parlak|guclu|sert|fazla|keskin|yogun)|"
        r"gozumu\s+(?:al|yak|yor|ac)\w*|gozlerimi\s+(?:yak|yor|ac)\w*|isik rahatsiz\w*|"
        r"(?:isigi|lambayi|aydinlatmayi)\s+(?:kis|azalt|dusur)\w*)")),
    ("lighting", "up", re.compile(
        r"\b(too dark|too dim|light is dim|can'?t see well|dark in here|dim in here|"
        rf"{_LIGHT_EN}\s+(?:is|are|feels?)\s+{_MUCH_EN}(?:dim|weak|low|dark)|"
        rf"{_ROOM_EN}\s+(?:is|feels)\s+{_MUCH_EN}(?:dark|dim|gloomy)|"
        rf"(?:brighten|turn up|raise)\s+the\s+{_LIGHT_EN}|more light|brighter,? please|"
        r"cok karanlik|karanlik geldi|los\b|isik az|"
        rf"{_ROOM_TR}\s+{_MUCH_TR}(?:karanlik|los)\b|"
        rf"{_LIGHT_TR}\s+{_MUCH_TR}(?:az|yetersiz|zayif|dusuk|soluk)\b|"
        r"(?:isigi|lambayi|aydinlatmayi)\s+(?:ac|art|yukselt)\w*)")),
    ("pressure", "report", re.compile(r"\b(ears? (?:feel|hurt|pop|blocked|strange)|ear pressure|pressure in my ears|"
                                       r"kulak\w*\s?(?:tika|agri|basinc|garip|tuhaf)\w*|basinc\w*)")),
]
_NEG_EN = re.compile(r"\b(not|no|isn'?t|aren'?t|don'?t|never)\b(?:\W+\w+){0,2}\W*$")
_NEG_TR = re.compile(r"^\w*\W+(degil\w*)")
# Turkish negative verb forms inside the matched word: usu-mu-yorum, usu-me-dim, sicakla-ma-dim
_NEG_TR_VERB = re.compile(r"m[iu]yor|m[ae]d[iu]m|m[ae]z")


def extract_comfort(text: str) -> list[ComfortHit]:
    folded = _ascii(text).lower()  # same length as text: positions map back
    out: list[ComfortHit] = []
    seen: set[str] = set()
    for variable, direction, pat in _RULES:
        for m in pat.finditer(folded):
            before, after = folded[max(0, m.start() - 30):m.start()], folded[m.end():m.end() + 20]
            word_end = re.match(r"\w*", folded[m.end():]).end()
            word = folded[m.start():m.end() + word_end]
            if _NEG_EN.search(before) or _NEG_TR.search(after) or _NEG_TR_VERB.search(word):
                continue
            if variable in seen:
                continue
            seen.add(variable)
            out.append(ComfortHit(variable, direction, sentence_at(text, m.start(), m.end())))
    return out
