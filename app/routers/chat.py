# app/routers/chat.py
"""
Chat router — APP channel for patient messages.

Two transports, ONE brain:
- WS  /chat/ws       — primary transport: persistent WebSocket, JSON frames.
- POST /chat/message — legacy/fallback transport (kept for SMS-parity testing
  and clients that cannot hold a socket open).

Both are THIN wrappers: they do transport + identity only (resolve the patient
from the JWT), then hand off to the shared, channel-agnostic brain in
app/services/chat_core.py. All conversation logic — translation pivot, loss
detection, triage, opt-out, alerting — lives in the brain so the app and SMS
channels share exactly one code path (no care drift).
"""

import asyncio
import base64
import json
import logging
from datetime import date, datetime

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.models.message import Message
from app.models.patient import Patient
from app.schemas.common import ChatMessageRequest, ChatMessageResponse
from app.services.chat_core import process_message
from app.services.alert_service import create_alert
from app.services.message_store import save_inbound, save_outbound
from app.services.red_flags import is_crisis_signal, match_red_flags
from app.services.voice_service import (
    MAX_AUDIO_BYTES,
    SUPPORTED_AUDIO_TYPES,
    VoiceServiceError,
    synthesize_speech,
    transcribe_audio,
)
from app.services.vision_service import (
    MAX_IMAGE_BYTES,
    SUPPORTED_IMAGE_TYPES,
    VisionServiceError,
    analyze_image,
)
from app.utils.access import require_patient

router = APIRouter()
logger = logging.getLogger(__name__)

# Application close codes (4000-4999 range is reserved for applications).
WS_POLICY_VIOLATION = 4401  # missing/invalid/expired token
WS_FORBIDDEN = 4403         # token is valid but not a patient token
WS_NOT_FOUND = 4404         # patient no longer exists / deactivated


