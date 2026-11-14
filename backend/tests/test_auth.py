"""Sign-in, sessions and role-based access (doctor sees everyone, an astronaut only themself)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from am.api.v1 import (
    admin, auth, companion, core, crew, crewlog, guidance, journal_ext, monitoring, notes, research, streaks,
    symptoms, testbench,
)
from am.auth import guard
from am.auth.passwords import generate_password, hash_password, verify_password
from am.auth.service import LOGIN_MAX_FAILURES, SESSION_COOKIE
from am.db.base import real_utcnow
from am.db.models_m6 import AuthSession, UserAccount
from tests.conftest import TEST_PASSWORD, sign_in

API_MODULES = (admin, auth, companion, core, crew, crewlog, guidance, journal_ext, monitoring, notes, research,
               streaks, symptoms, testbench)
PUBLIC_PATHS = {"/api/v1/auth/state", "/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/setup"}


def _operations(client):
    spec = client.app.openapi()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            yield method.upper(), path, op


def _fill(path: str, value: int = 1) -> str:
    """Replace every {param} with a value (ids that may not exist are fine for access checks)."""
    import re

    return re.sub(r"\{[^}]+\}", str(value), path)


@pytest.fixture
def two(client):
    """Two simulated astronauts (A has an open alert) created by the doctor."""
    a = client.post("/api/v1/simulation/run", json={"scenario": "acute_rest_hr", "astronaut_code": "AU-A"}).json()
    b = client.post("/api/v1/simulation/run", json={"scenario": "nominal", "astronaut_code": "AU-B"}).json()
    return a["astronaut"]["id"], b["astronaut"]["id"]


# ------------------------------------------------------------------ passwords
def _legacy_scrypt(password: str, n_log2: int = 10) -> str:
    """A hash in the pre-M6a scrypt format, built independently of am.auth.passwords."""
    import base64
    import hashlib
    import secrets

    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=1 << n_log2, r=8, p=1, dklen=32)
    return f"scrypt${n_log2}$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def test_password_hashing_roundtrip_and_tamper_resistance():
    h = hash_password("correct horse 42", "fast")
    assert h.startswith("$argon2id$") and "correct" not in h
    assert verify_password("correct horse 42", h) and not verify_password("correct horse 43", h)
    assert hash_password("correct horse 42", "fast") != h  # random salt
    assert hash_password("correct horse 42").startswith("$argon2id$v=19$m=65536,t=3,p=4$")  # standard profile
    assert not verify_password("x", "$argon2id$v=19$m=99999999,t=1,p=1$AAAA$AAAA")  # absurd memory is refused
    assert not verify_password("x", "scrypt$30$8$1$AAAA$AAAA")  # absurd legacy cost is refused, not computed
    assert not verify_password("x", "not-a-hash") and not verify_password("x", "")
    pw = generate_password()
    assert len(pw) == 14 and pw.count("-") == 2 and pw != generate_password()


def test_legacy_scrypt_hash_verifies_and_is_replaced_on_sign_in(client, anon):
    from am.auth.passwords import needs_rehash

    legacy = _legacy_scrypt("legacy-pass-123")
    assert verify_password("legacy-pass-123", legacy) and not verify_password("legacy-pass-124", legacy)
    assert needs_rehash(legacy) and not needs_rehash(hash_password("x" * 12, "fast"), "fast")
    ctx = client.app.state.ctx
    sign_in(anon, role="doctor", username="legacydoc")
    with ctx.session_factory() as s:
        user = s.query(UserAccount).filter_by(username="legacydoc").one()
        user.password_hash = legacy
        s.commit()
    fresh = TestClient(client.app)
    assert fresh.post("/api/v1/auth/login", json={"username": "legacydoc", "password": "legacy-pass-123"}).status_code == 200
    with ctx.session_factory() as s:
        stored = s.query(UserAccount).filter_by(username="legacydoc").one().password_hash
    assert stored.startswith("$argon2id$") and verify_password("legacy-pass-123", stored)


# ------------------------------------------------------------------ sign-in flow
def test_every_route_requires_sign_in_except_the_sign_in_routes(anon, client):
    seen = 0
    for method, path, _ in _operations(client):
        r = anon.request(method, _fill(path), json={} if method in ("POST", "PUT") else None)
        if path in PUBLIC_PATHS:
            assert r.status_code != 401, (method, path)
        else:
            assert r.status_code == 401, (method, path, r.status_code)
            seen += 1
    assert seen > 80


def test_login_logout_and_generic_failure_message(anon, client):
    user = sign_in(TestClient(client.app), username="medic2")  # creates the account with TEST_PASSWORD
    bad = anon.post("/api/v1/auth/login", json={"username": "medic2", "password": "wrong-password"})
    unknown = anon.post("/api/v1/auth/login", json={"username": "nobody", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()

    ok = anon.post("/api/v1/auth/login", json={"username": " Medic2 ", "password": TEST_PASSWORD,
                                               "device_name": "Lab PC"})
    assert ok.status_code == 200 and ok.json()["role"] == "doctor" and ok.json()["id"] == user.id
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and TEST_PASSWORD not in ok.text
    assert anon.get("/api/v1/auth/me").json()["username"] == "medic2"
    state = anon.get("/api/v1/auth/state").json()
    assert state["signed_in"] and state["user"]["username"] == "medic2" and not state["setup_allowed"]

    with client.app.state.ctx.session_factory() as s:  # only a hash of the token is stored
        token = anon.cookies.get(SESSION_COOKIE)
        rows = s.query(AuthSession).all()
        assert token and all(token != r.token_hash and len(r.token_hash) == 64 for r in rows)
        assert any(r.device_name == "Lab PC" for r in rows)
        assert TEST_PASSWORD not in s.query(UserAccount).filter_by(username="medic2").one().password_hash

    assert anon.post("/api/v1/auth/logout").json() == {"signed_in": False}
    assert anon.get("/api/v1/auth/me").status_code == 401
    anon.cookies.set(SESSION_COOKIE, token)  # a revoked token cannot be replayed
    assert anon.get("/api/v1/auth/me").status_code == 401
    actions = [a["action"] for a in client.get("/api/v1/audit", params={"action_prefix": "auth."}).json()]
    assert "auth.login" in actions and "auth.login_failed" in actions


def test_repeated_failures_are_throttled_per_client_and_username(anon, client):
    sign_in(TestClient(client.app), username="medic3")
    for _ in range(LOGIN_MAX_FAILURES):
        assert anon.post("/api/v1/auth/login", json={"username": "medic3", "password": "x" * 12}).status_code == 401
    locked = anon.post("/api/v1/auth/login", json={"username": "medic3", "password": TEST_PASSWORD})
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    # another username from the same client is not locked
    assert anon.post("/api/v1/auth/login", json={"username": "testdoctor", "password": TEST_PASSWORD}).status_code == 200


def test_first_run_setup_only_while_no_account_exists(settings):
    from am.main import create_app

    with TestClient(create_app(settings)) as c:
        assert c.get("/api/v1/auth/state").json()["setup_allowed"] is True
        short = c.post("/api/v1/auth/setup", json={"username": "chief", "display_name": "Chief", "password": "short"})
        assert short.status_code == 422
        r = c.post("/api/v1/auth/setup", json={"username": "Chief", "display_name": "Chief medical officer",
                                               "password": "a-long-passphrase"})
        assert r.status_code == 201 and r.json()["role"] == "doctor" and r.json()["username"] == "chief"
        assert c.get("/api/v1/auth/me").status_code == 200  # signed in by setup
        again = TestClient(c.app).post("/api/v1/auth/setup", json={
            "username": "second", "display_name": "Second", "password": "another-long-one"})
        assert again.status_code == 409


def test_expired_disabled_and_reset_sessions_stop_working(client, two, as_astronaut):
    a, _ = two
    crew = as_astronaut(a)
    assert crew.get("/api/v1/auth/me").json()["astronaut_id"] == a
    accounts = {x["username"]: x for x in client.get("/api/v1/auth/accounts").json()}
    acc = accounts[f"astro{a}"]

    r = client.post(f"/api/v1/auth/accounts/{acc['id']}/password", json={"password": "new-password-456"})
    assert r.status_code == 200 and "password" not in r.json()
    assert crew.get("/api/v1/auth/me").status_code == 401  # a password reset signs the account out everywhere

    crew = TestClient(client.app)
    assert crew.post("/api/v1/auth/login", json={"username": acc["username"],
                                                  "password": "new-password-456"}).status_code == 200
    client.post(f"/api/v1/auth/accounts/{acc['id']}/disable")
    assert crew.get("/api/v1/auth/me").status_code == 401
    assert crew.post("/api/v1/auth/login", json={"username": acc["username"],
                                                  "password": "new-password-456"}).status_code == 401
    client.post(f"/api/v1/auth/accounts/{acc['id']}/enable")
    assert crew.post("/api/v1/auth/login", json={"username": acc["username"],
                                                  "password": "new-password-456"}).status_code == 200

    with client.app.state.ctx.session_factory() as s:  # expiry uses real time
        for row in s.query(AuthSession).filter_by(user_id=acc["id"]).all():
            row.expires_at = real_utcnow() - timedelta(seconds=1)
        s.commit()
    assert crew.get("/api/v1/auth/me").status_code == 401
    me = client.get("/api/v1/auth/me").json()
    assert client.post(f"/api/v1/auth/accounts/{me['id']}/disable").status_code == 409  # not your own account


def test_account_rules(client, two):
    a, _ = two
    def add(**kw):
        return client.post("/api/v1/auth/accounts", json={"password": "long-enough-pw", "display_name": "X", **kw})
    assert add(username="crew.a", role="astronaut", astronaut_id=a).status_code == 201
    assert add(username="crew.a2", role="astronaut", astronaut_id=a).status_code == 422   # one account per astronaut
    assert add(username="crew.a", role="doctor").status_code == 422                        # username taken
    assert add(username="x", role="doctor").status_code == 422                             # too short
    assert add(username="crew.z", role="astronaut").status_code == 422                     # needs an astronaut record
    assert add(username="doc.z", role="doctor", astronaut_id=a).status_code == 422         # doctor is not a crew record
    assert add(username="crew.q", role="captain").status_code == 422
    assert add(username="crew.m", role="astronaut", astronaut_id=99999).status_code == 404


# ------------------------------------------------------------------ authorization
def test_astronaut_reads_and_writes_only_their_own_records(client, two, as_astronaut):
    a, b = two
    crew_b = as_astronaut(b)
    checked = 0
    for method, path, op in _operations(client):
        params = {p["name"]: p["in"] for p in op.get("parameters", [])}
        if method != "GET" or "astronaut_id" not in params:
            continue
        other, own = (path.replace("{astronaut_id}", str(a)), path.replace("{astronaut_id}", str(b)))
        q_other = {"astronaut_id": a} if params["astronaut_id"] == "query" else {}
        q_own = {"astronaut_id": b} if params["astronaut_id"] == "query" else {}
        endpoint_policy = None
        r = crew_b.get(_fill(other), params=q_other)
        r_own = crew_b.get(_fill(own), params=q_own)
        if r.status_code == 403 and r_own.status_code == 403:
            endpoint_policy = "doctor"  # doctor-only routes refuse a crew member even for their own id
        else:
            assert r.status_code == 403, (path, r.status_code)
            assert r_own.status_code != 403, (path, r_own.status_code)
        checked += 1 if endpoint_policy is None else 0
    assert checked >= 25

    assert [x["id"] for x in crew_b.get("/api/v1/astronauts").json()] == [b]
    assert {x["id"] for x in client.get("/api/v1/astronauts").json()} >= {a, b}


def test_records_addressed_by_id_are_checked_against_their_owner(client, two, as_astronaut):
    a, b = two
    crew_a, crew_b = as_astronaut(a), as_astronaut(b)
    alert = client.get("/api/v1/alerts", params={"astronaut_id": a, "open_only": True}).json()[0]["id"]
    conv = crew_a.post("/api/v1/companion/conversations", json={"astronaut_id": a}).json()["id"]
    task = crew_a.get("/api/v1/missions/today", params={"astronaut_id": a}).json()["tasks"][0]["id"]
    report = crew_a.post("/api/v1/symptoms", json={"astronaut_id": a, "symptom_code": "headache",
                                                    "severity": "mild"}).json()["id"]
    foreign = [
        ("GET", f"/api/v1/alerts/{alert}", None),
        ("POST", f"/api/v1/alerts/{alert}/acknowledge", {"actor": "b", "note": ""}),
        ("GET", f"/api/v1/guidance/alerts/{alert}", None),
        ("GET", f"/api/v1/companion/conversations/{conv}/messages", None),
        ("POST", f"/api/v1/companion/conversations/{conv}/messages", {"content": "hi"}),
        ("POST", f"/api/v1/missions/tasks/{task}", {"status": "done"}),
        ("POST", f"/api/v1/symptoms/{report}/resolve", {"note": ""}),
        ("GET", f"/api/v1/guidance/symptoms/{report}", None),
    ]
    for method, path, body in foreign:
        assert crew_b.request(method, path, json=body).status_code == 403, (method, path)
        assert crew_a.request(method, path, json=body).status_code != 403, (method, path)
    assert crew_b.get("/api/v1/alerts/999999").status_code == 404  # unknown ids are not found, not served


def test_scoped_requests_cannot_be_widened(client, two, as_astronaut):
    a, b = two
    crew_b = as_astronaut(b)
    # no astronaut named at all
    assert crew_b.get("/api/v1/alerts").status_code == 403
    assert crew_b.get("/api/v1/escalations").status_code == 403
    assert client.get("/api/v1/alerts").status_code == 200  # the doctor may list the whole crew
    # parameter pollution: every astronaut_id value must be the caller's own
    assert crew_b.get("/api/v1/alerts", params=[("astronaut_id", b), ("astronaut_id", a)]).status_code == 403
    assert crew_b.get("/api/v1/alerts", params=[("astronaut_id", a), ("astronaut_id", b)]).status_code == 403
    # body and batch items
    assert crew_b.post("/api/v1/checkins", json={"astronaut_id": a, "ratings": {"mood": 3}}).status_code == 403
    assert crew_b.post("/api/v1/checkins", json={"astronaut_id": b, "ratings": {"mood": 3}}).status_code == 201
    item = {"metric_code": "heart_rate", "value": 60, "unit": "bpm", "context": "rest",
            "recorded_at": "2026-11-01T10:00:00", "source_name": "strap"}
    mixed = {"items": [{**item, "astronaut_id": b}, {**item, "astronaut_id": a}]}
    assert crew_b.post("/api/v1/measurements/batch", json=mixed).status_code == 403
    assert crew_b.post("/api/v1/measurements/batch", json={"items": [{**item, "astronaut_id": b}]}).status_code == 201
    # own id in the query does not unlock another astronaut's record in the path
    alert = client.get("/api/v1/alerts", params={"astronaut_id": a, "open_only": True}).json()[0]["id"]
    assert crew_b.get(f"/api/v1/alerts/{alert}", params={"astronaut_id": b}).status_code == 403
    assert crew_b.get(f"/api/v1/journal/{a}/days/1/conversations").status_code == 403
    # garbage ids are refused, not crashed on
    assert crew_b.get("/api/v1/alerts", params={"astronaut_id": "1 OR 1=1"}).status_code in (403, 422)


def test_doctor_only_actions_are_refused_for_crew(client, two, as_astronaut):
    a, _ = two
    crew_a = as_astronaut(a)
    alert = client.get("/api/v1/alerts", params={"astronaut_id": a, "open_only": True}).json()[0]["id"]
    refused = [
        ("POST", f"/api/v1/alerts/{alert}/resolve", {"actor": "me", "note": "all fine"}),
        ("POST", "/api/v1/astronauts", {"code": "X-1", "display_name": "X"}),
        ("POST", "/api/v1/simulation/run", {"scenario": "nominal", "astronaut_code": "X-2"}),
        ("GET", "/api/v1/audit", None), ("GET", "/api/v1/admin/audit", None), ("GET", "/api/v1/settings", None),
        ("PUT", "/api/v1/settings/backup_retention", {"value": 3, "actor": "me"}),
        ("GET", "/api/v1/admin/backups", None), ("GET", "/api/v1/admin/outbox", None),
        ("POST", "/api/v1/admin/backups", {"passphrase": "a long passphrase", "actor": "me", "reason": "copy"}),
        ("GET", "/api/v1/auth/accounts", None),
        ("POST", "/api/v1/auth/accounts", {"username": "evil.doc", "display_name": "D", "role": "doctor",
                                           "password": "long-enough-pw"}),
        ("POST", "/api/v1/knowledge/ingest", None),
        ("POST", "/api/v1/baselines/rebuild", {"astronaut_id": a, "metric_code": "heart_rate", "context": "rest",
                                               "window_start": "2026-01-01T00:00:00",
                                               "window_end": "2026-12-31T00:00:00", "actor": "me", "reason": "mine"}),
    ]
    for method, path, body in refused:
        assert crew_a.request(method, path, json=body).status_code == 403, (method, path)
    # the same alert can still be read and acknowledged by its owner
    assert crew_a.post(f"/api/v1/alerts/{alert}/acknowledge", json={"actor": "me", "note": "seen"}).status_code == 200


def test_cross_origin_writes_are_rejected(client, two):
    a, _ = two
    body = {"astronaut_id": a, "ratings": {"mood": 4}}
    evil = client.post("/api/v1/checkins", json=body, headers={"origin": "http://evil.example"})
    assert evil.status_code == 403 and "Cross-origin" in evil.json()["detail"]
    same = client.post("/api/v1/checkins", json=body, headers={"origin": "http://testserver"})
    assert same.status_code == 201
    assert client.get("/api/v1/alerts", headers={"origin": "http://evil.example"}).status_code == 200  # reads carry no CSRF risk


def test_audit_entries_carry_the_authenticated_username(client, two, as_astronaut):
    a, _ = two
    crew_a = as_astronaut(a)
    alert = client.get("/api/v1/alerts", params={"astronaut_id": a, "open_only": True}).json()[0]["id"]
    crew_a.post(f"/api/v1/alerts/{alert}/acknowledge", json={"actor": "Dr. Somebody Else", "note": "seen"})
    entry = next(e for e in client.get("/api/v1/audit", params={"action_prefix": "alert.ack"}).json())
    assert entry["actor"] == "Dr. Somebody Else"            # the self-declared name is kept as given
    assert entry["payload"]["auth_user"] == f"astro{a}"     # and the real account is recorded next to it


# ------------------------------------------------------------------ policy coverage
def test_every_route_has_a_policy_that_can_be_enforced():
    """A SCOPED route must name an astronaut (directly or through a registered record id);
    otherwise crew members could never call it, or worse, a future change could open it."""
    unresolved, counts = [], {guard.PUBLIC: 0, guard.SIGNED_IN: 0, guard.DOCTOR: 0, guard.SCOPED: 0}
    for module in API_MODULES:
        for route in module.router.routes:
            policy = guard.policy_of(route.endpoint)
            counts[policy] += 1
            if policy != guard.SCOPED:
                continue
            dep = route.dependant
            names = {p.name for p in dep.path_params} | {p.name for p in dep.query_params}
            for b in dep.body_params:
                model = getattr(b, "type_", None) or b.field_info.annotation
                names |= set(getattr(model, "model_fields", {}))
            if not ("astronaut_id" in names or "items" in names or names & set(guard.OWNER_PARAMS)):
                unresolved.append(f"{sorted(route.methods)} {route.path}")
    assert unresolved == []
    assert counts[guard.PUBLIC] == 4 and counts[guard.DOCTOR] >= 25 and counts[guard.SCOPED] >= 40
