"""
LLM + Vector RAG integration.

Flow:
1. get_embedding(text)        → Ollama /api/embeddings (nomic-embed-text, 768-dim)
2. retrieve_context(question) → category routing + pgvector cosine similarity + keyword search
3. smart_excerpt(content, q)  → finds most relevant 800-char window in large docs
4. chat(question, history)    → builds prompt, calls Ollama /api/chat (llama3.2:3b)

Redis cache:
  - Embeddings: 24h  (same text → same vector, no need to recompute)
  - Answers:     1h
"""

import hashlib
import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

EMBED_CACHE_TTL  = 86400   # 24 hours
ANSWER_CACHE_TTL = 3600    # 1 hour

# Strips non-Latin/non-Turkish characters (CJK, Arabic, etc.) from model output
# Keep common currency symbols: $ (U+0024, already in range), ₺ (U+20BA), € (U+20AC), £ (U+00A3, in range)
_NON_LATIN = re.compile(r"[^\u0000-\u024F\u0300-\u036F\u20AC\u20BA\s]")

# Fixes Turkish vowel harmony errors in copula suffixes (e.g. BULUT'dür → BULUT'dur)
_COPULA_RE = re.compile(r"(\w+)['\u2019]([dt][ıiuü]r)", re.UNICODE)


def _fix_vowel_harmony(text: str) -> str:
    """Fix vowel harmony in Turkish copula suffixes attached to proper nouns."""
    def _correct(m: re.Match) -> str:
        word, suffix = m.group(1), m.group(2)
        last_vowel = next((c for c in reversed(word) if c in "aeıioöuüAEIİOÖUÜ"), None)
        if last_vowel is None:
            return m.group(0)
        lv = last_vowel.lower()
        if lv in "aı":
            v = "ı"
        elif lv in "ei":
            v = "i"
        elif lv in "ou":
            v = "u"
        else:  # öü
            v = "ü"
        return f"{word}'{suffix[0]}{v}r"

    return _COPULA_RE.sub(_correct, text)


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
# 2. HYBRID RETRIEVAL (Vector + Keyword + Category Routing)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_tr(text: str) -> str:
    """Normalize Turkish characters to ASCII equivalents for fuzzy matching."""
    tr_map = str.maketrans("şŞğĞüÜöÖçÇıİ", "sSgGuUoOcCiI")
    return text.translate(tr_map)


# Normalized keyword map for category classification
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "fees":          ["ucret", "fiyat", "burs", "odeme", "harc", "scholarship", "fee", "maliyet", "para"],
    "programs":      ["bolum", "program", "lisans", "yuksek lisans", "doktora", "fakulte", "mufredat", "ogretim"],
    "admission":     ["kayit", "kabul", "basvuru", "yatay gecis", "yks", "puan", "sart", "kosul", "nasil girilir"],
    "campus":        ["kampus", "yurt", "kutuphane", "kafeterya", "spor", "tesis", "ulasim", "bina", "yerlesim"],
    "contact":       ["iletisim", "adres", "telefon", "email", "nerede", "nasil gidilir"],
    "international": ["uluslararasi", "yabanci", "erasmus", "exchange", "international", "uyruk"],
    "student_life":  ["ogrenci", "kulup", "etkinlik", "sosyal", "aktivite", "topluluk"],
    "research":      ["arastirma", "proje", "laboratuvar", "yayin", "bilimsel", "makale"],
    "courses":       ["ders", "kredi", "syllabus", "kurs", "secmeli", "zorunlu"],
}


def _classify_category(question: str) -> str | None:
    """Return the best-matching category for the question, or None if ambiguous."""
    q_norm = _normalize_tr(question.lower())
    best_cat, best_score = None, 0
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in q_norm)
        if score > best_score:
            best_score, best_cat = score, cat
    return best_cat if best_score > 0 else None


