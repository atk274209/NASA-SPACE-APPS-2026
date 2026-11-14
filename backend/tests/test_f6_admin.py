"""F6: settings, diagnostics, encrypted backups, the restore CLI and the authorized ground-report outbox.

Every test runs without a language model (conftest points Ollama at a closed port) unless it
installs a FakeOllama. Data comes from the simulator, or is inserted directly where the
owning feature's endpoint is built elsewhere (symptom reports).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from am.admin import crypto, outbox
from am.config import REPO_ROOT
from am.db.base import utcnow
from am.db.models_m5 import SymptomReport
from am.main import create_app
from tests.test_companion import FakeOllama

PASS = "correct horse battery staple"
OTHER = "a completely different phrase"


class EmbedOllama(FakeOllama):
    """FakeOllama that also answers /api/embed and records which model each request named."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.embed_models: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            body = json.loads(request.content)
            self.embed_models.append(body["model"])
            return httpx.Response(200, json={"embeddings": [[1.0, 0.0, 0.0] for _ in body["input"]]})
        return super().handler(request)


@pytest.fixture
def sim(client):
    r = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "F6-1"})
    assert r.status_code == 201
    return r.json()


def _db_path(settings) -> Path:
    return Path(settings.database_url.removeprefix("sqlite:///"))


def _db_bytes(settings) -> bytes:
    """Raw bytes of the database file and its write-ahead log, where recent writes live."""
    db = _db_path(settings)
    return b"".join(p.read_bytes() for p in (db, Path(f"{db}-wal")) if p.exists())


def _codes(path: Path) -> set[str]:
    con = sqlite3.connect(str(path))
    try:
        return {r[0] for r in con.execute("SELECT code FROM astronaut")}
    finally:
        con.close()


