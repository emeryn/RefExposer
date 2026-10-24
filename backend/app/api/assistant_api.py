"""Assistant: conversations of a user with the model, and the agent that acts for them.

A conversation belongs to one account: every query filters on `user_id`, and someone else's conversation is
reported as unknown. Answers are streamed as server-sent events; the exchange is stored once it is finished, so
a conversation survives a reload and keeps going until the user resets it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import appsettings
from ..auth import CurrentUser, audit, require_user
from ..db import get_db
from ..llm import agent as agentlib
from .. import watchdog as watchdoglib
from ..llm.provider import LLMError, build
from ..models import ChatMessage, Conversation
from ..service import Service
from .deps import get_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

MAX_MESSAGE_CHARS = 8000
MAX_CONVERSATIONS = 100
_rate: dict[int, deque[float]] = {}
_rate_lock = threading.Lock()


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)
    # Agent mode for this exchange: tools are offered only when an administrator enabled them
    agent: bool = True


class ConversationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=200)


# --------------------------------------------------------------------------- access

def _config(db: Session, user: CurrentUser):
    cfg = appsettings.load(db, "assistant")
    if not agentlib.allowed(cfg, user):
        raise HTTPException(403, "the assistant is not available for this account")
    return cfg


def _provider(db: Session, service: Service, cfg):
    if not cfg.configured:
        raise HTTPException(409, "the assistant has no inference server or no model yet "
                                 "(Settings › Assistant)")
    return build(cfg, service.settings, appsettings.load(db, "proxy"))


def _throttle(user: CurrentUser, per_minute: int) -> None:
    """One generation at a time is cheap to serve; a loop in a script is not."""
    now = time.monotonic()
    with _rate_lock:
        hits = _rate.setdefault(user.id, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= per_minute:
            raise HTTPException(429, "too many questions in the last minute, wait a moment")
        hits.append(now)


def _conversation(db: Session, user: CurrentUser, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(404, "unknown conversation")
    return conv


def _message_dict(m: ChatMessage) -> dict[str, Any]:
    return {"id": m.id, "role": m.role, "content": m.content, "steps": m.steps or [],
            "artifacts": m.artifacts or [], "error": m.error, "created_at": m.created_at}


def _conversation_dict(conv: Conversation, messages: int | None = None) -> dict[str, Any]:
    return {"id": conv.id, "title": conv.title, "model": conv.model, "agent": conv.agent,
            "created_at": conv.created_at, "updated_at": conv.updated_at, "messages": messages}


# --------------------------------------------------------------------------- status

@router.get("", summary="Availability of the assistant for this account")
def status(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """Read by the interface to decide whether the assistant appears in the menu."""
    cfg = appsettings.load(db, "assistant")
    available = agentlib.allowed(cfg, user)
    return {
        "available": available and cfg.configured,
        "enabled": cfg.enabled,
        "configured": cfg.configured,
        "model": cfg.model if available else None,
        "agent": available and cfg.agent_enabled,
        "writes": available and cfg.agent_enabled and cfg.allow_writes,
        "confidential": available and cfg.allow_confidential,
        # Scheduled watchdogs: on, and this account may use them (administrators and advanced users only).
        # The interface shows the menu entry on this alone.
        "watchdogs": available and watchdoglib.enabled(cfg) and watchdoglib.may_use(user),
        "max_steps": cfg.max_steps,
        "max_chars": MAX_MESSAGE_CHARS,
    }


# --------------------------------------------------------------------------- conversations

@router.get("/conversations", summary="Conversations of the account, most recent first")
def list_conversations(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    _config(db, user)
    counts = dict(db.execute(
        select(ChatMessage.conversation_id, func.count(ChatMessage.id)).group_by(ChatMessage.conversation_id)).all())
    rows = db.scalars(select(Conversation).where(Conversation.user_id == user.id)
                      .order_by(Conversation.updated_at.desc()).limit(MAX_CONVERSATIONS)).all()
    return [_conversation_dict(c, counts.get(c.id, 0)) for c in rows]


@router.post("/conversations", summary="Start a conversation", status_code=201)
def create_conversation(body: ConversationIn, db: Session = Depends(get_db),
                        user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    cfg = _config(db, user)
    total = db.scalar(select(func.count(Conversation.id)).where(Conversation.user_id == user.id)) or 0
    if total >= MAX_CONVERSATIONS:
        raise HTTPException(409, f"at most {MAX_CONVERSATIONS} conversations: delete one first")
    conv = Conversation(id=uuid.uuid4().hex, user_id=user.id, title=(body.title or "New conversation")[:200],
                        model=cfg.model, agent=cfg.agent_enabled)
    db.add(conv)
    db.commit()
    return _conversation_dict(conv, 0)


@router.get("/conversations/{conversation_id}", summary="A conversation and its messages")
def get_conversation(conversation_id: str, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _config(db, user)
    conv = _conversation(db, user, conversation_id)
    messages = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
                          .order_by(ChatMessage.id)).all()
    return {**_conversation_dict(conv, len(messages)), "messages": [_message_dict(m) for m in messages]}


@router.patch("/conversations/{conversation_id}", summary="Rename a conversation")
def rename_conversation(conversation_id: str, body: ConversationIn, db: Session = Depends(get_db),
                        user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _config(db, user)
    conv = _conversation(db, user, conversation_id)
    if body.title:
        conv.title = body.title[:200]
    db.commit()
    return _conversation_dict(conv)


@router.delete("/conversations/{conversation_id}", summary="Delete a conversation and its messages")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db),
                        user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _config(db, user)
    conv = _conversation(db, user, conversation_id)
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conv.id))
    db.delete(conv)
    db.commit()
    return {"deleted": conversation_id}


@router.delete("/conversations", summary="Delete every conversation of the account")
def reset_conversations(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _config(db, user)
    ids = list(db.scalars(select(Conversation.id).where(Conversation.user_id == user.id)).all())
    if ids:
        db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(ids)))
        db.execute(delete(Conversation).where(Conversation.id.in_(ids)))
        db.commit()
    return {"deleted": len(ids)}


# --------------------------------------------------------------------------- the exchange

@router.post("/conversations/{conversation_id}/messages", summary="Ask a question (streamed answer)")
def send_message(conversation_id: str, body: MessageIn, request: Request, db: Session = Depends(get_db),
                 service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    """Server-sent events: `token` as the answer is written, `step` for each tool call, `done` at the end.

    The whole exchange is stored when it finishes, so the conversation can be picked up later.
    """
    cfg = _config(db, user)
    provider = _provider(db, service, cfg)
    conv = _conversation(db, user, conversation_id)
    _throttle(user, cfg.rate_per_minute)

    history = [{"role": m.role, "content": m.content}
               for m in db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
                                   .order_by(ChatMessage.id)).all()]
    question = ChatMessage(conversation_id=conv.id, role="user", content=body.content)
    db.add(question)
    if len(history) == 0:
        conv.title = body.content.strip().splitlines()[0][:80] or conv.title
    conv.model = cfg.model
    conv.agent = bool(body.agent and cfg.agent_enabled)
    db.commit()
    history.append({"role": "user", "content": body.content})

    worker = agentlib.Agent(agentlib.Caller.of(request), cfg, provider, user, agent_mode=body.agent)
    audit(db, "assistant.ask", user=user, target=f"conversation:{conv.id}", request=request,
          detail={"agent": worker.agent_mode, "model": cfg.model, "chars": len(body.content)})
    # The session of the request is closed before the stream is consumed: nothing of it is used below
    conversation_id = conv.id

    def events() -> Iterator[str]:
        final: dict[str, Any] = {}
        try:
            for event in worker.run(history):
                if event.get("type") == "done":
                    final = event
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except LLMError as e:  # raised outside the generator of the agent (connection lost)
            final = {"content": "", "steps": [], "artifacts": [], "error": str(e)}
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            _store(conversation_id, final)
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",  # the answer must reach the browser as it is written, not in one block
    })


def _store(conversation_id: str, final: dict[str, Any]) -> None:
    """Write the answer in its own session: the request session is closed once the stream started."""
    from ..db import session_factory

    if not final:
        return
    with session_factory()() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return
        db.add(ChatMessage(conversation_id=conversation_id, role="assistant",
                           content=(final.get("content") or "")[:1_000_000],
                           steps=final.get("steps") or None, artifacts=final.get("artifacts") or None,
                           error=(final.get("error") or None)))
        conv.updated_at = func.now()
        db.commit()


# --------------------------------------------------------------------------- administration

@router.post("/test", summary="Test the inference server and list its models")
def test_connection(request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """Reserved to administrators: it reports what the configured server answers."""
    if not user.is_admin:
        raise HTTPException(403, "reserved to administrators")
    cfg = appsettings.load(db, "assistant")
    if not cfg.base_url:
        raise HTTPException(409, "no inference server configured")
    try:
        health = build(cfg, service.settings, appsettings.load(db, "proxy")).health()
    except LLMError as e:
        return {"ok": False, "error": str(e)}
    return health
