import json
import logging
import time
import uuid

from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.decorators.http import require_POST

from chat.models import ChatSession, ChatMessage
from chat import llm

_feedback_log = logging.getLogger("chat.feedback")

# Rate limiting: max 10 requests per minute per IP
_RATE_LIMIT      = 10
_RATE_WINDOW     = 60   # seconds


def _check_rate_limit(request) -> bool:
    """Return True if request should be blocked (limit exceeded)."""
    ip  = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
    key = f"rl:{ip}"
    count = cache.get(key, 0)
    if count >= _RATE_LIMIT:
        return True
    cache.set(key, count + 1, _RATE_WINDOW)
    return False


def _get_or_create_session(request) -> ChatSession:
    sid = request.session.get("chat_session_id")
    if sid:
        session, _ = ChatSession.objects.get_or_create(session_id=sid)
    else:
        sid = str(uuid.uuid4())
        request.session["chat_session_id"] = sid
        session = ChatSession.objects.create(session_id=sid)

    # Track all session IDs for this browser
    ids = request.session.get("chat_session_ids", [])
    if sid not in ids:
        ids.insert(0, sid)
        request.session["chat_session_ids"] = ids

    return session


@ensure_csrf_cookie
def index(request):
    session = _get_or_create_session(request)
    messages = session.messages.order_by("timestamp")
    return render(request, "chat/index.html", {"messages": messages})


@require_POST
def send_message(request):
    if _check_rate_limit(request):
        return JsonResponse({"error": "Çok fazla istek gönderdiniz. Lütfen bir dakika bekleyin."}, status=429)

    try:
        data     = json.loads(request.body)
        question = data.get("question", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not question:
        return JsonResponse({"error": "Empty question"}, status=400)
    if len(question) > 1000:
        return JsonResponse({"error": "Message too long (max 1000 characters)"}, status=400)

    session = _get_or_create_session(request)
    ChatMessage.objects.create(session=session, role="user", content=question)

    all_msgs = list(session.messages.order_by("timestamp"))
    history = [{"role": m.role, "content": m.content} for m in all_msgs[:-1]]

    start = time.time()
    answer = llm.chat(question, history)
    elapsed_ms = int((time.time() - start) * 1000)

    msg = ChatMessage.objects.create(
        session=session,
        role="assistant",
        content=answer,
        response_time_ms=elapsed_ms,
    )

    return JsonResponse({"answer": answer, "response_time_ms": elapsed_ms, "message_id": msg.id})


@csrf_exempt
@require_POST
def stream_message(request):
    if _check_rate_limit(request):
        return JsonResponse({"error": "Çok fazla istek gönderdiniz. Lütfen bir dakika bekleyin."}, status=429)

    try:
        data     = json.loads(request.body)
        question = data.get("question", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not question:
        return JsonResponse({"error": "Empty question"}, status=400)
    if len(question) > 1000:
        return JsonResponse({"error": "Message too long"}, status=400)

    session = _get_or_create_session(request)
    ChatMessage.objects.create(session=session, role="user", content=question)

    all_msgs = list(session.messages.order_by("timestamp"))
    history = [{"role": m.role, "content": m.content} for m in all_msgs[:-1]]

    def event_stream():
        full_answer = []
        start = time.time()
        for token in llm.chat_stream(question, history):
            full_answer.append(token)
            yield f"data: {json.dumps({'token': token})}\n\n"

        answer = llm._fix_vowel_harmony("".join(full_answer))
        elapsed_ms = int((time.time() - start) * 1000)
        msg = ChatMessage.objects.create(
            session=session, role="assistant",
            content=answer, response_time_ms=elapsed_ms,
        )
        yield f"data: {json.dumps({'done': True, 'response_time_ms': elapsed_ms, 'corrected_text': answer, 'message_id': msg.id})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
def clear_history(request):
    session = _get_or_create_session(request)
    session.messages.all().delete()
    return JsonResponse({"status": "cleared"})


def api_status(request):
    from django.conf import settings
    return JsonResponse({
        "status": "ok",
        "model":  settings.OLLAMA_MODEL,
        "ollama_url": settings.OLLAMA_URL,
        "knowledge_entries": ChatMessage._meta.app_label and __import__(
            'chat.models', fromlist=['KnowledgeEntry']
        ).KnowledgeEntry.objects.count(),
    })


def list_sessions(request):
    ids = request.session.get("chat_session_ids", [])
    current_sid = request.session.get("chat_session_id")
    sessions = []
    for sid in ids:
        try:
            s = ChatSession.objects.get(session_id=sid)
            first_msg = s.messages.filter(role="user").order_by("timestamp").first()
            sessions.append({
                "id": sid,
                "preview": first_msg.content[:60] if first_msg else "Yeni Sohbet",
                "last_activity": s.last_activity.isoformat(),
                "is_current": sid == current_sid,
            })
        except ChatSession.DoesNotExist:
            pass
    return JsonResponse({"sessions": sessions})


@require_POST
def switch_session(request):
    try:
        data = json.loads(request.body)
        sid  = data.get("session_id", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    ids = request.session.get("chat_session_ids", [])
    if sid not in ids:
        return JsonResponse({"error": "Session not found"}, status=404)

    request.session["chat_session_id"] = sid
    return JsonResponse({"status": "ok"})


@require_POST
def new_session(request):
    sid = str(uuid.uuid4())
    ChatSession.objects.create(session_id=sid)
    request.session["chat_session_id"] = sid
    ids = request.session.get("chat_session_ids", [])
    ids.insert(0, sid)
    request.session["chat_session_ids"] = ids
    return JsonResponse({"session_id": sid})


@require_POST
def message_feedback(request, message_id):
    try:
        data     = json.loads(request.body)
        feedback = data.get("feedback")
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if feedback not in ("up", "down", None):
        return JsonResponse({"error": "invalid feedback value"}, status=400)

    try:
        msg = ChatMessage.objects.get(pk=message_id)
        msg.feedback = feedback
        msg.save(update_fields=["feedback"])

        if feedback == "down":
            preceding = ChatMessage.objects.filter(
                session=msg.session, role="user", timestamp__lt=msg.timestamp
            ).order_by("-timestamp").first()
            _feedback_log.warning(
                "DOWNVOTE | id=%d | Q: %.300s | A: %.300s",
                msg.id,
                preceding.content if preceding else "—",
                msg.content,
            )

        return JsonResponse({"status": "ok"})
    except ChatMessage.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
