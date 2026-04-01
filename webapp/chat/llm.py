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
# 2. HYBRID RETRIEVAL (Vector + Keyword)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_tr(text: str) -> str:
    """Normalize Turkish characters to ASCII equivalents for fuzzy matching."""
    tr_map = str.maketrans("şŞğĞüÜöÖçÇıİ", "sSgGuUoOcCiI")
    return text.translate(tr_map)


def _keyword_scores(words: list[str], limit: int) -> dict[int, float]:
    """Return {entry_id: keyword_score} for entries matching specific words."""
    from django.db.models import Q
    # Only use words longer than 4 chars to avoid common Turkish suffixes/words
    specific = [w for w in words if len(w) > 4]
    if not specific:
        specific = words  # fallback to all words if none are long enough
    if not specific:
        return {}
    # Entry must match at least 2 specific words (or all if fewer than 2)
    min_match = min(2, len(specific))
    scores: dict[int, float] = {}
    specific_norm = [_normalize_tr(w) for w in specific]
    for entry in KnowledgeEntry.objects.all().iterator():
        title_kw = _normalize_tr((entry.title + " " + (entry.keywords or "")).lower())
        content  = _normalize_tr(entry.content.lower())
        title_hits   = sum(1 for w in specific_norm if w in title_kw)
        content_hits = sum(1 for w in specific_norm if w in content)
        if title_hits + content_hits >= min_match:
            score = title_hits * 3.0 + content_hits * 1.0
            scores[entry.pk] = score
    # Return top `limit` by score
    top = sorted(scores.items(), key=lambda x: -x[1])[:limit]
    return dict(top)


def retrieve_context(question: str) -> list:
    """
    Hybrid search: combine vector (cosine similarity) + keyword scores,
    rerank and return top RAG_MAX_ENTRIES results.
    """
    from pgvector.django import CosineDistance

    limit   = settings.RAG_MAX_ENTRIES
    pool    = limit * 10         # candidate pool size for vector search
    words   = [_normalize_tr(w) for w in question.lower().split() if len(w) > 2]

    # ── Vector candidates ────────────────────────────────────────────────────
    vector        = get_embedding(question)
    vector_scores: dict[int, float] = {}  # {pk: similarity 0-1}
    entries_by_pk: dict[int, object] = {}

    if vector:
        qs = (
            KnowledgeEntry.objects
            .exclude(embedding=None)
            .annotate(dist=CosineDistance("embedding", vector))
            .order_by("dist")[:pool]
        )
        for e in qs:
            vector_scores[e.pk] = 1.0 - float(e.dist)
            entries_by_pk[e.pk] = e

    # ── Keyword candidates ───────────────────────────────────────────────────
    kw_scores = _keyword_scores(words, pool)
    for pk in kw_scores:
        if pk not in entries_by_pk:
            try:
                entries_by_pk[pk] = KnowledgeEntry.objects.get(pk=pk)
            except KnowledgeEntry.DoesNotExist:
                pass

    if not entries_by_pk:
        return []

    # ── Combine & rerank ─────────────────────────────────────────────────────
    # Normalize keyword scores to [0, 1]
    max_kw = max(kw_scores.values(), default=1.0)
    # Median vector score used as baseline for keyword-only entries
    all_v = list(vector_scores.values())
    v_baseline = sorted(all_v)[len(all_v) // 2] if all_v else 0.5
    combined: list[tuple[float, object]] = []
    for pk, entry in entries_by_pk.items():
        v_score  = vector_scores.get(pk, v_baseline)   # 0-1 (baseline if not in vector pool)
        kw_score = kw_scores.get(pk, 0.0) / max_kw     # 0-1 normalized
        combined.append((0.5 * v_score + 0.5 * kw_score, entry))

    combined.sort(key=lambda t: -t[0])
    return [e for _, e in combined[:limit]]


# ─────────────────────────────────────────────────────────────────────────────
# 3. SMART EXCERPT
# ─────────────────────────────────────────────────────────────────────────────

def smart_excerpt(content: str, question: str, window: int = 800, step: int = 200) -> str:
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
Yalnızca verilen bağlam bilgilerini kullanarak Türkçe yanıt ver.

- Bağlamda cevap yoksa: "Bu konuda elimde bilgi bulunmuyor, lütfen üniversiteyle iletişime geçin."
- Kısa, net ve bilgilendirici cevap ver.
- Bağlamdaki kişi isimlerini, unvanları ve adresleri AYNEN kullan. Hiçbir ismi değiştirme, gizleme veya [Adı] gibi yer tutucu ile değiştirme.
- Yönetmelik veya kural içeren cevaplarda sonuna ekle: "Kesin bilgi için danışmanınıza başvurun."
- Bir programa ait kuralı tüm üniversiteye genelleme.
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
                "options":  {"temperature": 0.3, "num_ctx": 2048, "num_predict": 200},
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


def chat_stream(question: str, history: list[dict] | None = None):
    """Generator: yields text chunks from Ollama stream for SSE."""
    entries = retrieve_context(question)
    context_parts = []
    for entry in entries:
        excerpt = smart_excerpt(entry.content, question)
        url_line = f"\nKaynak: {entry.source_url}" if entry.source_url else ""
        context_parts.append(f"### {entry.title}\n{excerpt}{url_line}")

    context_block = "\n\n".join(context_parts) if context_parts else "Bilgi bulunamadı."

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": (
        f"Aşağıdaki üniversite bilgilerini kullanarak soruyu yanıtla:\n\n"
        f"{context_block}\n\nSoru: {question}"
    )})

    try:
        resp = requests.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json={
                "model":    settings.OLLAMA_MODEL,
                "messages": messages,
                "stream":   True,
                "options":  {"temperature": 0.3, "num_ctx": 2048, "num_predict": 200},
            },
            stream=True,
            timeout=180,
        )
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            import json as _json
            try:
                chunk = _json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break
            except _json.JSONDecodeError:
                continue
    except requests.exceptions.ConnectionError:
        yield "AI servisi şu anda erişilemiyor."
    except requests.exceptions.Timeout:
        yield "AI servisi yanıt vermedi (zaman aşımı)."
    except Exception as e:
        logger.error("Ollama stream error: %s", e)
        yield "Beklenmeyen bir hata oluştu."
