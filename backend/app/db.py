"""SQLite storage and synthetic seed data for a solo clinic. No real data is used."""
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta

PROVIDER = "Dr. Meera Rao"
OPEN_HOUR, CLOSE_HOUR = 9, 17
LUNCH = (13, 14)  # closed 13:00-14:00
SLOT_MINUTES = 30
BOOKING_WINDOW_DAYS = 60
SLOT_FMT = "%Y-%m-%dT%H:%M"

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  patient_name TEXT NOT NULL,
  phone        TEXT NOT NULL,
  starts_at    TEXT NOT NULL,
  reason       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled')),
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
-- the database itself prevents double-booking, even under concurrent requests
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot
  ON appointments(starts_at) WHERE status = 'booked';
CREATE TABLE IF NOT EXISTS escalations (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  category   TEXT NOT NULL,
  urgency    TEXT NOT NULL,
  summary    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SEED_PATIENTS = [
    ("Priya Nair", "555-0142", "Follow-up visit"),
    ("Arjun Mehta", "555-0177", "Annual check-up"),
    ("Sara Khan", "555-0119", "Skin rash review"),
    ("Daniel Okafor", "555-0163", "Blood pressure check"),
    ("Meera Iyer", "555-0188", "Vaccination"),
    ("Tom Becker", "555-0105", "Back pain consultation"),
    ("Lucia Romero", "555-0131", "Lab results discussion"),
    ("Kiran Patel", "555-0156", "Persistent cough"),
]


def db_path() -> str:
    return os.getenv("CLINIC_DB", os.path.join(os.path.dirname(__file__), "..", "clinic.db"))


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def norm_phone(p: str) -> str:
    return "".join(c for c in p if c.isdigit())[-10:]


def norm_name(n: str) -> str:
    return " ".join(n.casefold().split())


def day_slots(d: date) -> list[str]:
    """All bookable slot start times for a day (empty on weekends)."""
    if d.weekday() >= 5:
        return []
    out, t = [], datetime.combine(d, time(OPEN_HOUR))
    end = datetime.combine(d, time(CLOSE_HOUR))
    while t < end:
        if not (LUNCH[0] <= t.hour < LUNCH[1]):
            out.append(t.strftime(SLOT_FMT))
        t += timedelta(minutes=SLOT_MINUTES)
    return out


def init_db(seed: bool = True) -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        if seed and c.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0:
            _seed(c)


def reset_db() -> None:
    with connect() as c:
        c.executescript("DELETE FROM appointments; DELETE FROM escalations; "
                        "DELETE FROM sqlite_sequence;")
    init_db()


def _seed(c: sqlite3.Connection) -> None:
    rng, now = random.Random(7), datetime.now().strftime(SLOT_FMT)
    days, d = [], date.today()
    while len(days) < 5:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    pool = [s for day in days for s in day_slots(day) if s > now]
    for (name, phone, reason), slot in zip(SEED_PATIENTS, sorted(rng.sample(pool, len(SEED_PATIENTS)))):
        c.execute("INSERT INTO appointments(patient_name, phone, starts_at, reason) VALUES (?,?,?,?)",
                  (name, phone, slot, reason))


def schedule_grid(start: date, days: int = 5) -> list[dict]:
    """Per-day slot grid with the booked appointment (if any) in each slot, for the staff view."""
    end = start + timedelta(days=days)
    with connect() as c:
        rows = c.execute("SELECT * FROM appointments WHERE status='booked' "
                         "AND starts_at >= ? AND starts_at < ?",
                         (start.isoformat(), end.isoformat())).fetchall()
    booked = {r["starts_at"]: dict(r) for r in rows}
    grid = []
    for i in range(days):
        d = start + timedelta(days=i)
        slots = day_slots(d)
        grid.append({"date": d.isoformat(), "closed": not slots,
                     "slots": [{"starts_at": s, "appointment": booked.get(s)} for s in slots]})
    return grid
