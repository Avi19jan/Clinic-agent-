"""API + agent-loop tests using a scripted fake model (no network, no API key)."""
from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import agent, db, tools
from app.main import app


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_DB", str(tmp_path / "t.db"))
    db.init_db()
    tools._FAILS.clear()
    agent.reset_sessions()


def tool_use(name, **inp):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=f"tu_{name}", name=name, input=inp)])


def text(t):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=t)])


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.messages = self

    def create(self, **kw):
        return self.script.pop(0)


def use_fake(monkeypatch, script):
    monkeypatch.setattr(agent, "_client", lambda: FakeClient(script))


SID = "session-12345"


def post(c, msg):
    return c.post("/api/chat", json={"session_id": SID, "message": msg})


def test_chat_without_api_key_returns_503(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent._client.cache_clear()
    with TestClient(app) as c:
        assert post(c, "hi").status_code == 503


def test_booking_flow_updates_schedule(monkeypatch):
    d = date.today() + timedelta(days=14)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    slot = f"{d}T10:00"
    use_fake(monkeypatch, [
        tool_use("book_appointment", patient_name="Asha Rao", phone="555-0200", starts_at=slot, reason="Check-up"),
        text("You're booked."),
    ])
    with TestClient(app) as c:
        r = post(c, "yes, book it").json()
        assert r["reply"] == "You're booked." and r["schedule_changed"] and not r["escalated"]
        assert r["tool_calls"][0]["result"]["ok"]
        grid = c.get(f"/api/schedule?start={d}&days=1").json()["days"][0]["slots"]
        assert next(s for s in grid if s["starts_at"] == slot)["appointment"]["patient_name"] == "Asha Rao"


def test_abusive_escalation_locks_session_without_calling_model(monkeypatch):
    use_fake(monkeypatch, [tool_use("escalate_to_human", category="abusive", summary="Threats."), text("Goodbye.")])
    with TestClient(app) as c:
        r = post(c, "...").json()
        assert r["escalated"] and r["locked"]
        use_fake(monkeypatch, [])  # any model call would now raise IndexError
        r2 = post(c, "hello?").json()
        assert r2["locked"] and r2["tool_calls"] == []


def test_model_failure_rolls_back_history(monkeypatch):
    class Boom:
        messages = None
        def __init__(self): self.messages = self
        def create(self, **kw): raise RuntimeError("boom")
    monkeypatch.setattr(agent, "_client", lambda: Boom())
    with TestClient(app, raise_server_exceptions=False) as c:
        assert post(c, "hi").status_code == 500
    assert agent.SESSIONS[SID].messages == []


def test_validation_and_reset():
    with TestClient(app) as c:
        assert c.post("/api/chat", json={"session_id": "x", "message": "hi"}).status_code == 422
        assert c.post("/api/chat", json={"session_id": SID, "message": "   "}).status_code == 422
        assert c.post("/api/reset").json() == {"status": "reset"}
        assert c.get("/api/health").json()["status"] == "ok"
