from datetime import date, timedelta

import pytest

from app import db, tools

CTX = tools.Ctx("test-session")


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_DB", str(tmp_path / "t.db"))
    db.init_db()
    tools._FAILS.clear()


def free_slots(n=3):
    d = date.today() + timedelta(days=14)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return tools.check_availability(CTX, d.isoformat())["availability"][0]["available_slots"][:n]


def book(slot, name="Asha Rao", phone="555-0200"):
    return tools.dispatch("book_appointment", {"patient_name": name, "phone": phone,
                                               "starts_at": slot, "reason": "Check-up"}, "test-session")


def test_book_then_slot_disappears_and_double_booking_fails():
    a, _, _ = free_slots()
    assert book(a)["ok"]
    assert a not in free_slots(14)
    again = book(a, name="Someone Else", phone="555-0999")
    assert not again["ok"] and "taken" in again["error"]


def test_hours_enforced():
    d = date.today() + timedelta(days=14)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    assert not book(f"{d}T13:00")["ok"]    # lunch
    assert not book(f"{d}T17:00")["ok"]    # after close
    assert not book(f"{d}T09:10")["ok"]    # off the 30-minute grid
    sat = d + timedelta(days=(5 - d.weekday()) % 7)
    assert not book(f"{sat}T10:00")["ok"]  # weekend


def test_reschedule_requires_matching_name_and_phone():
    a, b, _ = free_slots()
    appt = book(a)["appointment"]
    wrong = tools.dispatch("reschedule_appointment", {"appointment_id": appt["id"], "patient_name": "Asha Rao",
                           "phone": "555-9999", "new_starts_at": b}, "test-session")
    assert not wrong["ok"]
    ok = tools.dispatch("reschedule_appointment", {"appointment_id": appt["id"], "patient_name": " asha  RAO ",
                        "phone": "(555) 0200", "new_starts_at": b}, "test-session")
    assert ok["ok"] and ok["appointment"]["starts_at"] == b
    assert a in free_slots(14)


def test_cancel_frees_slot_and_cannot_repeat():
    a, _, _ = free_slots()
    appt = book(a)["appointment"]
    args = {"appointment_id": appt["id"], "patient_name": "Asha Rao", "phone": "555-0200"}
    assert tools.dispatch("cancel_appointment", args, "test-session")["ok"]
    assert a in free_slots(14)
    assert not tools.dispatch("cancel_appointment", args, "test-session")["ok"]


def test_lookup_only_returns_matching_patient():
    a, _, _ = free_slots()
    book(a)
    assert len(tools.lookup_appointment(CTX, "Asha Rao", "555-0200")["appointments"]) == 1
    assert tools.lookup_appointment(CTX, "Asha Rao", "555-0000")["appointments"] == []


def test_identity_lockout_after_repeated_failures():
    a, _, _ = free_slots()
    appt = book(a)["appointment"]
    bad = {"appointment_id": appt["id"], "patient_name": "Asha Rao", "phone": "555-1111"}
    for _ in range(3):
        assert not tools.dispatch("cancel_appointment", bad, "test-session")["ok"]
    good = dict(bad, phone="555-0200")
    res = tools.dispatch("cancel_appointment", good, "test-session")
    assert not res["ok"] and "escalate_to_human" in res["error"]


def test_escalation_is_logged_with_guidance():
    r = tools.dispatch("escalate_to_human", {"category": "medical_emergency",
                       "summary": "Caller reports chest pain."}, "test-session")
    assert r["ok"] and r["urgency"] == "urgent" and "emergency" in r["instruction"].lower()
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM escalations").fetchone()[0] == 1