def _keyword_scores(words: list[str], limit: int, category: str | None = None) -> dict[int, float]:
    """
    Return {entry_id: keyword_score} using PostgreSQL filtering instead of a
    full table scan. The DB pre-filters matching rows; Python only scores those.
    """
    from django.db.models import Q

    specific = [w for w in words if len(w) > 4] or words
    if not specific:
        return {}

    q = Q()
    for w in specific:
        q |= Q(title__icontains=w) | Q(keywords__icontains=w) | Q(content__icontains=w)

    qs = KnowledgeEntry.objects.filter(q)
    if category:
        qs = qs.filter(category=category)
    qs = qs.only("pk", "title", "keywords", "content")[: limit * 3]

    specific_norm = [_normalize_tr(w) for w in specific]
    scores: dict[int, float] = {}
    for entry in qs:
        title_kw     = _normalize_tr((entry.title + " " + (entry.keywords or "")).lower())
        content      = _normalize_tr(entry.content.lower())
        title_hits   = sum(1 for w in specific_norm if w in title_kw)
        content_hits = sum(1 for w in specific_norm if w in content)
        if title_hits + content_hits > 0:
            scores[entry.pk] = title_hits * 3.0 + content_hits * 1.0

    return dict(sorted(scores.items(), key=lambda x: -x[1])[:limit])


