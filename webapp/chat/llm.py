"""
LLM + Vector RAG integration.

Flow:
1. get_embedding(text)        → Ollama /api/embeddings (nomic-embed-text, 768-dim)
2. retrieve_context(question) → pgvector cosine similarity search
3. smart_excerpt(content, q)  → finds most relevant 2000-char window in large docs
4. chat(question, history)    → builds prompt, calls Ollama /api/chat (llama3.2:3b)

Redis cache:
  - Embeddings: 24h  (same text → same vector, no need to recompute)
  - Answers:     1h
"""

import hashlib
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

EMBED_CACHE_TTL  = 86400   # 24 hours
ANSWER_CACHE_TTL = 3600    # 1 hour


# ─────────────────────────────────────────────────────────────────────────────
# 1. EMBEDDING
# ─────────────────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> list[float] | None:
    """Return 768-dim embedding vector via Ollama nomic-embed-text."""
    cache_key = "emb:" + hashlib.md5(text.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        resp = requests.post(
            f"{settings.OLLAMA_URL}/api/embeddings",
            json={"model": settings.EMBEDDING_MODEL, "prompt": text},
            timeout=60,
        )
        resp.raise_for_status()
        vector = resp.json()["embedding"]
        cache.set(cache_key, vector, EMBED_CACHE_TTL)
        return vector
    except Exception as e:
        logger.error("Embedding error: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. VECTOR RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_context(question: str) -> list:
    """
    Semantic search: find most similar KnowledgeEntry rows via pgvector
    cosine distance. Falls back to keyword search if embeddings unavailable.
    """
    from pgvector.django import CosineDistance

    vector = get_embedding(question)

    if vector:
        entries = list(
            KnowledgeEntry.objects
            .exclude(embedding=None)
            .order_by(CosineDistance("embedding", vector))
            [:settings.RAG_MAX_ENTRIES]
        )
        if entries:
            return entries

    # Fallback: keyword scoring
    logger.warning("Vector search unavailable — falling back to keyword search")
    words = [w for w in question.lower().split() if len(w) > 2]
    scored = []
    for entry in KnowledgeEntry.objects.all():
        combined = (entry.title + " " + entry.keywords + " " + entry.content).lower()
        score = sum(
            3 if w in entry.keywords.lower() else 1
            for w in words if w in combined
        )
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda t: -t[0])
    return [e for _, e in scored[:settings.RAG_MAX_ENTRIES]]


# ─────────────────────────────────────────────────────────────────────────────
# 3. SMART EXCERPT
# ─────────────────────────────────────────────────────────────────────────────

def smart_excerpt(content: str, question: str, window: int = 2000, step: int = 200) -> str:
    """
    For long documents: slide a window and return the 2000-char slice
    where question keywords appear most densely.
    """
    if len(content) <= window:
        return content

    words = [w for w in question.lower().split() if len(w) > 2]
    best_score, best_start = -1, 0

    for start in range(0, len(content) - window, step):
        chunk = content[start: start + window].lower()
        score = sum(chunk.count(w) for w in words)
        if score > best_score:
            best_score, best_start = score, start

    return content[best_start: best_start + window]


# ─────────────────────────────────────────────────────────────────────────────
# 4. CHAT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sen Acıbadem Mehmet Ali Aydınlar Üniversitesi'nin resmi AI asistanısın.
Görevin: yalnızca aşağıda verilen üniversite bilgilerini kullanarak soruları Türkçe olarak yanıtlamak.

Kurallar:
- Yalnızca verilen bağlam bilgilerine dayan; asla tahmin yürütme.
- Bağlamda cevap yoksa: "Bu konuda elimde bilgi bulunmuyor, lütfen üniversiteyle iletişime geçin." de.
- Cevapların kısa, net ve bilgilendirici olsun.
- Kaynak URL varsa belirt.
- Türkçe yaz, resmi ama samimi bir dil kullan.
"""


def chat(question: str, history: list[dict] | None = None) -> str:
    """Send question + vector-retrieved context to Ollama and return the answer."""
    # Cache check
    cache_key = "ans:" + hashlib.md5(question.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Retrieve relevant knowledge
    entries = retrieve_context(question)
    context_parts = []
    for entry in entries:
        excerpt = smart_excerpt(entry.content, question)
        url_line = f"\nKaynak: {entry.source_url}" if entry.source_url else ""
        context_parts.append(f"### {entry.title}\n{excerpt}{url_line}")

    context_block = "\n\n".join(context_parts) if context_parts else "Bilgi bulunamadı."

    # Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-6:])

    messages.append({"role": "user", "content": (
        f"Aşağıdaki üniversite bilgilerini kullanarak soruyu yanıtla:\n\n"
        f"{context_block}\n\nSoru: {question}"
    )})

    # Call Ollama
    try:
        resp = requests.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model":    settings.OLLAMA_MODEL,
                "messages": messages,
                "stream":   False,
                "options":  {"temperature": 0.3, "num_ctx": 4096},
            },
            timeout=180,
        )
        resp.raise_for_status()
        answer = resp.json()["message"]["content"].strip()
        cache.set(cache_key, answer, ANSWER_CACHE_TTL)
        return answer

    except requests.exceptions.ConnectionError:
        return "AI servisi şu anda erişilemiyor. Lütfen daha sonra tekrar deneyin."
    except requests.exceptions.Timeout:
        return "AI servisi yanıt vermedi (zaman aşımı). Lütfen tekrar deneyin."
    except Exception as e:
        logger.error("Ollama chat error: %s", e)
        return "Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin."
