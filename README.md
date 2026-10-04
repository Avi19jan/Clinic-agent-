# Clinic front-desk agent

A conversational agent that handles a solo clinic's phone desk: booking, rescheduling, cancelling,
and callers who should be handed to a human. Synthetic data only.

- **Backend:** FastAPI + SQLite + Anthropic tool-calling loop (`backend/`)
- **Frontend:** React + Vite, two screens: *Front desk* (chat) and *Schedule* (week grid + staff hand-offs)

## Run

```bash
# 1. API
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add your ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000

# 2. UI (new terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173

# tests (no API key needed)
cd backend && python -m pytest -q
```

Interactive API docs: http://localhost:8000/docs

## The six tools

| Tool | Purpose | Guardrails enforced in code |
|---|---|---|
| `check_availability(date, days?)` | Open slots | Only future, in-hours, unbooked slots |
| `book_appointment(patient_name, phone, starts_at, reason)` | Book | Hours/30-min grid/60-day window; DB unique index prevents double-booking; max 3 upcoming per patient |
| `lookup_appointment(patient_name, phone)` | Find a patient's visits | Name **and** phone must match |
| `reschedule_appointment(appointment_id, patient_name, phone, new_starts_at)` | Move | Identity must match the booking; same slot rules |
| `cancel_appointment(appointment_id, patient_name, phone)` | Cancel | Identity must match; past visits immutable |
| `escalate_to_human(category, summary, urgency?)` | Hand off | Logged; returns what to tell the caller |

Escalation categories: `medical_emergency`, `medical_advice`, `abusive`, `suspicious_identity`, `out_of_scope`.

**Why rules live in code, not just the prompt:** a caller can talk a model into anything. Identity checks,
hours and double-booking are enforced by the tools. After 3 failed identity checks in a session the tools
refuse and tell the agent to escalate. An `abusive` escalation locks the session, so later messages never reach the model.

## REST API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/chat` | `{session_id, message}` → `{reply, tool_calls[], escalated, locked, schedule_changed}` |
| DELETE | `/api/sessions/{id}` | End a conversation |
| GET | `/api/schedule?start=YYYY-MM-DD&days=5` | Slot grid for the staff view |
| GET | `/api/availability?date=YYYY-MM-DD&days=1` | Open slots |
| GET | `/api/escalations` | Latest hand-offs |
| POST | `/api/reset` | Re-seed the synthetic clinic |
| GET | `/api/health` | Status, whether a key is configured |

## Try it

Seeded patient: **Priya Nair, 555-0142** (has an upcoming "Follow-up visit"; look at the Schedule screen for the date).

- "Book me tomorrow afternoon, I'm Sam Lee, 555-0300, for a check-up" → books after confirmation
- "Move Priya Nair's appointment" with the wrong phone number → refused; three misses → escalated
- "I have chest pain and feel dizzy" → immediate `medical_emergency` escalation, no booking
- "What dose of ibuprofen should I take?" → `medical_advice` escalation, offers a visit
- Abuse/threats → warning, then `abusive` escalation and the session locks

## Notes and limits

- Sessions are in memory (lost on restart, single process). Swap `SESSIONS` for Redis/DB to scale out.
- Times are naive clinic-local time; no timezone handling.
- No auth on the REST API: fine for a synthetic demo, not for production.
- Set `CLAUDE_MODEL` in `.env` to change the model.
