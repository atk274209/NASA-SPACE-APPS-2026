"""Source-traceable knowledge base + retrieval.

Documents come from knowledge/sources/*.json (curated, verified metadata). Each document is
split into chunks: verbatim public-domain passages, paraphrased key findings and stated
limitations. Retrieval uses Ollama embeddings when available and falls back to a lexical
TF-IDF score, so search works fully offline even without an embedding model.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from am.ai.ollama import LLMUnavailable, OllamaClient
from am.db.models_research import DocumentChunk, KnowledgeDocument

REQUIRED = ("id", "title", "url", "source_type", "license_basis")
_TOKEN = re.compile(r"[a-z][a-z0-9\-]+")
_STOP = set("the a an and or of to in on for with by is are was were be been as at from that this these those it its "
            "into than more most can may also not no such which during after before over under between their there "
            "has have had".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]


@dataclass
class IngestReport:
    loaded: int
    updated: int
    unchanged: int
    skipped: list[str]
    embedded: int
    embed_model: str | None
    embed_error: str | None


_UNCERTAIN = ("not stated", "not established", "not verified", "uncertain", "copyright", "unclear")


def _passages_allowed(license_basis: str) -> bool:
    """Verbatim text only for sources explicitly recorded as US federal government works."""
    lb = license_basis.lower()
    return "government work" in lb and not any(u in lb for u in _UNCERTAIN)


def _validate(doc: dict[str, Any]) -> str | None:
    for k in REQUIRED:
        if not doc.get(k):
            return f"missing '{k}'"
    if not str(doc["url"]).startswith(("http://", "https://")):
        return "url must be http(s)"
    if doc.get("passages") and not _passages_allowed(str(doc.get("license_basis", ""))):
        return "verbatim passages require an explicit US-government-work license basis"
    return None


def _same_model(a: str | None, b: str | None) -> bool:
    """Ollama treats an untagged model name as ':latest'."""
    norm = (lambda m: m if m is None or ":" in m else f"{m}:latest")
    return norm(a) == norm(b)


def ingest_directory(session: Session, directory: Path, llm: OllamaClient | None, embed_model: str) -> IngestReport:
    loaded = updated = unchanged = embedded = 0
    skipped: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            skipped.append(f"{path.name}: unreadable ({exc.__class__.__name__})")
            continue
        err = _validate(doc)
        if err:
            skipped.append(f"{path.name}: {err}")
            continue
        h = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
        row = session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.slug == doc["id"])).first()
        if row is not None and row.content_hash == h:
            unchanged += 1
            continue
        if row is None:
            row = KnowledgeDocument(slug=doc["id"])
            session.add(row)
            loaded += 1
        else:
            row.chunks.clear()
            updated += 1
        row.title = doc["title"]
        row.authors = doc.get("authors") or []
        row.organization = doc.get("organization") or ""
        row.published_date = doc.get("published_date")
        row.url = doc["url"]
        row.doi = doc.get("doi")
        row.source_type = doc["source_type"]
        row.topics = doc.get("topics") or []
        row.license_basis = doc["license_basis"]
        row.key_findings = doc.get("key_findings") or []
        row.limitations = doc.get("limitations") or []
        row.retrieved_at = doc.get("retrieved_at")
        row.verification_notes = doc.get("verification_notes") or ""
        row.content_hash = h
        ord_ = 0
        for p in doc.get("passages") or []:
            if p.get("text"):
                row.chunks.append(DocumentChunk(ord=ord_, kind="passage", text=p["text"].strip(),
                                                source_url=p.get("url") or doc["url"]))
                ord_ += 1
        for f in row.key_findings:
            row.chunks.append(DocumentChunk(ord=ord_, kind="finding", text=f.strip(), source_url=doc["url"]))
            ord_ += 1
        for lim in row.limitations:
            row.chunks.append(DocumentChunk(ord=ord_, kind="limitation", text=lim.strip(), source_url=doc["url"]))
            ord_ += 1
    session.flush()

    embed_error = None
    if llm is not None:
        todo = [c for c in session.scalars(select(DocumentChunk))
                if c.embedding is None or not _same_model(c.embed_model, embed_model)]
        try:
            for i in range(0, len(todo), 16):
                batch = todo[i:i + 16]
                vecs = llm.embed(embed_model, [_embed_text(c) for c in batch])
                for c, v in zip(batch, vecs, strict=True):
                    arr = np.asarray(v, dtype=np.float32)
                    arr /= max(float(np.linalg.norm(arr)), 1e-12)
                    c.embedding, c.embed_model = arr.tobytes(), embed_model
                    embedded += 1
        except LLMUnavailable as exc:
            embed_error = exc.reason
        session.flush()
    return IngestReport(loaded, updated, unchanged, skipped, embedded, embed_model if embedded else None, embed_error)


def _embed_text(c: DocumentChunk) -> str:
    return f"{c.document.title}. {c.text}"


@dataclass
class Hit:
    chunk: DocumentChunk
    score: float
    method: str

    def as_dict(self) -> dict[str, Any]:
        d = self.chunk.document
        return {"chunk_id": self.chunk.id, "doc_slug": d.slug, "title": d.title, "organization": d.organization,
                "published_date": d.published_date, "url": self.chunk.source_url or d.url, "kind": self.chunk.kind,
                "text": self.chunk.text, "score": round(self.score, 4), "method": self.method}


def search(session: Session, query: str, *, llm: OllamaClient | None, embed_model: str, k: int = 5,
           min_score: float | None = None) -> list[Hit]:
    chunks = session.scalars(select(DocumentChunk)).all()
    if not chunks or not query.strip():
        return []
    embedded = [c for c in chunks if c.embedding is not None and _same_model(c.embed_model, embed_model)]
    if llm is not None and len(embedded) == len(chunks):
        try:
            q = np.asarray(llm.embed(embed_model, [query])[0], dtype=np.float32)
            q /= max(float(np.linalg.norm(q)), 1e-12)
            mat = np.stack([np.frombuffer(c.embedding, dtype=np.float32) for c in embedded])
            scores = mat @ q
            order = np.argsort(-scores)[:k]
            thr = 0.45 if min_score is None else min_score
            return [Hit(embedded[i], float(scores[i]), "embedding") for i in order if scores[i] >= thr]
        except LLMUnavailable:
            pass
    return _lexical(chunks, query, k, 0.05 if min_score is None else min_score)


def _lexical(chunks: list[DocumentChunk], query: str, k: int, min_score: float) -> list[Hit]:
    docs = [Counter(_tokens(f"{c.document.title} {' '.join(c.document.topics)} {c.text}")) for c in chunks]
    df = Counter(t for d in docs for t in d)
    n = len(docs)
    q = set(_tokens(query))
    hits = []
    for c, d in zip(chunks, docs, strict=True):
        length = sum(d.values()) or 1
        score = sum((d[t] / length) * math.log(1 + n / df[t]) for t in q if t in d)
        if score > 0:
            hits.append(Hit(c, score, "lexical"))
    hits.sort(key=lambda h: -h.score)
    top = hits[0].score if hits else 0
    return [h for h in hits[:k] if top and h.score / top >= min_score]
