"""The six tools the agent can call.

Business rules (hours, double-booking, identity checks) are enforced HERE, in code,
so they hold even if the model misbehaves or a caller tries to talk it into something.
Every tool returns a JSON-serialisable dict: {"ok": True, ...} or {"ok": False, "error": ...}.
"""
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta

from .db import (BOOKING_WINDOW_DAYS, PROVIDER, SLOT_FMT, SLOT_MINUTES, connect, day_slots,
                 norm_name, norm_phone)

log = logging.getLogger("clinic.tools")
MAX_UPCOMING_PER_PATIENT = 3
MAX_FAILED_IDENTITY_CHECKS = 3
_FAILS: dict[str, int] = {}  # session_id -> failed identity checks


class ToolError(Exception):
    """A problem the model can explain to the caller."""


@dataclass
class Ctx:
    session_id: str


# ---------- helpers ----------

def _now() -> datetime:
    return datetime.now()


def _parse_date(s: str) -> Date:
    try:
        return Date.fromisoformat(str(s).strip())
    except ValueError:
        raise ToolError("date must be in YYYY-MM-DD format")


def _valid_slot(s: str) -> str:
    try:
        dt = datetime.strptime(str(s).strip()[:16], SLOT_FMT)
    except ValueError:
        raise ToolError("Time must look like 2026-10-06T10:30 (YYYY-MM-DDTHH:MM).")
    if dt.strftime(SLOT_FMT) not in day_slots(dt.date()):
        raise ToolError("That time is outside clinic hours: Mon-Fri, 09:00-17:00, closed 13:00-14:00, "
                        f"{SLOT_MINUTES}-minute slots.")
    if dt <= _now():
        raise ToolError("That time has already passed.")
    if dt.date() > Date.today() + timedelta(days=BOOKING_WINDOW_DAYS):
        raise ToolError(f"Appointments can only be booked up to {BOOKING_WINDOW_DAYS} days ahead.")
    return dt.strftime(SLOT_FMT)


def _view(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "patient_name": row["patient_name"],
            "starts_at": row["starts_at"], "reason": row["reason"]}


def _same_person(row, name: str, phone: str) -> bool:
    return norm_name(row["patient_name"]) == norm_name(name) and norm_phone(row["phone"]) == norm_phone(phone)


def _guard_identity(ctx: Ctx) -> None:
    if _FAILS.get(ctx.session_id, 0) >= MAX_FAILED_IDENTITY_CHECKS:
        raise ToolError("Too many failed identity checks in this conversation. Do not retry; "
                        "call escalate_to_human with category 'suspicious_identity'.")


def _fail_identity(ctx: Ctx) -> None:
    _FAILS[ctx.session_id] = _FAILS.get(ctx.session_id, 0) + 1


def _clean_identity(name: str, phone: str) -> tuple[str, str]:
    name, phone = " ".join(str(name).split()), str(phone)
    if len(name) < 2:
        raise ToolError("A full name is required.")
    if len(norm_phone(phone)) < 7:
        raise ToolError("A valid phone number is required.")
    return name, phone


def _verified_appointment(conn, ctx: Ctx, appointment_id: int, name: str, phone: str) -> sqlite3.Row:
    """Only returns an active appointment if name AND phone match. Errors are deliberately generic."""
    _guard_identity(ctx)
    row = conn.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    if not row or row["status"] != "booked" or not _same_person(row, name, phone):
        _fail_identity(ctx)
        raise ToolError("No active appointment matches those details. Ask the caller to re-check "
                        "the name and phone number used when booking.")
    return row


# ---------- the six tools ----------

def check_availability(ctx: Ctx, date: str, days: int = 1) -> dict:
    start, days = _parse_date(date), max(1, min(int(days), 7))
    now = _now().strftime(SLOT_FMT)
    with connect() as c:
        taken = {r["starts_at"] for r in c.execute(
            "SELECT starts_at FROM appointments WHERE status='booked' AND starts_at >= ? AND starts_at < ?",
            (start.isoformat(), (start + timedelta(days=days)).isoformat()))}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        free = [s for s in day_slots(d) if s not in taken and s > now]
        out.append({"date": d.isoformat(), "weekday": d.strftime("%A"),
                    "open_day": bool(day_slots(d)), "available_slots": free})
    return {"ok": True, "provider": PROVIDER, "slot_minutes": SLOT_MINUTES, "availability": out}


