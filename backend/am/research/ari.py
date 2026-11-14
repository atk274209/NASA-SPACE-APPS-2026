"""AM Research Intelligence (ARI): structured observations from findings + literature.

Composition of an observation:
  observation         deterministic text from the statistical finding (+ caveat)
  scientific_context  retrieved knowledge-base chunks, quoted with their source
  hypothesis          drafted by the local LLM ONLY from the finding and the retrieved chunks,
                      with citations restricted to those chunk IDs; validated before storage.
                      Without a model, a clearly-labelled template hypothesis is used.
  evidence_level      from the finding type; never 'causal'
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from am.ai.companion import grounding_check
from am.ai.ollama import LLMUnavailable, OllamaClient
from am.db.models import Astronaut
from am.db.models_research import ResearchFinding, ResearchObservation
from am.rag.knowledge import Hit, search

TOPIC_TERMS = {
    "exercise_duration": "exercise countermeasures resistive exercise bone loss muscle atrophy aerobic capacity",
    "step_count": "physical activity exercise countermeasures muscle",
    "heart_rate": "cardiovascular deconditioning heart rate orthostatic intolerance",
    "hrv_rmssd": "cardiovascular autonomic heart rate variability",
    "sleep_duration": "sleep loss circadian desynchronization sleep duration",
    "body_temperature": "immune system dysregulation",
    "spo2": "cardiovascular respiratory",
    "water_intake": "nutrition hydration fluid shift",
    "energy_intake": "nutrition inadequate food intake",
    "selfreport:mood": "behavioral health isolation confinement mood",
    "selfreport:stress": "behavioral health stress isolation",
    "selfreport:fatigue": "fatigue sleep loss behavioral health",
    "selfreport:sleep_quality": "sleep quality sleep loss circadian",
    "selfreport:motivation": "behavioral health motivation isolation",
}

HYPOTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "rationale": {"type": "string"},
        "cited_chunk_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["hypothesis", "rationale", "cited_chunk_ids"],
}

PROMPT = """You draft ONE cautious research hypothesis for a space-medicine research log.
Inputs: a statistical finding from one astronaut's data, and literature excerpts with numeric IDs.
Rules:
- The hypothesis must be phrased as a testable possibility ("may", "could"), never as a conclusion.
- Do not claim causation, diagnose, or recommend treatment.
- Use ONLY the excerpts provided; cite the excerpt IDs you rely on in cited_chunk_ids.
  If no excerpt is relevant, return an empty cited_chunk_ids list and say the literature retrieved
  does not address it.
- Do not introduce numbers that are not in the inputs.
- If the finding says the association is NOT present after removing shared trends, the hypothesis
  MUST say that the association may only reflect two parallel trends and needs testing with
  detrended or controlled data.
- 2-3 sentences for the hypothesis, 2-4 sentences for the rationale. English.
Return JSON with keys hypothesis, rationale, cited_chunk_ids."""


def _topic_query(f: ResearchFinding) -> str:
    keys = [k for k in (f.x_key, f.y_key) if k]
    terms = []
    for k in keys:
        metric = k if k.startswith("selfreport:") else k.split(":")[0]
        terms.append(TOPIC_TERMS.get(metric, metric.replace("_", " ")))
    return "spaceflight " + " ".join(terms)


def _evidence_level(f: ResearchFinding) -> str:
    return {"trend": "trend", "correlation": "correlation", "temporal_association": "temporal_association"}[
        f.finding_type]


def _confidence(f: ResearchFinding, simulated: bool) -> str:
    parts = [f.caveat]
    if f.finding_type != "trend":
        parts.append("The association persists after removing shared trends." if f.detrended_supported
                     else "The association does not persist after removing shared trends; it may only reflect "
                          "two parallel trends.")
    parts.append(f"Based on {f.n_days} daily values from one person (n=1). Insufficient evidence for causal "
                 "conclusions.")
    if simulated:
        parts.append("The underlying data is SIMULATED; this observation demonstrates the method only.")
    return " ".join(parts)


def build_observation(session: Session, finding_id: int, *, llm: OllamaClient | None, chat_model: str,
                      embed_model: str) -> ResearchObservation:
    f = session.get(ResearchFinding, finding_id)
    if f is None:
        raise LookupError("finding not found")
    a = session.get(Astronaut, f.astronaut_id)
    hits: list[Hit] = search(session, _topic_query(f), llm=llm, embed_model=embed_model, k=4)
    def _line(h: Hit) -> str:
        d = h.chunk.document
        cite = f"({d.organization or d.title}, {d.published_date or 'n.d.'}; {h.chunk.source_url or d.url})"
        if h.chunk.kind == "passage":  # verbatim public-domain text
            return f'[{h.chunk.id}] Quote: "{h.chunk.text}" {cite}'
        label = "Stated limitation" if h.chunk.kind == "limitation" else "Summary (AM paraphrase, not a quote)"
        return f"[{h.chunk.id}] {label}: {h.chunk.text} {cite}"

    context_lines = [_line(h) for h in hits]
    doc_refs = [h.as_dict() for h in hits]
    title = (f"{f.y_label}: {f.finding_type.replace('_', ' ')}" if f.finding_type == "trend"
             else f"{f.x_label} and {f.y_label}")

    hypothesis, generated_by, model, grounding = "", "deterministic", None, {}
    if llm is not None:
        try:
            msg = (f"FINDING: {f.summary}\nCAVEAT: {f.caveat}\n\nEXCERPTS:\n" +
                   ("\n".join(context_lines) if context_lines else "(none retrieved)"))
            reply = llm.chat(chat_model, [{"role": "system", "content": PROMPT}, {"role": "user", "content": msg}],
                             format=HYPOTHESIS_SCHEMA, temperature=0.1)
            data = json.loads(reply.content)
            allowed = {h.chunk.id for h in hits}
            cited = [int(c) for c in data.get("cited_chunk_ids", []) if int(c) in allowed]
            invalid = [c for c in data.get("cited_chunk_ids", []) if int(c) not in allowed]
            text = f"{data['hypothesis'].strip()}\n\nRationale: {data['rationale'].strip()}"
            unverified = grounding_check(text, [f.summary, f.caveat, *context_lines])
            hypothesis, generated_by, model = text, "llm", reply.model or chat_model
            grounding = {"cited_chunk_ids": cited, "invalid_citations_removed": invalid,
                         "unverified_numbers": unverified}
        except (LLMUnavailable, ValueError, KeyError, TypeError):
            hypothesis = ""
    if not hypothesis:
        if f.finding_type == "trend":
            hypothesis = (f"Template hypothesis (no language model used): the change in {f.y_label} over the "
                          "mission may reflect an underlying physiological or behavioural change that warrants "
                          "investigation.")
        else:
            hypothesis = (f"Template hypothesis (no language model used): changes in {f.x_label} may be related "
                          f"to changes in {f.y_label}; this requires investigation with controlled data.")

    obs = ResearchObservation(
        astronaut_id=f.astronaut_id, finding_ids=[f.id], title=title[:200],
        observation=f.summary, scientific_context="\n".join(context_lines), hypothesis=hypothesis,
        evidence_level=_evidence_level(f), confidence_note=_confidence(f, bool(a and a.is_simulated)),
        doc_refs=doc_refs, generated_by=generated_by, model=model, grounding=grounding,
        is_simulated=bool(a and a.is_simulated))
    session.add(obs)
    session.flush()
    return obs