def _get_patient(patient_id: str, db: Session) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.post(
    "/message",
    response_model=ChatMessageResponse,
    summary="Send a chat message (app channel — legacy HTTP fallback)",
    description=(
        "Patient-only entry point for app-channel messages. Resolves the patient "
        "from the JWT and hands off to the shared chat brain: multilingual pivot, "
        "PAUSE/STOP/RESUME, pregnancy-loss detection, and per-message triage "
        "(low/medium/high). Returns the reply plus triage_level, loss_detected, "
        "and is_crisis (true only for a pre-approved stored crisis message). "
        "NOTE: the primary chat transport is now the WebSocket at /chat/ws; this "
        "endpoint is kept as a fallback for clients without socket support."
    ),
)
def receive_message(
    body: ChatMessageRequest,
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    """
    Entry point for app-channel patient messages.

    Transport + identity only — the brain does the rest.
    """
    patient = _get_patient(patient_id, db)

    reply = process_message(patient, body.message, channel="app", db=db)

    return ChatMessageResponse(
        reply=reply.text,
        triage_level=reply.triage_level or "low",
        loss_detected=reply.loss_detected,
        is_crisis=reply.is_crisis,
    )


# ── WebSocket transport ───────────────────────────────────────────────────────

HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 100
# Outbound message types surfaced as bell notifications (same set as the
# /notifications REST router — keep in sync).
NOTIFICATION_TYPES = ("reminder", "checkin", "crisis")


def _ws_authenticate(token: str | None) -> tuple[str | None, int, str]:
    """
    Validate a patient JWT for the WebSocket handshake.

    Returns (patient_id, close_code, close_reason). patient_id is None when
    authentication fails, in which case close_code/close_reason describe why.
    Decoding is done here (not via get_current_user) because WebSocket auth
    cannot raise HTTPException — it must close with an application code.
    """
    if not token:
        return None, WS_POLICY_VIOLATION, "Missing token"
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None, WS_POLICY_VIOLATION, "Invalid or expired token"
    if payload.get("type") != "patient":
        return None, WS_FORBIDDEN, "Patient access required"
    return payload["user_id"], 0, ""


async def _ws_send(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_text(json.dumps(payload))


def _message_to_dict(m: Message) -> dict:
    return {
        "id": str(m.id),
        "direction": m.direction,
        "content": m.content,
        "message_type": m.message_type,
        "triage_level": m.triage_level,
        "is_read": m.is_read,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# Sync per-frame handlers — each opens its OWN short-lived DB session (a session
# must never be held open across the lifetime of a long-lived socket) and runs
# in the threadpool so slow work never blocks the event loop.

def _process_chat(patient_id: str, msg: str, client_msg_id: str | None) -> dict:
    db: Session = SessionLocal()
    try:
        patient = (
            db.query(Patient)
            .filter(Patient.id == patient_id, Patient.is_active.is_(True))
            .first()
        )
        if not patient:
            return {"_gone": True}
        reply = process_message(patient, msg, channel="app", db=db)
        return {
            "type": "reply",
            "client_msg_id": client_msg_id,
            "reply": reply.text,
            "triage_level": reply.triage_level or "low",
            "loss_detected": reply.loss_detected,
            "is_crisis": reply.is_crisis,
        }
    finally:
        db.close()


def _fetch_history(patient_id: str, limit: int, before: str | None) -> dict:
    """Newest-first page of the patient's messages; `before` is the cursor."""
    db: Session = SessionLocal()
    try:
        q = db.query(Message).filter(Message.patient_id == patient_id)
        if before:
            try:
                before_dt = datetime.fromisoformat(before)
            except ValueError:
                return {"type": "error", "detail": "Invalid 'before' timestamp — use ISO 8601"}
            q = q.filter(Message.created_at < before_dt)
        rows = q.order_by(Message.created_at.desc()).limit(limit).all()
        return {
            "type": "history",
            "items": [_message_to_dict(m) for m in rows],
            "has_more": len(rows) == limit,
        }
    finally:
        db.close()


def _fetch_unread(patient_id: str) -> dict:
    """Unread bell notifications — same query as GET /notifications/unread."""
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(Message)
            .filter(
                Message.patient_id == patient_id,
                Message.direction == "out",
                Message.message_type.in_(NOTIFICATION_TYPES),
                Message.is_read.is_(False),
            )
            .order_by(Message.created_at.desc())
            .all()
        )
        return {"type": "unread_notifications", "items": [_message_to_dict(m) for m in rows]}
    finally:
        db.close()


def _ack_notifications(patient_id: str, message_ids: list) -> dict:
    """Mark the listed messages read — same semantics as POST /notifications/acknowledge."""
    db: Session = SessionLocal()
    try:
        acknowledged = []
        for msg_id in message_ids:
            msg = (
                db.query(Message)
                .filter(Message.id == str(msg_id), Message.patient_id == patient_id)
                .first()
            )
            if msg:
                msg.is_read = True
                acknowledged.append(str(msg_id))
        db.commit()
        return {"type": "notifications_acked", "acknowledged": acknowledged}
    finally:
        db.close()


@router.websocket("/ws")
async def chat_websocket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
):
    """
    Primary chat transport: WS /chat/ws?token=<patient JWT>.

    Browsers cannot set an Authorization header on WebSockets, so the JWT is
    passed as a query parameter (an `Authorization: Bearer` header is also
    accepted for non-browser clients).

    Protocol — JSON text frames, routed by "action" (defaults to "message" so
    the original {"message": "..."} frames keep working; bare text tolerated):

      client -> {"action": "message", "message": "<text>", "client_msg_id": "<any>"}
      server -> {"type": "ack", "client_msg_id": ...}        (received, processing)
      server -> {"type": "typing"}                           (reply being generated)
      server -> {"type": "reply", "client_msg_id": ..., "reply": "...",
                 "triage_level": "low|medium|high", "loss_detected": bool,
                 "is_crisis": bool}

      client -> {"action": "history", "limit": 50, "before": "<ISO timestamp>"}
      server -> {"type": "history", "items": [...], "has_more": bool}

      client -> {"action": "ack_notifications", "message_ids": ["<uuid>", ...]}
      server -> {"type": "notifications_acked", "acknowledged": [...]}

      client -> {"action": "ping"}
      server -> {"type": "pong"}

      server -> {"type": "error", "detail": "..."}           (connection stays open)

    On connect the server pushes {"type": "connected"} followed by
    {"type": "unread_notifications", "items": [...]} so the bell renders
    without polling.

    Close codes: 4401 bad/missing token, 4403 not a patient token,
    4404 patient not found/deactivated.
    """
    # Fallback for non-browser clients that send a Bearer header instead.
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

    patient_id, close_code, close_reason = _ws_authenticate(token)

    # Accept first so the client receives a readable close code instead of a
    # raw handshake failure it cannot inspect.
    await websocket.accept()
    if patient_id is None:
        await websocket.close(code=close_code, reason=close_reason)
        return

    logger.info("Chat WS connected | patient=%s", patient_id)

    try:
        # Greet + push unread notifications so the bell is instant, no polling.
        await _ws_send(websocket, {"type": "connected"})
        await _ws_send(websocket, await run_in_threadpool(_fetch_unread, patient_id))

        while True:
            raw_frame = await websocket.receive_text()

            # Parse the frame: JSON preferred, bare text tolerated as a chat message.
            try:
                data = json.loads(raw_frame)
                if not isinstance(data, dict):
                    data = {"action": "message", "message": raw_frame}
            except json.JSONDecodeError:
                data = {"action": "message", "message": raw_frame}

            action = data.get("action", "message")

            # ── heartbeat ────────────────────────────────────────────────────
            if action == "ping":
                await _ws_send(websocket, {"type": "pong"})
                continue

            # ── chat history page ────────────────────────────────────────────
            if action == "history":
                try:
                    limit = min(int(data.get("limit", HISTORY_DEFAULT_LIMIT)), HISTORY_MAX_LIMIT)
                except (TypeError, ValueError):
                    limit = HISTORY_DEFAULT_LIMIT
                result = await run_in_threadpool(
                    _fetch_history, patient_id, max(limit, 1), data.get("before")
                )
                await _ws_send(websocket, result)
                continue

            # ── notification acknowledgement ─────────────────────────────────
            if action == "ack_notifications":
                ids = data.get("message_ids") or []
                if not isinstance(ids, list):
                    await _ws_send(websocket, {"type": "error", "detail": "message_ids must be a list"})
                    continue
                result = await run_in_threadpool(_ack_notifications, patient_id, ids)
                await _ws_send(websocket, result)
                continue

            # ── chat message (default action) ────────────────────────────────
            if action != "message":
                await _ws_send(websocket, {"type": "error", "detail": f"Unknown action '{action}'"})
                continue

            text = data.get("message")
            client_msg_id = data.get("client_msg_id")

            if not text or not str(text).strip():
                await _ws_send(websocket, {
                    "type": "error", "detail": "Empty message", "client_msg_id": client_msg_id,
                })
                continue

            # Immediate delivery ack (optimistic UI), then typing indicator
            # while the brain (LLM + translation) works in the threadpool.
            await _ws_send(websocket, {"type": "ack", "client_msg_id": client_msg_id})
            await _ws_send(websocket, {"type": "typing"})

            result = await run_in_threadpool(
                _process_chat, patient_id, str(text), client_msg_id
            )

            if result.get("_gone"):
                await websocket.close(code=WS_NOT_FOUND, reason="Patient not found")
                return

            # Live "typing-out" effect: stream the (already safety-vetted)
            # reply in small chunks, then the full final frame. Deltas are
            # presentation-only — the complete reply has ALREADY been through
            # the brain (translation, red flags, triage, alerting), so nothing
            # unvetted ever reaches the patient. Clients that ignore "delta"
            # frames keep working unchanged off the final "reply" frame.
            await _ws_send(websocket, {"type": "reply_start",
                                       "client_msg_id": client_msg_id})
            words = result["reply"].split(" ")
            CHUNK = 4
            for i in range(0, len(words), CHUNK):
                await _ws_send(websocket, {
                    "type": "delta",
                    "client_msg_id": client_msg_id,
                    "text": " ".join(words[i:i + CHUNK]) + (" " if i + CHUNK < len(words) else ""),
                })
                await asyncio.sleep(0.045)

            await _ws_send(websocket, result)

    except WebSocketDisconnect:
        logger.info("Chat WS disconnected | patient=%s", patient_id)
    except Exception:  # noqa: BLE001 — never let one socket crash the worker
        logger.exception("Chat WS error | patient=%s", patient_id)
        try:
            await websocket.close(code=1011, reason="Internal error")
        except RuntimeError:
            pass  # already closed


# ── Voice notes (Whisper STT → brain → TTS) ──────────────────────────────────

@router.post(
    "/voice",
    summary="Send a voice note (app channel)",
    description=(
        "Patient-only. Upload a voice note (multipart field `audio`: m4a/mp3/"
        "wav/webm/ogg, ≤25MB). The audio is transcribed (Whisper, hinted with "
        "the patient's language), runs through the SAME chat brain as typed "
        "messages — triage, red flags, loss detection, hospital alerting all "
        "apply — and the reply is returned as text plus a warm spoken MP3 "
        "(base64). If speech synthesis fails the reply is text-only "
        "(audio_base64 null) — care never blocks on voice."
    ),
)
async def voice_message(
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    patient = _get_patient(patient_id, db)

    ext = (audio.filename or "voice.m4a").rsplit(".", 1)[-1].lower()
    if ext not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported audio format '{ext}' — use one of {sorted(SUPPORTED_AUDIO_TYPES)}",
        )
    audio_bytes = await audio.read()
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail="Audio file too large (max 25MB)")
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="Empty audio file")

    # 1) Speech -> text (Whisper auto-detects; patient.language is a hint)
    try:
        transcript = await run_in_threadpool(
            transcribe_audio, audio_bytes, audio.filename or f"voice.{ext}",
            getattr(patient, "language", None),
        )
    except VoiceServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 2) The SAME brain as typed chat — no parallel care path
    reply = await run_in_threadpool(process_message, patient, transcript, "app", db)

    # 3) Text -> warm speech (fail-soft: None → text-only reply)
    audio_out = await run_in_threadpool(synthesize_speech, reply.text)

    return {
        "transcript": transcript,
        "reply": reply.text,
        "triage_level": reply.triage_level or "low",
        "loss_detected": reply.loss_detected,
        "is_crisis": reply.is_crisis,
        "audio_base64": base64.b64encode(audio_out).decode() if audio_out else None,
        "audio_mime": "audio/mpeg" if audio_out else None,
    }