def book_appointment(ctx: Ctx, patient_name: str, phone: str, starts_at: str, reason: str) -> dict:
    name, phone = _clean_identity(patient_name, phone)
    reason = " ".join(str(reason).split())
    if not 3 <= len(reason) <= 200:
        raise ToolError("A short reason for the visit (3-200 characters) is required.")
    slot = _valid_slot(starts_at)
    with connect() as c:
        upcoming = [r for r in c.execute(
            "SELECT * FROM appointments WHERE status='booked' AND starts_at >= ?",
            (_now().strftime(SLOT_FMT),)) if _same_person(r, name, phone)]
        if len(upcoming) >= MAX_UPCOMING_PER_PATIENT:
            raise ToolError(f"This patient already has {MAX_UPCOMING_PER_PATIENT} upcoming appointments. "
                            "Offer to reschedule or cancel one instead.")
        try:
            cur = c.execute("INSERT INTO appointments(patient_name, phone, starts_at, reason) VALUES (?,?,?,?)",
                            (name, phone, slot, reason))
        except sqlite3.IntegrityError:
            raise ToolError("That slot was just taken. Call check_availability and offer other times.")
        row = c.execute("SELECT * FROM appointments WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"ok": True, "appointment": _view(row)}


def lookup_appointment(ctx: Ctx, patient_name: str, phone: str) -> dict:
    name, phone = _clean_identity(patient_name, phone)
    _guard_identity(ctx)
    with connect() as c:
        rows = c.execute("SELECT * FROM appointments WHERE status='booked' AND starts_at >= ? "
                         "ORDER BY starts_at", (_now().strftime(SLOT_FMT),)).fetchall()
    mine = [_view(r) for r in rows if _same_person(r, name, phone)]
    if not mine:
        _fail_identity(ctx)
        return {"ok": True, "appointments": [],
                "note": "No upcoming appointments match that name and phone number."}
    return {"ok": True, "appointments": mine}


def reschedule_appointment(ctx: Ctx, appointment_id: int, patient_name: str, phone: str,
                           new_starts_at: str) -> dict:
    name, phone = _clean_identity(patient_name, phone)
    slot = _valid_slot(new_starts_at)
    with connect() as c:
        row = _verified_appointment(c, ctx, int(appointment_id), name, phone)
        if row["starts_at"] <= _now().strftime(SLOT_FMT):
            raise ToolError("Past appointments cannot be changed.")
        if row["starts_at"] == slot:
            raise ToolError("The appointment is already at that time.")
        try:
            c.execute("UPDATE appointments SET starts_at=?, updated_at=datetime('now') WHERE id=?",
                      (slot, row["id"]))
        except sqlite3.IntegrityError:
            raise ToolError("That slot is taken. Call check_availability and offer other times.")
        new = c.execute("SELECT * FROM appointments WHERE id=?", (row["id"],)).fetchone()
    return {"ok": True, "previous_starts_at": row["starts_at"], "appointment": _view(new)}


def cancel_appointment(ctx: Ctx, appointment_id: int, patient_name: str, phone: str) -> dict:
    name, phone = _clean_identity(patient_name, phone)
    with connect() as c:
        row = _verified_appointment(c, ctx, int(appointment_id), name, phone)
        if row["starts_at"] <= _now().strftime(SLOT_FMT):
            raise ToolError("Past appointments cannot be cancelled.")
        c.execute("UPDATE appointments SET status='cancelled', updated_at=datetime('now') WHERE id=?",
                  (row["id"],))
    return {"ok": True, "cancelled": _view(row)}


ESCALATION_GUIDANCE = {
    "medical_emergency": "Tell the caller to stop and call their local emergency number now (112 in India, "
                         "911 in the US) or go to the nearest emergency department. Do not continue booking.",
    "medical_advice": "Explain that you cannot give medical advice. Offer to book a visit; say the note "
                      "has been passed to the clinic team.",
    "abusive": "Politely say you are ending this conversation and that clinic staff may follow up. Stop there.",
    "suspicious_identity": "Say you cannot make changes without verifying identity, and that clinic staff "
                           "will follow up. Do not reveal any appointment details.",
    "out_of_scope": "Say this is something the front desk cannot help with and that the note has been "
                    "passed to the clinic team.",
}


def escalate_to_human(ctx: Ctx, category: str, summary: str, urgency: str = "normal") -> dict:
    if category not in ESCALATION_GUIDANCE:
        raise ToolError(f"category must be one of {sorted(ESCALATION_GUIDANCE)}")
    if urgency not in ("normal", "urgent"):
        urgency = "normal"
    if category == "medical_emergency":
        urgency = "urgent"
    summary = " ".join(str(summary).split())[:500] or "(no summary)"
    with connect() as c:
        cur = c.execute("INSERT INTO escalations(session_id, category, urgency, summary) VALUES (?,?,?,?)",
                        (ctx.session_id, category, urgency, summary))
    return {"ok": True, "escalation_id": cur.lastrowid, "category": category, "urgency": urgency,
            "instruction": ESCALATION_GUIDANCE[category]}


# ---------- registry + schemas for the model ----------

TOOL_FUNCS = {f.__name__: f for f in (check_availability, book_appointment, lookup_appointment,
                                      reschedule_appointment, cancel_appointment, escalate_to_human)}
MUTATING = {"book_appointment", "reschedule_appointment", "cancel_appointment"}

_NAME = {"type": "string", "description": "Caller's full name exactly as given."}
_PHONE = {"type": "string", "description": "Caller's phone number."}
_SLOT = {"type": "string", "description": "Slot start, local clinic time, e.g. 2026-10-06T10:30."}

TOOLS = [
    {"name": "check_availability",
     "description": "List open appointment slots for a date (and optionally the following days). "
                    "Always call this before offering times; never invent availability.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "YYYY-MM-DD"},
         "days": {"type": "integer", "minimum": 1, "maximum": 7,
                  "description": "How many consecutive days to list, starting at date. Default 1."}},
         "required": ["date"]}},
    {"name": "book_appointment",
     "description": "Book a new appointment in an open slot. Only call after the caller has confirmed "
                    "the exact time, their full name, phone number and reason.",
     "input_schema": {"type": "object", "properties": {
         "patient_name": _NAME, "phone": _PHONE, "starts_at": _SLOT,
         "reason": {"type": "string", "description": "Short, non-diagnostic reason for the visit."}},
         "required": ["patient_name", "phone", "starts_at", "reason"]}},
    {"name": "lookup_appointment",
     "description": "Find a caller's upcoming appointments. Requires BOTH full name and phone number; "
                    "returns only appointments matching both.",
     "input_schema": {"type": "object", "properties": {"patient_name": _NAME, "phone": _PHONE},
                      "required": ["patient_name", "phone"]}},
    {"name": "reschedule_appointment",
     "description": "Move an existing appointment to a new open slot. Name and phone must match the "
                    "booking. Only call after the caller confirms the new time.",
     "input_schema": {"type": "object", "properties": {
         "appointment_id": {"type": "integer"}, "patient_name": _NAME, "phone": _PHONE,
         "new_starts_at": _SLOT},
         "required": ["appointment_id", "patient_name", "phone", "new_starts_at"]}},
    {"name": "cancel_appointment",
     "description": "Cancel an existing appointment. Name and phone must match the booking. "
                    "Only call after the caller explicitly confirms the cancellation.",
     "input_schema": {"type": "object", "properties": {
         "appointment_id": {"type": "integer"}, "patient_name": _NAME, "phone": _PHONE},
         "required": ["appointment_id", "patient_name", "phone"]}},
    {"name": "escalate_to_human",
     "description": "Hand the caller to clinic staff and log why. Use for medical emergencies (call this "
                    "FIRST), requests for medical advice, abusive or threatening callers, failed identity "
                    "checks, or anything outside scheduling. Follow the returned instruction.",
     "input_schema": {"type": "object", "properties": {
         "category": {"type": "string", "enum": sorted(ESCALATION_GUIDANCE)},
         "urgency": {"type": "string", "enum": ["normal", "urgent"]},
         "summary": {"type": "string", "description": "1-2 factual sentences for staff. No diagnosis."}},
         "required": ["category", "summary"]}},
]


def dispatch(name: str, args: dict, session_id: str) -> dict:
    fn = TOOL_FUNCS.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown tool '{name}'."}
    try:
        return fn(Ctx(session_id), **(args or {}))
    except ToolError as e:
        return {"ok": False, "error": str(e)}
    except (TypeError, ValueError) as e:
        log.warning("bad arguments for %s: %s", name, e)
        return {"ok": False, "error": "Invalid or missing arguments."}
