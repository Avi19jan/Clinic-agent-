"""REST API. Run: uvicorn app.main:app --reload --port 8000"""
from contextlib import asynccontextmanager
from datetime import date

import anthropic
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import agent, db, tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Clinic front-desk agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


class ChatIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=2000)


class ToolCall(BaseModel):
    name: str
    input: dict
    result: dict


class ChatOut(BaseModel):
    reply: str
    tool_calls: list[ToolCall]
    escalated: bool
    locked: bool
    schedule_changed: bool


@app.get("/api/health")
def health():
    import os
    return {"status": "ok", "llm_configured": bool(os.getenv("ANTHROPIC_API_KEY")), "model": agent.MODEL}


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn):
    text = body.message.strip()
    if not text:
        raise HTTPException(422, "message must not be blank")
    try:
        return agent.chat(body.session_id, text)
    except agent.AgentUnavailable as e:
        raise HTTPException(503, str(e))
    except anthropic.APIError as e:
        raise HTTPException(502, f"Language model error: {getattr(e, 'message', str(e))}")


@app.delete("/api/sessions/{session_id}", status_code=204)
def end_session(session_id: str):
    agent.SESSIONS.pop(session_id, None)
    tools._FAILS.pop(session_id, None)


@app.get("/api/schedule")
def schedule(start: date = Query(default_factory=date.today), days: int = Query(5, ge=1, le=14)):
    return {"provider": db.PROVIDER, "days": db.schedule_grid(start, days)}


@app.get("/api/availability")
def availability(date_: date = Query(alias="date"), days: int = Query(1, ge=1, le=7)):
    return tools.check_availability(tools.Ctx("rest"), date_.isoformat(), days)


@app.get("/api/escalations")
def escalations(limit: int = Query(20, ge=1, le=100)):
    with db.connect() as c:
        rows = c.execute("SELECT * FROM escalations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/reset")
def reset():
    """Demo helper: wipe and re-seed the synthetic clinic, clear all conversations."""
    db.reset_db()
    agent.reset_sessions()
    tools._FAILS.clear()
    return {"status": "reset"}
