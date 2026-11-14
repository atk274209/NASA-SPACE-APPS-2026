"""Symptom catalog shared by symptom reporting, personalised missions, the journal and the body map.

Safety rules encoded here:
  * Tasks are monitoring, logging, protocol-adherence and reporting prompts only. AM never
    suggests medication, treatment, doses or diagnosis.
  * No durations or numeric thresholds are invented ("if it continues, per protocol").
  * `report_promptly` marks symptoms for which the DEMO escalation procedure says to contact
    the crew medical officer / flight surgeon without waiting. This list is a DEMONSTRATION
    placeholder and must be replaced by the mission's approved medical procedures.
  * Synonyms (English + Turkish stems) are used only to PROPOSE a symptom from chat; the
    astronaut must confirm before anything is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

REPORT_PROMPTLY_LABEL = ("DEMO list: symptoms the placeholder escalation procedure says to report to the crew "
                         "medical officer without waiting. Requires validation by mission medical operations.")

BODY_SYSTEMS: dict[str, str] = {
    "neuro_behavioral": "Brain, sleep and behavioural health",
    "eyes": "Eyes and vision",
    "cardiovascular": "Heart and circulation",
    "respiratory": "Breathing and airways",
    "gastrointestinal": "Digestion",
    "musculoskeletal": "Muscles, bones and spine",
    "immune_skin": "Immune system and skin",
    "metabolic": "Nutrition, hydration and metabolism",
}

CATEGORIES = ("gastrointestinal", "pain", "neurological", "sensory", "respiratory", "skin", "sleep", "behavioral",
              "general", "cardiorespiratory")


@dataclass(frozen=True)
class TaskTemplate:
    code: str  # reuse generic mission templates (hydration_log, exercise_session, ...) or a symptom-specific code
    title: str
    detail: str
    completion_rule: str = "manual"  # manual | checkin | measurement:<metric>:<context>
    protocol_ref: str = "AM-DEMO-SYM-00"


@dataclass(frozen=True)
class Symptom:
    code: str
    label: str
    category: str
    body_system: str
    synonyms_en: tuple[str, ...]
    synonyms_tr: tuple[str, ...]
    report_promptly: bool = False
    tasks: tuple[TaskTemplate, ...] = field(default_factory=tuple)


_CMO_IF_CONTINUES = TaskTemplate(
    "symptom_followup_cmo", "Tell the crew medical officer if it continues or gets worse",
    "Mention it at your next private medical conference, or sooner if it worsens, as your mission medical "
    "procedure describes.", "manual", "AM-DEMO-SYM-01")
_CONTACT_NOW = TaskTemplate(
    "symptom_contact_now", "Contact the crew medical officer or flight surgeon now",
    "Use the mission's medical contact procedure today. Do not wait for the end of the day.",
    "manual", "AM-DEMO-SYM-02")
_LOG_SYMPTOM = TaskTemplate(
    "symptom_daily_log", "Note how the symptom is today",
    "In today's check-in note, say whether it is better, the same or worse, so the medical team can see the trend.",
    "checkin", "AM-DEMO-SYM-03")
_HYDRATION = TaskTemplate("hydration_log", "Log water intake", "Record today's total water intake.",
                          "measurement:water_intake:daily", "AM-DEMO-NUT-01")
_EXERCISE = TaskTemplate("exercise_session", "Complete today's scheduled exercise session",
                         "Follow the exercise prescription in your mission protocol; AM does not change it.",
                         "measurement:exercise_duration:daily", "AM-DEMO-EX-01")
_SLEEP_LOG = TaskTemplate("sleep_log", "Log last night's sleep", "Make sure last night's sleep duration is recorded.",
                          "measurement:sleep_duration:daily", "AM-DEMO-SLP-02")
_SLEEP_PREP = TaskTemplate("sleep_prep", "Sleep preparation routine",
                           "Follow the mission sleep-hygiene guidance before your scheduled sleep period.",
                           "manual", "AM-DEMO-SLP-01")
_BEHAVIORAL = TaskTemplate("behavioral_support", "Use a behavioral-health support option",
                           "Choose one of the support options in your mission behavioral-health plan.",
                           "manual", "AM-DEMO-BH-02")
_TEMP = TaskTemplate("temperature_check", "Record a body temperature reading",
                     "Take and record a body temperature reading at rest.",
                     "measurement:body_temperature:rest", "AM-DEMO-MON-02")


SYMPTOMS: tuple[Symptom, ...] = (
    Symptom("constipation", "Constipation", "gastrointestinal", "gastrointestinal",
            ("constipat", "hard stool", "can't go to the toilet", "no bowel movement", "haven't had a bowel movement",
             "haven't pooped", "can't poop", "cannot poop"),
            ("kabız", "tuvalete çıkamı", "tuvalete çıkamadı", "tuvaletimi yapamı", "tuvaletimi yapamadı",
             "dışkılayamı", "dışkılayamadı", "bağırsaklarım çalışmı"),
            tasks=(TaskTemplate("bowel_log", "Log bowel movements today",
                                "Note whether you had a bowel movement today in the companion check-in.",
                                "manual", "AM-DEMO-GI-01"),
                   _HYDRATION,
                   TaskTemplate("meal_log", "Log today's meals",
                                "Record what you ate, following your mission nutrition plan; AM does not change it.",
                                "manual", "AM-DEMO-NUT-02"),
                   _EXERCISE, _CMO_IF_CONTINUES)),
    Symptom("diarrhea", "Diarrhea", "gastrointestinal", "gastrointestinal",
            ("diarrh", "loose stool"), ("ishal",),
            tasks=(_HYDRATION, TaskTemplate("bowel_log", "Log bowel movements today",
                                            "Note bowel movements today in the companion check-in.",
                                            "manual", "AM-DEMO-GI-01"), _CMO_IF_CONTINUES)),
    Symptom("nausea", "Nausea", "gastrointestinal", "gastrointestinal",
            ("nausea", "nauseous", "queasy", "feel sick"), ("mide bulant", "midem bulan", "bulantı", "kusacak gibi"),
            tasks=(_LOG_SYMPTOM, _HYDRATION, _CMO_IF_CONTINUES)),
    Symptom("vomiting", "Vomiting", "gastrointestinal", "gastrointestinal",
            ("vomit", "threw up", "throwing up"), ("kustum", "kusma", "kusuyorum"),
            tasks=(_HYDRATION, _CMO_IF_CONTINUES)),
    Symptom("abdominal_pain", "Abdominal pain", "pain", "gastrointestinal",
            ("stomach ache", "stomachache", "abdominal pain", "belly pain", "stomach pain", "stomach hurts",
             "tummy ache"),
            ("karın ağrı", "karnım ağrı", "midem ağrı"),
            tasks=(_LOG_SYMPTOM, _CMO_IF_CONTINUES)),
    Symptom("loss_of_appetite", "Loss of appetite", "gastrointestinal", "metabolic",
            ("no appetite", "loss of appetite", "not hungry"), ("iştahsız", "iştahım yok"),
            tasks=(TaskTemplate("meal_log", "Log today's meals",
                                "Record what you ate, following your mission nutrition plan; AM does not change it.",
                                "manual", "AM-DEMO-NUT-02"), _HYDRATION, _CMO_IF_CONTINUES)),
    Symptom("headache", "Headache", "pain", "neuro_behavioral",
            ("headache", "head ache", "migraine", "my head hurts", "head is pounding"),
            ("baş ağrı", "başım ağrı", "kafam ağrı"),
            tasks=(_LOG_SYMPTOM, _HYDRATION, _SLEEP_LOG, _CMO_IF_CONTINUES)),
    Symptom("back_pain", "Back pain", "pain", "musculoskeletal",
            ("back pain", "backache", "back ache", "lower back", "back hurts", "back is sore", "sore back"),
            ("bel ağrı", "belim ağrı", "sırt ağrı", "sırtım ağrı"),
            tasks=(_LOG_SYMPTOM, _EXERCISE, _CMO_IF_CONTINUES)),
    Symptom("muscle_soreness", "Muscle soreness", "pain", "musculoskeletal",
            ("sore muscle", "muscle pain", "muscles hurt", "muscle soreness", "muscles ache", "aching muscles"),
            ("kas ağrı", "kaslarım ağrı"),
            tasks=(_LOG_SYMPTOM, _EXERCISE)),
    Symptom("nasal_congestion", "Nasal congestion", "respiratory", "respiratory",
            ("congest", "stuffy nose", "blocked nose"), ("burnum tıkalı", "burun tıkan", "tıkanıklık"),
            tasks=(_LOG_SYMPTOM, _SLEEP_LOG)),
    Symptom("insomnia", "Difficulty sleeping", "sleep", "neuro_behavioral",
            ("insomnia", "can't sleep", "cannot sleep", "couldn't sleep", "trouble sleeping", "sleepless"),
            ("uyuyamıyorum", "uyuyamadım", "uykusuz", "uyku sorunu"),
            tasks=(_SLEEP_LOG, _SLEEP_PREP, _CMO_IF_CONTINUES)),
    Symptom("fatigue", "Fatigue", "general", "neuro_behavioral",
            ("fatigue", "exhausted", "worn out", "very tired"), ("yorgun", "bitkin", "halsiz"),
            tasks=(_SLEEP_LOG, _SLEEP_PREP, _LOG_SYMPTOM)),
    Symptom("fever", "Feeling feverish", "general", "immune_skin",
            ("fever", "feverish", "chills", "running a temperature"), ("ateşim", "ateş var", "titreme", "ateşlen"),
            tasks=(_TEMP, _LOG_SYMPTOM, _CMO_IF_CONTINUES)),
    Symptom("skin_irritation", "Skin irritation or rash", "skin", "immune_skin",
            ("rash", "itchy", "itching", "skin irritat"), ("kaşıntı", "kaşın", "döküntü", "cildim tahriş"),
            tasks=(_LOG_SYMPTOM, _CMO_IF_CONTINUES)),
    Symptom("eye_irritation", "Eye irritation or dryness", "sensory", "eyes",
            ("eye irritat", "dry eye", "eyes are dry", "eyes burn"), ("gözüm yan", "göz kuru", "gözlerim kuru"),
            tasks=(_LOG_SYMPTOM, _CMO_IF_CONTINUES)),
    Symptom("vision_change", "Change in vision", "sensory", "eyes",
            ("blurry vision", "blurred vision", "vision change", "can't see clearly", "seeing double"),
            ("bulanık gör", "görmem bulan", "çift görü", "görüşüm bulan"),
            report_promptly=True, tasks=(_CONTACT_NOW, _LOG_SYMPTOM)),
    Symptom("dizziness", "Dizziness or light-headedness", "neurological", "cardiovascular",
            ("dizzy", "dizziness", "lightheaded", "light-headed", "vertigo"),
            ("başım dön", "baş dönme", "sersem", "kafam dön"),
            tasks=(_LOG_SYMPTOM, _HYDRATION, _CMO_IF_CONTINUES)),
    Symptom("fainting", "Fainting or near-fainting", "neurological", "cardiovascular",
            ("fainted", "faint", "passed out", "blacked out"), ("bayıl", "bayga"),
            report_promptly=True, tasks=(_CONTACT_NOW,)),
    Symptom("chest_pain", "Chest pain or tightness", "cardiorespiratory", "cardiovascular",
            ("chest pain", "chest tight", "pain in my chest", "chest hurts", "chest pressure"),
            ("göğüs ağrı", "göğsüm ağrı", "göğsümde ağrı", "göğsüm sıkış", "göğsümde sıkış"),
            report_promptly=True, tasks=(_CONTACT_NOW,)),
    Symptom("shortness_of_breath", "Shortness of breath", "cardiorespiratory", "respiratory",
            ("short of breath", "shortness of breath", "can't breathe", "cannot breathe", "breathless"),
            ("nefes darl", "nefes alamı", "nefesim daral", "nefes alamadı", "nefessiz kal"),
            report_promptly=True, tasks=(_CONTACT_NOW,)),
    Symptom("low_mood", "Low mood", "behavioral", "neuro_behavioral",
            ("depressed", "feel down", "feeling down", "feeling low", "hopeless", "feel sad", "feeling sad"),
            ("mutsuz", "keyifsiz", "moralim bozuk", "üzgün", "depresif", "canım sıkkın", "moralim düşük"),
            tasks=(_BEHAVIORAL, _CMO_IF_CONTINUES)),
    Symptom("anxiety", "Anxiety or worry", "behavioral", "neuro_behavioral",
            ("anxious", "anxiety", "panic", "worried"), ("kaygı", "endişe", "panik", "tedirgin"),
            tasks=(_BEHAVIORAL, _CMO_IF_CONTINUES)),
    Symptom("irritability", "Irritability or interpersonal conflict", "behavioral", "neuro_behavioral",
            ("irritable", "angry", "conflict", "argument", "argued"), ("sinirli", "tartış", "kavga", "gergin"),
            tasks=(_BEHAVIORAL, TaskTemplate("crew_conflict_note", "Note what happened in your check-in",
                                             "A short private note helps the behavioral-health team support you.",
                                             "manual", "AM-DEMO-BH-04"))),
    Symptom("loneliness", "Feeling isolated or lonely", "behavioral", "neuro_behavioral",
            ("lonely", "isolated", "homesick", "miss my family", "miss home"), ("yalnız", "özledim", "izole"),
            tasks=(_BEHAVIORAL,)),
)

BY_CODE: dict[str, Symptom] = {s.code: s for s in SYMPTOMS}
SEVERITIES = ("mild", "moderate", "severe")


def get(code: str) -> Symptom | None:
    return BY_CODE.get(code)


def symptoms_for_system(system: str) -> list[Symptom]:
    return [s for s in SYMPTOMS if s.body_system == system]


assert all(s.body_system in BODY_SYSTEMS for s in SYMPTOMS)
assert all(s.category in CATEGORIES for s in SYMPTOMS)
assert len(BY_CODE) == len(SYMPTOMS)
