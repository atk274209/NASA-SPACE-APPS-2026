"""Fixed, deterministic companion texts for safety situations, in English and Turkish.

The companion answers in the language the astronaut writes in (master prompt Module L/O). These
texts never come from the model: medication or treatment questions (review finding R02) and symptoms
on the DEMO report-promptly list, including self-harm (R04, R06), always get these words.
No diagnosis, no treatment, no doses, no durations.
"""

from __future__ import annotations

import re

_TR_CHARS = re.compile(r"[çğışöüÇĞİŞÖÜ]")
_TR_WORDS = frozenset((
    "ve", "bir", "bu", "çok", "cok", "var", "yok", "ben", "benim", "değil", "degil", "nasıl", "nasil", "ne",
    "için", "icin", "bugün", "bugun", "biraz", "gibi", "ama", "şu", "su", "mı", "mi", "mu", "mü", "da", "de",
    "merhaba", "selam", "evet", "hayır", "hayir", "teşekkürler", "tesekkurler", "iyiyim", "kötü", "kotu",
))


_TR_SUFFIX = re.compile(r"(iyor|ıyor|uyor|üyor|yorum|yoruz|mek|mak|miyorum|mıyorum|dim|dım|dum|düm|tim|tım"
                        r"|sin|sın|ım|im|um|üm)$")


def detect_language(text: str) -> str:
    """'tr' or 'en'. A simple, deterministic guess: Turkish letters, common Turkish words, or at least
    two words with Turkish verb/person endings (Turkish typed without Turkish letters)."""
    if _TR_CHARS.search(text):
        return "tr"
    words = re.findall(r"\w+", text.lower())
    if sum(w in _TR_WORDS for w in words) >= 1 and len(words) <= 40:
        return "tr"
    return "tr" if sum(len(w) > 4 and bool(_TR_SUFFIX.search(w)) for w in words) >= 2 else "en"


CONTACT_NOW = {
    "en": "{label} is on the DEMO report-promptly list: contact the crew medical officer or flight surgeon now, "
          "using the mission's medical contact procedure.",
    "tr": "{label}, DEMO 'hemen bildir' listesinde: görev tıbbi iletişim prosedürünü kullanarak ekip sağlık "
          "görevlisine veya uçuş cerrahına şimdi ulaş.",
}

LABELS_TR = {
    "chest_pain": "Göğüs ağrısı veya sıkışması", "shortness_of_breath": "Nefes darlığı",
    "fainting": "Bayılma veya bayılacak gibi olma", "vision_change": "Görme değişikliği",
    "self_harm_thoughts": "Kendine zarar verme veya intihar düşünceleri",
    "constipation": "Kabızlık", "diarrhea": "İshal", "nausea": "Bulantı", "vomiting": "Kusma",
    "abdominal_pain": "Karın ağrısı", "loss_of_appetite": "İştahsızlık", "headache": "Baş ağrısı",
    "back_pain": "Sırt ağrısı", "muscle_soreness": "Kas ağrısı", "nasal_congestion": "Burun tıkanıklığı",
    "insomnia": "Uyku güçlüğü", "fatigue": "Yorgunluk", "fever": "Ateşli hissetme",
    "skin_irritation": "Ciltte tahriş veya döküntü", "eye_irritation": "Gözde tahriş veya kuruluk",
    "dizziness": "Baş dönmesi", "low_mood": "Moral düşüklüğü", "anxiety": "Kaygı",
    "irritability": "Gerginlik veya ekip içi sürtüşme", "loneliness": "Yalnızlık hissi",
}


def label(code: str, english_label: str, lang: str) -> str:
    return LABELS_TR.get(code, english_label) if lang == "tr" else english_label


def safety_reply(lang: str, items: list[tuple[str, str]], awaiting: bool) -> str:
    """Safety shift (Module O): plain, direct, no casual tone. items: (code, English label)."""
    self_harm = any(code == "self_harm_thoughts" for code, _ in items)
    if lang == "tr":
        lines = ["Bunu ciddiye alıyorum ve açıkça söylüyorum:"]
        if self_harm:
            lines.append("Kendine zarar vermeyi düşünüyorsan yalnız değilsin. Lütfen şimdi ekip sağlık görevlisine "
                         "veya uçuş cerrahına ulaş; mümkünse bir ekip arkadaşına da hemen yanına gelmesini söyle.")
        for code, eng in items:
            lines.append("- " + CONTACT_NOW["tr"].format(label=label(code, eng, "tr")))
        if awaiting:
            lines.append("- Yazdıkların uçuş cerrahına hemen iletildi ve doktor onayı bekliyor.")
        lines.append("- Kaydedilmesi için semptomu Companion'da onayla. AMC teşhis koymaz ve tedavi önermez.")
        return "\n".join(lines)
    lines = ["I'm taking this seriously, so I'll be direct:"]
    if self_harm:
        lines.append("If you are thinking about harming yourself, you are not alone. Please contact the crew medical "
                     "officer or flight surgeon now, and if you can, ask a crewmate to be with you.")
    for code, eng in items:
        lines.append("- " + CONTACT_NOW["en"].format(label=eng))
    if awaiting:
        lines.append("- What you wrote went to the flight surgeon at once and is waiting for the doctor's approval.")
    lines.append("- Confirm the symptom in the companion so it is recorded. AMC does not diagnose or suggest treatment.")
    return "\n".join(lines)


NO_ADVICE_INTRO = {
    "en": "AMC does not give treatment or self-care advice, and it does not guess what causes a symptom.",
    "tr": "AMC tedavi, ilaç veya kendi kendine bakım tavsiyesi vermez ve bir semptomun nedenini tahmin etmez.",
}
NO_ADVICE_WHO = {
    "en": "- For advice, talk to the crew medical officer or flight surgeon at your next private medical conference, "
          "or sooner if it gets worse.",
    "tr": "- Tavsiye için bir sonraki özel tıbbi görüşmede ekip sağlık görevlisi veya uçuş cerrahıyla konuş; "
          "kötüleşirse daha erken.",
}
RECORDED = {
    "en": "- {label} is recorded (self-rated {severity}); its follow-up tasks are in your daily missions.",
    "tr": "- {label} kaydedildi (kendi değerlendirmen: {severity}); takip görevleri günlük görevlerinde.",
}
TO_CONFIRM = {
    "en": "- {label}: confirm or dismiss it in the companion. Once confirmed it is recorded for the medical team "
          "and adds follow-up tasks to your daily missions.",
    "tr": "- {label}: Companion'da onayla veya reddet. Onaylanınca sağlık ekibi için kaydedilir ve günlük "
          "görevlerine takip görevleri eklenir.",
}
SEVERE_NOW = {
    "en": "- You rated {label} severe: contact the crew medical officer or flight surgeon now, using the mission's "
          "medical contact procedure.",
    "tr": "- {label} için 'şiddetli' dedin: görev tıbbi iletişim prosedürüyle ekip sağlık görevlisine veya uçuş "
          "cerrahına şimdi ulaş.",
}