def _restore_cli():
    spec = importlib.util.spec_from_file_location("restore_backup", REPO_ROOT / "scripts" / "restore_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backup(client, reason: str = "routine backup") -> dict:
    r = client.post("/api/v1/admin/backups", json={"passphrase": PASS, "actor": "flight surgeon", "reason": reason})
    assert r.status_code == 201, r.text
    return r.json()


def _report_body(sim: dict, client, **over) -> dict:
    aid = sim["astronaut"]["id"]
    today = client.get(f"/api/v1/astronauts/{aid}/overview").json()["mission_day"]
    body = {"astronaut_id": aid, "first_day": 1, "last_day": today, "sections": ["alerts"],
            "authorized_by": "Flight surgeon Kaya", "reason": "Weekly private medical conference"}
    return {**body, **over}


# ------------------------------------------------------------------ settings
def test_settings_list_defaults_descriptions_and_types(client):
    rows = {s["key"]: s for s in client.get("/api/v1/settings").json()}
    assert set(rows) >= {"chat_model", "embed_model", "symptom_llm_detection", "crew_network_enabled",
                         "backup_retention"}
    assert all(r["description"] and r["is_default"] and r["updated_by"] is None for r in rows.values())
    assert rows["backup_retention"]["kind"] == "int" and rows["backup_retention"]["default"] == 10
    assert rows["backup_retention"]["min_value"] == 1
    assert rows["symptom_llm_detection"]["kind"] == "bool" and rows["symptom_llm_detection"]["value"] is True
    chat = rows["chat_model"]
    assert chat["kind"] == "model" and chat["value"] is None and chat["effective"] == "qwen2.5:7b-instruct"


def test_setting_changes_are_type_checked_and_audited(client):
    def put(key, value, actor="ops lead"):
        return client.put(f"/api/v1/settings/{key}", json={"value": value, "actor": actor})

    for bad in ("false", 0, None):
        assert put("symptom_llm_detection", bad).status_code == 422
    r = put("symptom_llm_detection", False)
    assert r.status_code == 200
    assert r.json()["value"] is False and r.json()["updated_by"] == "ops lead" and not r.json()["is_default"]
    for bad in (0, "3", True, 2.5, 1001):
        assert put("backup_retention", bad).status_code == 422, bad
    assert put("backup_retention", 3).json()["value"] == 3
    assert put("no_such_setting", 1).status_code == 404
    assert put("backup_retention", 4, actor="   ").status_code == 422
    assert client.put("/api/v1/settings/backup_retention", json={"actor": "ops"}).status_code == 422

    changed = [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "setting.changed"]
    assert sorted(a["entity_id"] for a in changed) == ["backup_retention", "symptom_llm_detection"]
    assert {"old": None, "new": False} in [a["payload"] for a in changed]
    assert all(a["actor"] == "ops lead" for a in changed)
    rows = {s["key"]: s for s in client.get("/api/v1/settings").json()}
    assert rows["backup_retention"]["value"] == 3 and rows["symptom_llm_detection"]["value"] is False


def test_model_settings_accept_only_installed_models(client):
    def put(key, value):
        return client.put(f"/api/v1/settings/{key}", json={"value": value, "actor": "ops"})

    # conftest: Ollama unreachable, so a new model cannot be confirmed
    r = put("chat_model", "llama3.1:8b")
    assert r.status_code == 409 and "not reachable" in r.json()["detail"]
    assert put("chat_model", None).status_code == 200  # the deployment default needs no check

    client.app.state.ctx.llm = FakeOllama([], models=["qwen2.5:7b-instruct", "llama3.1:8b",
                                                      "nomic-embed-text:latest"]).client()
    r = put("chat_model", "missing-model:1b")
    assert r.status_code == 422 and "not installed" in r.json()["detail"] and "llama3.1:8b" in r.json()["detail"]
    assert put("chat_model", 42).status_code == 422
    r = put("chat_model", "llama3.1:8b")
    assert r.status_code == 200 and r.json()["effective"] == "llama3.1:8b"
    assert put("embed_model", "nomic-embed-text").status_code == 200  # untagged name means ':latest'
    r = put("chat_model", "  ")
    assert r.json()["value"] is None and r.json()["effective"] == "qwen2.5:7b-instruct"


def test_research_routes_use_the_effective_models(client):
    aid = client.post("/api/v1/simulation/run", json={"scenario": "gradual_decline",
                                                       "astronaut_code": "F6-R"}).json()["astronaut"]["id"]
    run = client.post("/api/v1/research/correlation/run", json={"astronaut_id": aid}).json()
    trend = next(f for f in run["findings"] if f["y_key"] == "heart_rate:rest" and f["finding_type"] == "trend")
    fake = EmbedOllama([{"role": "assistant", "content": json.dumps({
        "hypothesis": "Resting heart rate may rise with deconditioning.", "rationale": "r", "cited_chunk_ids": []})}],
        models=["qwen2.5:7b-instruct", "llama3.1:8b", "nomic-embed-text:latest", "mxbai-embed-large:latest"])
    client.app.state.ctx.llm = fake.client()
    for key, value in (("chat_model", "llama3.1:8b"), ("embed_model", "mxbai-embed-large")):
        assert client.put(f"/api/v1/settings/{key}", json={"value": value, "actor": "ops"}).status_code == 200

    rep = client.post("/api/v1/knowledge/ingest").json()
    assert rep["embedded"] > 0 and set(fake.embed_models) == {"mxbai-embed-large"}
    emb = client.get("/api/v1/admin/diagnostics").json()["embeddings"]
    assert emb["coverage_percent"] == 100.0 and emb["chunks_current_model"] == emb["chunks_total"]
    assert emb["search_method"] == "embedding"

    obs = client.post("/api/v1/research/observations", json={"finding_id": trend["id"]}).json()
    assert fake.requests[-1]["model"] == "llama3.1:8b" and obs["model"] == "llama3.1:8b"
    assert client.get("/api/v1/knowledge/search", params={"q": "heart"}).json()[0]["method"] == "embedding"

    # switching the embedding model leaves the stored vectors behind: coverage drops to zero
    client.put("/api/v1/settings/embed_model", json={"value": None, "actor": "ops"})
    emb = client.get("/api/v1/admin/diagnostics").json()["embeddings"]
    assert emb["embed_model"] == "nomic-embed-text" and emb["chunks_current_model"] == 0
    assert emb["chunks_other_model"] == emb["chunks_total"] and emb["search_method"] == "keyword (offline fallback)"


# ------------------------------------------------------------------ diagnostics and privacy
def test_diagnostics_measure_storage_schema_and_services(client, sim):
    d = client.get("/api/v1/admin/diagnostics").json()
    db = d["database"]
    assert db["engine"] == "sqlite" and db["size_bytes"] > 0
    assert db["alembic_revision"] and db["alembic_revision"] == db["alembic_head"] and db["at_head"]
    assert db["row_counts"]["astronaut"] == 1 and db["row_counts"]["measurement"] > 100
    assert db["row_counts"]["sync_package"] == 0
    assert d["storage"]["free_bytes"] > 0 and d["storage"]["total_bytes"] >= d["storage"]["free_bytes"]
    o = d["ollama"]
    assert o["reachable"] is False and o["reason"] == "service_unreachable"
    assert o["chat_model"] == "qwen2.5:7b-instruct" and o["chat_model_installed"] is None
    assert d["embeddings"]["search_method"] == "no documents ingested"
    assert d["backups"] == {"last_backup_at": None, "last_backup_id": None, "stored": 0, "retention": 10}
    v = d["versions"]
    assert v["app"] == "0.1.0" and v["python"].count(".") == 2 and v["thresholds_label"].startswith("DEMO")


def test_privacy_inventory_describes_this_installation(client, settings):
    p = client.get("/api/v1/admin/privacy").json()
    stores = {s["key"]: s for s in p["stores"]}
    assert stores["backups"]["where"] == str(settings.data_dir / "backups")
    assert stores["outbox"]["where"] == str(settings.data_dir / "outbox")
    assert stores["database"]["where"] == str(_db_path(settings))
    assert "not encrypt" in stores["database"]["protection"]
    assert p["model_service"]["is_local"] is True and p["warnings"] == []
    assert "no function that uploads" in p["transmission"]
    assert p["counts"] == {"ground_reports_prepared": 0, "ground_reports_exported": 0, "backups_stored": 0}


# ------------------------------------------------------------------ encrypted backups
def test_backup_is_encrypted_verifiable_and_never_stores_the_passphrase(client, sim, settings):
    b = _backup(client, "before maintenance")
    path = Path(b["location"])
    assert path.parent == (settings.data_dir / "backups").resolve() and path.suffix == ".ambk"
    blob = path.read_bytes()
    assert blob.startswith(b"AMBK1") and b["sha256"] == hashlib.sha256(blob).hexdigest()
    assert b["size_bytes"] == len(blob) and b["status"] == "stored" and b["file_present"]
    assert b["cipher"] == "AES-256-GCM" and b["kdf"] == {"name": "scrypt", "n": 2**15, "r": 8, "p": 1,
                                                          "salt_bytes": 16}
    assert b"SQLite format 3" not in blob and b"F6-1" not in blob  # nothing readable without the key

    # the plaintext is a consistent copy of the live database
    plain = bytearray(crypto.decrypt_bytes(blob, PASS))
    plain[18] = plain[19] = 1  # open the WAL-mode image in memory
    con = sqlite3.connect(":memory:")
    con.deserialize(bytes(plain))
    assert [r[0] for r in con.execute("SELECT code FROM astronaut")] == ["F6-1"]
    con.close()

    v = client.post(f"/api/v1/admin/backups/{b['id']}/verify", json={"passphrase": PASS, "actor": "ops"}).json()
    assert v["ok"] and v["decrypted"] and v["checksum_ok"] and v["integrity"] == ["ok"]
    assert v["alembic_revision"] == b["alembic_revision"] and v["tables"] > 10
    wrong = client.post(f"/api/v1/admin/backups/{b['id']}/verify", json={"passphrase": OTHER}).json()
    assert not wrong["ok"] and not wrong["decrypted"] and wrong["checksum_ok"]
    assert wrong["message"].startswith("Wrong passphrase")
    assert client.post("/api/v1/admin/backups/999/verify", json={"passphrase": PASS}).status_code == 404

    assert [x["id"] for x in client.get("/api/v1/admin/backups").json()] == [b["id"]]
    assert client.get("/api/v1/admin/diagnostics").json()["backups"]["last_backup_id"] == b["id"]
    trail = client.get("/api/v1/admin/audit").json()
    created = next(a for a in trail if a["action"] == "backup.created")
    assert created["actor"] == "flight surgeon" and created["payload"]["sha256"] == b["sha256"]
    assert [a["payload"]["ok"] for a in trail if a["action"] == "backup.verified"] == [False, True]
    # the passphrase is in no response, audit payload, database page or WAL frame
    assert PASS not in json.dumps(trail) and OTHER not in json.dumps(trail)
    assert PASS.encode() not in _db_bytes(settings) and OTHER.encode() not in _db_bytes(settings)


def test_backup_validation_never_echoes_the_passphrase(client, settings):
    short = "tooshort-1"
    r = client.post("/api/v1/admin/backups", json={"passphrase": short, "actor": "ops", "reason": "test run"})
    assert r.status_code == 422 and short not in r.text and "12 characters" in r.text
    r = client.post("/api/v1/admin/backups", json={"passphrase": PASS, "reason": "actor is missing"})
    assert r.status_code == 422 and PASS not in r.text
    assert client.get("/api/v1/admin/backups").json() == []
    backups_root = settings.data_dir / "backups"
    assert not backups_root.exists() or not any(backups_root.iterdir())


def test_modified_backup_file_is_detected(client, sim):
    b = _backup(client)
    path = Path(b["location"])
    data = bytearray(path.read_bytes())
    data[-40] ^= 0x01
    path.write_bytes(bytes(data))
    v = client.post(f"/api/v1/admin/backups/{b['id']}/verify", json={"passphrase": PASS}).json()
    assert not v["ok"] and not v["checksum_ok"] and not v["decrypted"] and "changed" in v["message"]


def test_backup_retention_prunes_the_oldest_files(client, sim, settings):
    client.put("/api/v1/settings/backup_retention", json={"value": 2, "actor": "ops"})
    made = [_backup(client, f"backup {i}") for i in range(3)]
    ids = [b["id"] for b in made]
    assert made[2]["pruned_backup_ids"] == [ids[0]] and made[1]["pruned_backup_ids"] == []
    listing = {x["id"]: x for x in client.get("/api/v1/admin/backups").json()}
    assert listing[ids[0]]["status"] == "pruned" and not listing[ids[0]]["file_present"]
    assert all(listing[i]["status"] == "stored" and listing[i]["file_present"] for i in ids[1:])
    assert len(list((settings.data_dir / "backups").glob("*.ambk"))) == 2
    assert client.post(f"/api/v1/admin/backups/{ids[0]}/verify", json={"passphrase": PASS}).status_code == 409
    pruned = [a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "backup.pruned"]
    assert [a["entity_id"] for a in pruned] == [str(ids[0])] and pruned[0]["payload"]["retention"] == 2


def test_restore_cli_needs_confirm_checks_passphrase_and_keeps_a_copy(settings, monkeypatch):
    restore = _restore_cli()
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/v1/astronauts", json={"code": "KEEP-1", "display_name": "In the backup"}).status_code == 201
        b = _backup(c, "before an experiment")
        assert c.post("/api/v1/astronauts", json={"code": "LATE-1", "display_name": "After the backup"}).status_code == 201
    # the app is shut down here, as the CLI requires
    db = _db_path(settings)
    args = [b["location"], "--actor", "Ops lead", "--reason", "roll back the experiment",
            "--data-dir", str(settings.data_dir), "--database-url", settings.database_url]
    pre_dir = settings.data_dir / "pre-restore"

    assert restore.main(args) == 2  # no --confirm: nothing happens
    assert _codes(db) == {"KEEP-1", "LATE-1"}

    monkeypatch.setattr("sys.stdin", io.StringIO(OTHER + "\n"))
    assert restore.main(args + ["--confirm", "--passphrase-stdin"]) == 1
    assert _codes(db) == {"KEEP-1", "LATE-1"} and not pre_dir.exists()

    monkeypatch.setattr("sys.stdin", io.StringIO(PASS + "\n"))
    assert restore.main(args + ["--confirm", "--passphrase-stdin"]) == 0
    assert _codes(db) == {"KEEP-1"}
    copies = list(pre_dir.glob("am-pre-restore-*.sqlite3"))
    assert len(copies) == 1 and _codes(copies[0]) == {"KEEP-1", "LATE-1"}

    con = sqlite3.connect(str(db))
    actor, payload = con.execute("SELECT actor, payload FROM audit_log WHERE action = 'backup.restored'").fetchone()
    con.close()
    payload = json.loads(payload)
    assert actor == "Ops lead" and payload["pre_restore_copy"] == str(copies[0])
    assert payload["backup_sha256"] == b["sha256"] and payload["reason"] == "roll back the experiment"
    assert PASS.encode() not in _db_bytes(settings)

    with TestClient(create_app(settings)) as c:  # the restored database serves normally
        assert [a["code"] for a in c.get("/api/v1/astronauts").json()] == ["KEEP-1"]


# ------------------------------------------------------------------ ground-report outbox
def _seed_all_sections(client, aid: int) -> None:
    assert client.post("/api/v1/journal/generate", json={"astronaut_id": aid}).status_code == 201
    assert client.post("/api/v1/checkins", json={"astronaut_id": aid, "ratings": {"mood": 2, "stress": 4},
                                                 "note": "Slept badly near the fan"}).status_code == 201
    assert client.get("/api/v1/missions/today", params={"astronaut_id": aid}).status_code == 200
    with client.app.state.ctx.session_factory() as s:
        s.add(SymptomReport(astronaut_id=aid, symptom_code="headache", severity="moderate", note="since lunch",
                            source="manual", reported_at=utcnow()))
        s.commit()


def test_ground_report_holds_only_authorized_sections_with_provenance(client, sim, settings):
    aid = sim["astronaut"]["id"]
    _seed_all_sections(client, aid)
    r = client.post("/api/v1/admin/outbox/ground-report",
                    json=_report_body(sim, client, sections=["symptoms", "alerts", "alerts"]))
    assert r.status_code == 201, r.text
    pkg = r.json()
    assert pkg["sections"] == ["alerts", "symptoms"] and pkg["status"] == "prepared" and not pkg["encrypted"]
    assert pkg["authorized_by"] == "Flight surgeon Kaya" and pkg["simulated"] is True

    folder = settings.data_dir / "outbox" / str(pkg["id"])
    json_name, md_name = f"am-ground-report-{pkg['id']}.json", f"am-ground-report-{pkg['id']}.md"
    assert sorted(p.name for p in folder.iterdir()) == [json_name, md_name]
    raw = (folder / json_name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pkg["sha256"] and len(raw) == pkg["size_bytes"]
    assert [f["name"] for f in pkg["files"]] == [json_name, md_name]

    report = json.loads(raw)
    assert set(report["sections"]) == {"alerts", "symptoms"}
    assert report["sections_not_included"] == ["journal", "observations", "checkins", "missions"]
    assert "Slept badly" not in raw.decode("utf-8")  # an unselected section's free text never leaks
    prov = report["provenance"]
    assert prov["thresholds"]["label"] == "DEMO — NOT CLINICALLY VALIDATED" and prov["simulated"] is True
    assert not {"journal_entry", "self_report", "daily_task", "research_observation"} & set(prov["record_ids"])
    alert_ids = {a["id"] for a in client.get("/api/v1/alerts", params={"astronaut_id": aid}).json()}
    assert alert_ids and set(prov["record_ids"]["alert"]) == alert_ids
    alerts = report["sections"]["alerts"]["records"]
    assert all(a["threshold_label"].startswith("DEMO") and a["evidence"] and a["is_simulated"] for a in alerts)
    assert set(prov["record_ids"]["measurement"]) == {e["measurement_id"] for a in alerts for e in a["evidence"]}
    symptom = report["sections"]["symptoms"]["records"][0]
    assert symptom["symptom_code"] == "headache" and symptom["label"] == "Headache"
    assert symptom["severity"] == "moderate" and symptom["is_simulated"] is False

    md = (folder / md_name).read_text(encoding="utf-8")
    assert "Flight surgeon Kaya" in md and "## Alerts" in md and "## Symptom reports" in md
    assert "## Check-ins" not in md and "DEMO" in md and "Slept badly" not in md

    prepared = next(a for a in client.get("/api/v1/admin/audit").json() if a["action"] == "outbox.prepared")
    assert prepared["actor"] == "Flight surgeon Kaya" and prepared["payload"]["sections"] == ["alerts", "symptoms"]
    assert prepared["payload"]["files"][0]["sha256"] == pkg["sha256"]


def test_ground_report_with_all_sections_is_deterministic(client, sim):
    aid = sim["astronaut"]["id"]
    _seed_all_sections(client, aid)
    body = _report_body(sim, client, sections=list(outbox.SECTIONS))
    first = client.post("/api/v1/admin/outbox/ground-report", json=body).json()
    second = client.post("/api/v1/admin/outbox/ground-report", json=body).json()
    data = [json.loads(client.get(f"/api/v1/admin/outbox/{p['id']}/download").content) for p in (first, second)]
    for d in data:
        d.pop("package")
    assert data[0] == data[1]  # same records and request: same content
    sections = data[0]["sections"]
    assert sections["checkins"]["records"][0]["note"] == "Slept badly near the fan"
    assert "not validated clinical instruments" in sections["checkins"]["scale"]
    assert sections["journal"]["records"] and sections["missions"]["records"]
    assert "DEMO" in sections["missions"]["protocol_note"]

    # the renderers are pure: identical bytes for identical inputs
    ctx = client.app.state.ctx
    req = outbox.ReportRequest(aid, body["first_day"], body["last_day"], outbox.SECTIONS, "A", "same request")
    with ctx.session_factory() as s:
        reports = [outbox.build_report(s, req, package_id=7, created_at=datetime(2026, 11, 14, 12),
                                       thresholds=ctx.thresholds) for _ in range(2)]
    assert outbox.render_json(reports[0]) == outbox.render_json(reports[1])
    assert outbox.render_markdown(reports[0]) == outbox.render_markdown(reports[1])


def test_download_is_audited_checked_and_export_is_recorded(client, sim, settings):
    pkg = client.post("/api/v1/admin/outbox/ground-report", json=_report_body(sim, client)).json()
    folder = settings.data_dir / "outbox" / str(pkg["id"])
    url = f"/api/v1/admin/outbox/{pkg['id']}/download"

    r = client.get(url, headers={"X-AM-Actor": quote("Şule Öztürk")})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.content == (folder / f"am-ground-report-{pkg['id']}.json").read_bytes()
    assert "attachment" in r.headers["content-disposition"]
    md_name = f"am-ground-report-{pkg['id']}.md"
    r = client.get(url, params={"file": md_name})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert client.get(url, params={"file": "../../test.sqlite3"}).status_code == 404
    assert client.get("/api/v1/admin/outbox/999/download").status_code == 404

    (folder / md_name).write_text("edited after authorization", encoding="utf-8")
    assert client.get(url, params={"file": md_name}).status_code == 409  # never serve a changed file

    r = client.post(f"/api/v1/admin/outbox/{pkg['id']}/mark-exported", json={"actor": "Comms officer"})
    assert r.status_code == 200
    assert r.json()["status"] == "exported" and r.json()["exported_by"] == "Comms officer" and r.json()["exported_at"]
    assert client.post(f"/api/v1/admin/outbox/{pkg['id']}/mark-exported",
                       json={"actor": "Comms officer"}).status_code == 409
    listed = client.get("/api/v1/admin/outbox", params={"astronaut_id": sim["astronaut"]["id"]}).json()
    assert [(p["id"], p["status"], p["exported_by"]) for p in listed] == [(pkg["id"], "exported", "Comms officer")]

    trail = client.get("/api/v1/admin/audit").json()
    downloads = [a for a in trail if a["action"] == "outbox.downloaded"]
    assert {a["actor"] for a in downloads} == {"Şule Öztürk", "unnamed operator"}
    assert any(a["action"] == "outbox.exported" and a["actor"] == "Comms officer" for a in trail)
    counts = client.get("/api/v1/admin/privacy").json()["counts"]
    assert counts["ground_reports_exported"] == 1 and counts["ground_reports_prepared"] == 0


def test_encrypted_ground_report(client, sim, settings, monkeypatch, tmp_path):
    body = _report_body(sim, client, encrypt=True)
    assert client.post("/api/v1/admin/outbox/ground-report", json=body).status_code == 422  # no passphrase
    r = client.post("/api/v1/admin/outbox/ground-report", json={**body, "encrypt": False, "passphrase": PASS})
    assert r.status_code == 422 and PASS not in r.text  # a passphrase without encryption is refused
    r = client.post("/api/v1/admin/outbox/ground-report", json={**body, "passphrase": "Xq7#pw"})
    assert r.status_code == 422 and "Xq7#pw" not in r.text

    pkg = client.post("/api/v1/admin/outbox/ground-report", json={**body, "passphrase": PASS}).json()
    assert pkg["encrypted"] and all(f["name"].endswith(".ambk") for f in pkg["files"])
    folder = settings.data_dir / "outbox" / str(pkg["id"])
    assert all(p.suffix == ".ambk" for p in folder.iterdir())  # no plaintext copy on disk
    sealed = folder / pkg["files"][0]["name"]
    report = json.loads(crypto.decrypt_bytes(sealed.read_bytes(), PASS))
    assert report["package"]["id"] == pkg["id"] and report["sections_included"] == ["alerts"]

    out = tmp_path / "report.json"
    monkeypatch.setattr("sys.stdin", io.StringIO(PASS + "\n"))
    assert crypto.main([str(sealed), str(out), "--passphrase-stdin"]) == 0
    assert json.loads(out.read_bytes()) == report
    monkeypatch.setattr("sys.stdin", io.StringIO(OTHER + "\n"))
    assert crypto.main([str(sealed), str(tmp_path / "other.json"), "--passphrase-stdin"]) == 1

    assert PASS not in json.dumps(client.get("/api/v1/admin/audit").json())
    assert PASS.encode() not in _db_bytes(settings)


def test_ground_report_requests_are_validated_before_anything_is_written(client, sim, settings):
    post = lambda **over: client.post("/api/v1/admin/outbox/ground-report", json=_report_body(sim, client, **over))
    today = _report_body(sim, client)["last_day"]
    assert post(last_day=today + 1).status_code == 422
    assert post(first_day=3, last_day=2).status_code == 422
    assert post(first_day=0).status_code == 422
    assert post(sections=[]).status_code == 422
    assert post(sections=["vitals"]).status_code == 422
    assert post(astronaut_id=9999).status_code == 404
    assert post(reason=" ").status_code == 422
    assert post(authorized_by="").status_code == 422
    assert client.get("/api/v1/admin/outbox").json() == []
    outbox_root = settings.data_dir / "outbox"
    assert not outbox_root.exists() or not any(outbox_root.iterdir())
