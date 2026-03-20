"""
Claude API integration with Redis caching and keyword-weighted RAG.
"""

import hashlib
import logging

import anthropic
from django.conf import settings
from django.core.cache import cache

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


# ── RAG ───────────────────────────────────────────────────────────────────────

def retrieve_context(question: str) -> list[KnowledgeEntry]:
    """Keyword-weighted retrieval from KnowledgeEntry table."""
    stopwords = {
        "what", "is", "are", "the", "a", "an", "in", "at", "of", "for",
        "to", "and", "or", "how", "can", "i", "do", "does", "tell", "me",
        "about", "please", "have", "has", "this", "that", "with", "from",
        "bu", "ve", "bir", "de", "da", "ne", "nasıl", "nerede", "hangi",
        "için", "olan", "gibi", "var", "mı", "mi",
    }
    words = {w for w in question.lower().split() if len(w) > 2 and w not in stopwords}

    if not words:
        return list(KnowledgeEntry.objects.filter(category="general")[:3])

    scored: list[tuple[int, KnowledgeEntry]] = []
    for entry in KnowledgeEntry.objects.all():
        combined = (entry.title + " " + entry.keywords + " " + entry.content).lower()
        score = sum(
            3 if w in entry.keywords.lower() else 1
            for w in words
            if w in combined
        )
        if score:
            scored.append((score, entry))

    scored.sort(key=lambda t: -t[0])
    return [e for _, e in scored[: settings.RAG_MAX_ENTRIES]]


# ── Main chat function ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the official virtual assistant for Acıbadem University (Acıbadem Üniversitesi), Istanbul, Turkey.

Rules:
1. Answer ONLY questions related to Acıbadem University — programs, admission, fees, campus, student life, research, and contact information.
2. Base your answers on the provided context. If the context does not contain enough information, say so honestly and suggest contacting the university directly.
3. Be concise, friendly, and professional.
4. If asked in Turkish, reply in Turkish. If asked in English, reply in English.
5. Never fabricate specific numbers (fees, scores, dates) — always note that figures should be verified at acibadem.edu.tr.
6. Format lists with bullet points for clarity.
7. End responses with a helpful follow-up suggestion when appropriate."""


def chat(question: str, history: list[dict]) -> str:
    """Send a question to Claude with RAG context and conversation history."""

    # Check cache first
    cache_key = "chat:" + hashlib.md5(question.encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Retrieve relevant knowledge
    entries = retrieve_context(question)
    if entries:
        context_blocks = "\n\n".join(
            f"[{e.get_category_display()}] {e.title}:\n{e.content[:800]}"
            for e in entries
        )
        context_text = f"RELEVANT KNOWLEDGE BASE:\n{context_blocks}\n\n"
    else:
        context_text = ""

    # Build messages
    messages: list[dict] = []

    # Add conversation history (last 6 turns)
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Add current question with context
    user_content = f"{context_text}User question: {question}"
    messages.append({"role": "user", "content": user_content})

    try:
        client = _get_client()
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=settings.CLAUDE_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        answer = response.content[0].text.strip()
        cache.set(cache_key, answer, timeout=3600)
        return answer

    except anthropic.AuthenticationError:
        logger.error("Anthropic API key is invalid or missing.")
        return "Configuration error: please check the API key."
    except anthropic.RateLimitError:
        logger.warning("Anthropic rate limit hit.")
        return "I'm receiving too many requests right now. Please try again in a moment."
    except Exception as exc:
        logger.exception("Unexpected error from Claude API: %s", exc)
        return "An unexpected error occurred. Please try again."
