import json
import time

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from chat.models import ChatSession, ChatMessage
from chat import llm


def _get_or_create_session(request) -> ChatSession:
    sid = request.session.get("chat_session_id")
    if sid:
        session, _ = ChatSession.objects.get_or_create(session_id=sid)
    else:
        import uuid
        sid = str(uuid.uuid4())
        request.session["chat_session_id"] = sid
        session = ChatSession.objects.create(session_id=sid)
    return session


@ensure_csrf_cookie
def index(request):
    session = _get_or_create_session(request)
    messages = session.messages.order_by("timestamp")
    return render(request, "chat/index.html", {"messages": messages})


@require_POST
def send_message(request):
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

    # Save user message
    ChatMessage.objects.create(session=session, role="user", content=question)

    # Build history for context (exclude the message just saved)
    all_msgs = list(session.messages.order_by("timestamp"))
    history = [
        {"role": m.role, "content": m.content}
        for m in all_msgs[:-1]
    ]

    # Get answer from Claude
    start = time.time()
    answer = llm.chat(question, history)
    elapsed_ms = int((time.time() - start) * 1000)

    # Save assistant message
    ChatMessage.objects.create(
        session=session,
        role="assistant",
        content=answer,
        response_time_ms=elapsed_ms,
    )

    return JsonResponse({"answer": answer, "response_time_ms": elapsed_ms})


@require_POST
def clear_history(request):
    session = _get_or_create_session(request)
    session.messages.all().delete()
    return JsonResponse({"status": "cleared"})


def api_status(request):
    from chat.models import KnowledgeEntry
    from django.conf import settings
    return JsonResponse({
        "status": "ok",
        "model":  settings.OLLAMA_MODEL,
        "ollama_url": settings.OLLAMA_URL,
        "knowledge_entries": KnowledgeEntry.objects.count(),
    })
