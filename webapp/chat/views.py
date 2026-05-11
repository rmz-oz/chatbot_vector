import json
import logging
import time
import uuid

from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.decorators.http import require_POST

from chat.models import ChatSession, ChatMessage, KnowledgeEntry, SessionDocumentChunk
from chat import llm

_feedback_log = logging.getLogger("chat.feedback")


def _save_golden_answer(question: str, answer: str, message_id: int) -> None:
    """Save an upvoted Q&A pair as a golden KnowledgeEntry for future retrieval."""
    source_url = f"golden://{message_id}"
    vector = llm.get_embedding(question)
    entry, created = KnowledgeEntry.objects.update_or_create(
        source_url=source_url,
        defaults={
            "title":    question[:200],
            "content":  answer,
            "category": "golden",
            "embedding": vector,
        },
    )
    _feedback_log.info(
        "GOLDEN %s | id=%d | Q: %.200s",
        "created" if created else "updated",
        message_id,
        question,
    )

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
    category = llm._classify_category(question)
    ChatMessage.objects.create(session=session, role="user", content=question, category=category)

    all_msgs = list(session.messages.order_by("timestamp"))
    history = [{"role": m.role, "content": m.content} for m in all_msgs[:-1]]

    start = time.time()
    answer = llm.chat(question, history, session=session)
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
    category = llm._classify_category(question)
    ChatMessage.objects.create(session=session, role="user", content=question, category=category)

    all_msgs = list(session.messages.order_by("timestamp"))
    history = [{"role": m.role, "content": m.content} for m in all_msgs[:-1]]

    def event_stream():
        full_answer = []
        start = time.time()
        for token in llm.chat_stream(question, history, session=session):
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

        preceding = ChatMessage.objects.filter(
            session=msg.session, role="user", timestamp__lt=msg.timestamp
        ).order_by("-timestamp").first()

        if feedback == "down":
            _feedback_log.warning(
                "DOWNVOTE | id=%d | Q: %.300s | A: %.300s",
                msg.id,
                preceding.content if preceding else "—",
                msg.content,
            )

        if feedback == "up" and preceding:
            _save_golden_answer(preceding.content, msg.content, msg.id)

        return JsonResponse({"status": "ok"})
    except ChatMessage.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)


@require_POST
def upload_document(request):
    if _check_rate_limit(request):
        return JsonResponse({"error": "Çok fazla istek gönderdiniz. Lütfen bir dakika bekleyin."}, status=429)

    file_obj = request.FILES.get("file")
    if not file_obj:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    filename = file_obj.name.lower()
    if not (filename.endswith(".pdf") or filename.endswith(".txt")):
        return JsonResponse({"error": "Only PDF and TXT files are supported"}, status=400)

    session = _get_or_create_session(request)

    # Read text
    text = ""
    try:
        if filename.endswith(".pdf"):
            import pdfplumber
            with pdfplumber.open(file_obj) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        else:
            text = file_obj.read().decode("utf-8")
    except Exception as e:
        logger.error("Document upload parse error: %s", e)
        return JsonResponse({"error": "Could not parse document"}, status=400)

    text = text.strip()
    if not text:
        return JsonResponse({"error": "Document is empty or text could not be extracted"}, status=400)

    # Chunk the text into roughly 1000 character pieces
    chunk_size = 1000
    overlap = 200
    chunks = []
    
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap

    # Delete existing chunks for this session so we only query the new document (or we could keep them, but user said "sadece o dosya içeriği")
    session.document_chunks.all().delete()

    created_chunks = 0
    for c in chunks:
        # Get embedding
        embedding = llm.get_embedding(c)
        if embedding:
            SessionDocumentChunk.objects.create(
                session=session,
                file_name=file_obj.name,
                content=c,
                embedding=embedding
            )
            created_chunks += 1

    if created_chunks == 0:
        return JsonResponse({"error": "Failed to process document embeddings"}, status=500)

    return JsonResponse({"status": "ok", "filename": file_obj.name, "chunks": created_chunks})