def _do_retrieve(question: str, words: list[str], vector: list[float] | None,
                 category: str | None, limit: int, pool: int) -> tuple[list, float]:
    """Core retrieval logic. Returns (entries, best_score)."""
    from pgvector.django import CosineDistance

    vector_scores: dict[int, float] = {}
    entries_by_pk: dict[int, object] = {}

    if vector:
        qs = KnowledgeEntry.objects.exclude(embedding=None)
        if category:
            qs = qs.filter(category=category)
        qs = qs.annotate(dist=CosineDistance("embedding", vector)).order_by("dist")[:pool]
        for e in qs:
            vector_scores[e.pk] = 1.0 - float(e.dist)
            entries_by_pk[e.pk] = e

    kw_scores = _keyword_scores(words, pool, category=category)
    for pk in kw_scores:
        if pk not in entries_by_pk:
            try:
                entries_by_pk[pk] = KnowledgeEntry.objects.get(pk=pk)
            except KnowledgeEntry.DoesNotExist:
                pass

    if not entries_by_pk:
        return [], 0.0

    max_kw     = max(kw_scores.values(), default=1.0)
    all_v      = list(vector_scores.values())
    v_baseline = sorted(all_v)[len(all_v) // 2] if all_v else 0.5

    combined: list[tuple[float, object]] = []
    for pk, entry in entries_by_pk.items():
        v_score  = vector_scores.get(pk, v_baseline)
        kw_score = kw_scores.get(pk, 0.0) / max_kw
        combined.append((0.5 * v_score + 0.5 * kw_score, entry))

    combined.sort(key=lambda t: -t[0])
    best_score = combined[0][0] if combined else 0.0
    return [e for _, e in combined[:limit]], best_score


# If category-filtered best score is below this, retry without category filter.
_CATEGORY_SCORE_THRESHOLD = 0.45

# If global best score is below this, return nothing to avoid hallucination.
_MIN_RELEVANCE_SCORE = 0.40

# Detects "list all programs" intent — inject the summary entry
_PROGRAMS_LIST_RE = re.compile(
    r"(hangi|tüm|butun|liste|neler|ne var|ne tur).*(program|bolum|fakulte)|"
    r"(program|bolum).*(hangi|liste|neler|var|sunuyor|mevcut)",
    re.IGNORECASE,
)
_PROGRAMS_SUMMARY_URL  = "https://www.acibadem.edu.tr/akademik/programlar-ozet"
_INTL_APPLY_URL        = "https://www.acibadem.edu.tr/uluslararasi-ofis/basvuru-rehberi"
_SCHOLARSHIPS_URL      = "https://www.acibadem.edu.tr/aday/ogrenci/egitim/burs/burs-ozet"
_CONTACT_URL           = "https://www.acibadem.edu.tr/universite/iletisim-ozet"
_TRANSPORT_URL         = "https://www.acibadem.edu.tr/universite/kampus/ulasim-ozet"
_DORM_URL              = "https://www.acibadem.edu.tr/ogrenci/yurt-ozet"
_DOUBLE_MAJOR_URL      = "https://www.acibadem.edu.tr/ogrenci/cift-anadal-ozet"

_INTL_APPLY_RE = re.compile(
    r"(nasil|nasıl).*(basvur|başvur|kayit|kayıt)|"
    r"(basvur|başvur|kayit|kayıt).*(nasil|nasıl|yapabil|edebil)|"
    r"uluslararasi.*(basvur|başvur|kayit|kayıt)|"
    r"(basvur|başvur).*(uluslararasi|yabanci)",
    re.IGNORECASE,
)

_SCHOLARSHIPS_RE = re.compile(
    r"burs|indirim|yks.*(burs|indirim)|"
    r"(burs|indirim).*(var|nasil|nasıl|alabil|oran|yuzde|yüzde)|"
    r"sinav.*(burs|indirim)|sıralama.*(burs|indirim)|"
    r"(burs|indirim).*(sinav|sıralama)|osym.*(burs|indirim)",
    re.IGNORECASE,
)

_CONTACT_RE = re.compile(
    r"iletisim|iletişim|telefon|eposta|e-posta|"
    r"(nasil|nasıl).*(ulasil|ulaşıl|iletisim|iletişim)|"
    r"(numara|mail|saat).*(universite|üniversite)|"
    r"(universite|üniversite).*(numara|mail|saat|adres|iletisim)",
    re.IGNORECASE,
)

_TRANSPORT_RE = re.compile(
    r"ulasim|ulaşım|kampus.*gid|gid.*kampus|nasil.*gid|"
    r"metro|otobüs|otobus|servis|kozyatagi|kozyatağı|yol tarif|"
    r"nereden.*bin|hangi.*metro|hangi.*otobüs",
    re.IGNORECASE,
)

_DORM_RE = re.compile(
    r"yurt|konaklam|barinma|barınma|oda.*kira|kira.*oda|"
    r"ogrenci.*yurd|yurd.*ogrenci|kalacak|nerede.*kal",
    re.IGNORECASE,
)

_DOUBLE_MAJOR_RE = re.compile(
    r"cift anadal|çift anadal|cap\b|yandal|ikinci bolum|ikinci bölüm|"
    r"iki bolum|iki bölüm|cap basvur|çap basvur",
    re.IGNORECASE,
)


def retrieve_context(question: str) -> list:
    """
    Hybrid search: category routing → vector + keyword, with fallback.
    If category-filtered results are low-confidence, retries without filter.
    If global results are still low-confidence, returns empty (no context).
    """
    limit    = settings.RAG_MAX_ENTRIES
    pool     = limit * 10
    words    = [_normalize_tr(w) for w in question.lower().split() if len(w) > 2]
    category = _classify_category(question)
    vector   = get_embedding(question)

    if category:
        entries, best = _do_retrieve(question, words, vector, category, limit, pool)
        if best >= _CATEGORY_SCORE_THRESHOLD:
            logger.debug("Category routing: '%s' → %s (score %.2f)", question[:60], category, best)
            entries = _inject_summary(question, entries, limit)
            return entries
        logger.debug("Category fallback: '%s' score %.2f < %.2f", question[:60], best, _CATEGORY_SCORE_THRESHOLD)

    entries, best = _do_retrieve(question, words, vector, None, limit, pool)

    if best < _MIN_RELEVANCE_SCORE:
        logger.debug("Low relevance (%.2f) for '%s' — returning no context", best, question[:60])
        return []

    return _inject_summary(question, entries, limit)


def _inject_summary(question: str, entries: list, limit: int) -> list:
    """Inject curated summary entries for known broad query patterns.

    The summary entry is always moved to position 0 — even if it was already
    retrieved by the vector search at a lower rank.
    """
    q_norm = _normalize_tr(question.lower())
    injections = []

    def _maybe_inject(url: str, label: str) -> None:
        try:
            s = KnowledgeEntry.objects.get(source_url=url)
            injections.append(s)
            logger.debug("Injected %s entry", label)
        except KnowledgeEntry.DoesNotExist:
            pass

    if _PROGRAMS_LIST_RE.search(q_norm):
        _maybe_inject(_PROGRAMS_SUMMARY_URL, "programs summary")

    if _INTL_APPLY_RE.search(q_norm):
        _maybe_inject(_INTL_APPLY_URL, "international apply")

    if _SCHOLARSHIPS_RE.search(q_norm):
        _maybe_inject(_SCHOLARSHIPS_URL, "scholarships summary")

    if _CONTACT_RE.search(q_norm):
        _maybe_inject(_CONTACT_URL, "contact summary")

    if _TRANSPORT_RE.search(q_norm):
        _maybe_inject(_TRANSPORT_URL, "transport summary")

    if _DORM_RE.search(q_norm):
        _maybe_inject(_DORM_URL, "dorm summary")

    if _DOUBLE_MAJOR_RE.search(q_norm):
        _maybe_inject(_DOUBLE_MAJOR_URL, "double major summary")

    if injections:
        injected_pks = {s.pk for s in injections}
        rest = [e for e in entries if e.pk not in injected_pks]
        entries = injections + rest[: limit - len(injections)]
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# 3. SMART EXCERPT
# ─────────────────────────────────────────────────────────────────────────────

def smart_excerpt(content: str, question: str, window: int = 800, step: int = 200) -> str:
    """
    For long documents: slide a window and return the 800-char slice
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

- Yanıtlarını YALNIZCA Türkçe yaz. İngilizce kelime, ifade veya açıklama ekleme. "meaning", "so", "therefore", "thus" gibi İngilizce bağlaç veya kelimeler kullanma.
- Bağlamda cevap yoksa SADECE şunu yaz: "Bu konuda elimde bilgi bulunmuyor, lütfen üniversiteyle iletişime geçin." Başka hiçbir şey ekleme.
- Bilgi bulunamadığında bağlamda geçen program veya bölüm adlarını ASLA yanıta ekleme. Kullanıcı sormadıysa program adı belirtme.
- Kısa, net ve bilgilendirici cevap ver. Ancak bağlamda bir liste veya tablo varsa listedeki TÜM maddeleri eksiksiz yaz, kısaltma yapma.
- Bağlamdaki URL'leri olduğu gibi aktar, kısaltma veya değiştirme yapma. URL'leri tam hâliyle yaz (örneğin https://www.acibadem.edu.tr/...).
- Kesinlikle var olmayan veya uydurulmuş Türkçe kelimeler kullanma.
- Bağlamdaki kişi isimlerini, unvanları ve adresleri AYNEN kullan. Hiçbir ismi değiştirme, gizleme veya [Adı] gibi yer tutucu ile değiştirme.
- Yönetmelik veya kural içeren cevaplarda sonuna ekle: "Kesin bilgi için danışmanınıza başvurun."
- Bir programa ait kuralı tüm üniversiteye genelleme.
- Bir kural yaz okulu, belirli bir program veya özel bir koşula özgüyse bunu AÇIKÇA belirt.
- Bağlamda "X-Y" şeklinde bir aralık varsa ve kullanıcı maksimumu soruyorsa Y değerini kullan. Sayısal aralıkları toplarken en yüksek değeri kullan.
"""


_BYPASS_URLS = {
    _PROGRAMS_SUMMARY_URL, _INTL_APPLY_URL, _SCHOLARSHIPS_URL,
    _CONTACT_URL, _TRANSPORT_URL, _DORM_URL, _DOUBLE_MAJOR_URL,
}


def _direct_response(entries: list) -> str | None:
    """Return curated summary content directly (bypass LLM) for known summary entries."""
    if entries and entries[0].source_url in _BYPASS_URLS:
        return entries[0].content
    return None


def _retrieval_query(question: str, history: list[dict] | None) -> str:
    """Build an enriched query for RAG retrieval using recent conversation context.

    Follow-up questions like "peki ücreti ne kadar?" are ambiguous without
    the previous turn. Prepend the last user message so the embedding captures
    the full intent.
    """
    if not history:
        return question
    last_user = next(
        (m["content"] for m in reversed(history) if m.get("role") == "user"),
        None,
    )
    if not last_user or last_user == question:
        return question
    return f"{last_user[:200]} {question}"


def chat(question: str, history: list[dict] | None = None) -> str:
    """Send question + vector-retrieved context to Ollama and return the answer."""
    cache_key = "ans:" + hashlib.md5(question.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return cached

    entries = retrieve_context(_retrieval_query(question, history))

    direct = _direct_response(entries)
    if direct:
        cache.set(cache_key, direct, ANSWER_CACHE_TTL)
        return direct
    context_parts = []
    for entry in entries:
        excerpt = entry.content if entry.source_url in _BYPASS_URLS else smart_excerpt(entry.content, question)
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
                "stream":   False,
                "options":  {"temperature": 0.3, "num_ctx": 4096, "num_predict": 500},
            },
            timeout=180,
        )
        resp.raise_for_status()
        raw    = resp.json()["message"]["content"]
        answer = _fix_vowel_harmony(_NON_LATIN.sub("", raw)).strip()
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
    entries = retrieve_context(_retrieval_query(question, history))

    direct = _direct_response(entries)
    if direct:
        yield direct
        return

    context_parts = []
    for entry in entries:
        excerpt = entry.content if entry.source_url in _BYPASS_URLS else smart_excerpt(entry.content, question)
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
                "options":  {"temperature": 0.3, "num_ctx": 4096, "num_predict": 500},
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
                token = _NON_LATIN.sub("", chunk.get("message", {}).get("content", ""))
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