# ── Live voice conversation (OpenAI Realtime API) ────────────────────────────

_REALTIME_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"


def _realtime_instructions(patient: Patient) -> str:
    """Per-patient system instructions for a live voice session."""
    weeks = None
    if getattr(patient, "lmp", None):
        weeks = max((date.today() - patient.lmp).days // 7, 0)
    lang = (getattr(patient, "language", None) or "en").lower()
    lang_name = {"en": "English", "fr": "French", "pt": "Portuguese"}.get(lang, "English")

    post_loss = getattr(patient, "status", "") == "post_loss"
    context = (
        f"You are HASH, a warm maternal-care companion on a live voice call with "
        f"{patient.name}, a {'woman receiving post-pregnancy-loss support' if post_loss else 'pregnant woman'} "
        f"in Cameroon."
    )
    if weeks is not None and not post_loss:
        context += f" She is about {weeks} weeks pregnant."
    if getattr(patient, "risk_level", None):
        context += f" Her clinical risk level is {patient.risk_level}."

    rules = (
        f" You speak as a Cameroonian woman — a maternal nurse with a gentle "
        f"African (Cameroonian) accent, talking like a real person in a normal "
        f"phone conversation: natural, calm, kind, never theatrical. NEVER use "
        f"pet names or endearments ('my dear', 'sweetie', 'dearie') — use her "
        f"first name occasionally, or nothing. "
        f"Speak {lang_name} (switch only if she does). Keep replies short and "
        "unhurried — this is a phone conversation, not a lecture. "
        "HARD RULES: never prescribe medicines or dosages; never diagnose. "
        "If she mentions danger signs (bleeding, severe pain, severe headache, "
        "blurred vision, fever, the baby not moving, thoughts of self-harm), tell "
        "her clearly and calmly to go to her hospital immediately and to also "
        "send the same thing as a chat message so her care team is alerted. "
    )
    if post_loss:
        rules += (
            "She has experienced a pregnancy loss: be gentle, listen more than you "
            "speak, never speculate about the cause, never use phrases like "
            "'it was for the best', and never bring up future pregnancies yourself."
        )
    return context + rules


@router.post(
    "/realtime/session",
    summary="Start a live voice session (Realtime API)",
    description=(
        "Patient-only. Mints a short-lived ephemeral key (~10 min) for the "
        "OpenAI Realtime API so the app can open a direct WebRTC voice "
        "conversation with the HASH companion — configured server-side with "
        "the patient's context (name, weeks, risk, language, post-loss state) "
        "and the non-negotiable care rules. The real API key never reaches "
        "the device. After the call, POST each final user transcript line to "
        "/chat/realtime/transcript so red-flag monitoring still applies."
    ),
)
def create_realtime_session(
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    if not settings.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Live voice is not configured")
    patient = _get_patient(patient_id, db)

    payload = {
        "expires_after": {"anchor": "created_at", "seconds": 600},
        "session": {
            "type": "realtime",
            "model": settings.OPENAI_REALTIME_MODEL,
            "instructions": _realtime_instructions(patient),
            "audio": {
                # Input transcription ON — the app receives
                # conversation.item.input_audio_transcription.completed events
                # and MUST forward each final user transcript to
                # POST /chat/realtime/transcript (red-flag safety net: spoken
                # danger signs raise the same hospital alert as typed ones).
                "input": {
                    "transcription": {"model": settings.OPENAI_STT_MODEL},
                },
                "output": {"voice": settings.OPENAI_REALTIME_VOICE},
            },
        },
    }
    try:
        resp = httpx.post(
            _REALTIME_SECRETS_URL,
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Realtime session mint failed | %d | %s",
                     exc.response.status_code, exc.response.text[:300])
        raise HTTPException(status_code=502, detail="Could not start live voice session")
    except Exception:  # noqa: BLE001
        logger.exception("Realtime session mint error")
        raise HTTPException(status_code=502, detail="Could not start live voice session")

    return {
        "client_secret": data.get("value"),
        "expires_at": data.get("expires_at"),
        "model": settings.OPENAI_REALTIME_MODEL,
        "voice": settings.OPENAI_REALTIME_VOICE,
    }


class RealtimeTranscriptIn(BaseModel):
    """One final user-utterance transcript line from a live voice session."""
    text: str = Field(..., min_length=1, max_length=4000,
                      examples=["I have been having headaches since yesterday"])


@router.post(
    "/realtime/transcript",
    summary="Log a live-voice transcript line (safety net)",
    description=(
        "Patient-only. The app posts each FINAL user transcript line from a "
        "live voice session. The line is stored in the conversation history "
        "and scanned by the deterministic red-flag layer — a spoken danger "
        "sign or crisis phrase raises the same hospital alert as a typed one. "
        "No bot reply is generated (the live session already answered)."
    ),
)
def log_realtime_transcript(
    body: RealtimeTranscriptIn,
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    patient = _get_patient(patient_id, db)

    flags = match_red_flags(body.text)
    triage = "high" if flags else "low"

    in_msg = save_inbound(patient.id, body.text, triage_level=triage,
                          channel="app", message_type="chat",
                          source_lang=(getattr(patient, "language", None) or "en"))
    db.add(in_msg)
    db.commit()

    if flags:
        try:
            create_alert(
                db, patient=patient,
                source="post_loss_crisis" if (patient.status == "post_loss" and is_crisis_signal(flags)) else "message_triage",
                reason=("CRISIS — self-harm language in live voice call"
                        if is_crisis_signal(flags)
                        else f"Red-flag in live voice call: \"{', '.join(flags[:3])}\""),
                triage_level="high",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Realtime transcript alert failed | patient=%s", patient.id)

    return {"stored": True, "triage_level": triage, "red_flags": flags}


# ── Image messages (vision) ───────────────────────────────────────────────────

@router.post(
    "/image",
    summary="Send a photo to the chatbot (app channel)",
    description=(
        "Patient-only. Upload a photo (multipart field `image`: jpg/png/webp/"
        "gif, ≤10MB) with an optional `caption` form field. The vision model "
        "explains medication packages and hospital documents, gives nutrition "
        "feedback on meals, and conservatively assesses possible symptoms — "
        "never prescribing or dosing. The image summary + caption pass through "
        "the deterministic red-flag layer, and a high triage (from the model "
        "OR a red flag) alerts the hospital exactly like a text message. Both "
        "sides are stored in the conversation history."
    ),
)
async def image_message(
    image: UploadFile = File(...),
    caption: str | None = Form(default=None),
    db: Session = Depends(get_db),
    patient_id: str = Depends(require_patient),
):
    patient = _get_patient(patient_id, db)

    ext = (image.filename or "photo.jpg").rsplit(".", 1)[-1].lower()
    if ext not in SUPPORTED_IMAGE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported image format '{ext}' — use one of {sorted(SUPPORTED_IMAGE_TYPES)}",
        )
    image_bytes = await image.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail="Image too large (max 10MB)")
    if not image_bytes:
        raise HTTPException(status_code=422, detail="Empty image file")

    weeks = None
    if getattr(patient, "lmp", None):
        weeks = max((date.today() - patient.lmp).days // 7, 0)
    lang = (getattr(patient, "language", None) or "en").lower()

    try:
        result = await run_in_threadpool(
            lambda: analyze_image(
                image_bytes, ext, caption,
                patient_name=patient.name, weeks=weeks, language=lang,
                post_loss=(patient.status == "post_loss"),
            )
        )
    except VisionServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Deterministic red flags on what the image shows + what she wrote —
    # always wins over the model's own triage (same rule as text chat).
    flag_text = f"{result['summary']} {caption or ''}"
    flags = match_red_flags(flag_text)
    triage = "high" if flags else result["triage_level"]

    # Store both sides in the conversation history (English pivot for inbound).
    in_msg = save_inbound(
        patient.id, f"[photo] {result['summary']}" + (f' — "{caption.strip()}"' if caption and caption.strip() else ""),
        triage_level=triage, channel="app", source_lang=lang,
    )
    db.add(in_msg)
    out_msg = save_outbound(patient.id, result["reply"], channel="app", source_lang=lang)
    db.add(out_msg)
    db.commit()

    if triage == "high":
        try:
            create_alert(
                db, patient=patient,
                source="post_loss_crisis" if (patient.status == "post_loss" and is_crisis_signal(flags)) else "message_triage",
                reason=(f"Red-flag in photo message: \"{', '.join(flags[:3])}\"" if flags
                        else f"High-acuity photo: {result['summary'][:120]}"),
                triage_level="high",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Image alert failed | patient=%s", patient.id)

    return {
        "summary": result["summary"],
        "reply": result["reply"],
        "triage_level": triage,
        "red_flags": flags,
    }
