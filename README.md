# ACU AI Chatbot

**CSE 322 – Cloud Computing | Spring 2026**

A sovereign, on-premises AI chatbot that answers natural-language questions about Acıbadem Mehmet Ali Aydınlar University. Zero external API calls — all inference runs locally via Ollama. Built with Django, PostgreSQL/pgvector, Redis, and Docker Compose.

**Group:** Cold Start &nbsp;|&nbsp; **Repo:** [github.com/rmz-oz/chatbot_vector](https://github.com/rmz-oz/chatbot_vector)

---

## Team

| Member | Role | Contributions |
|--------|------|---------------|
| **Ramiz** | Backend | Django skeleton, DB models (`KnowledgeEntry`, `ChatSession`, `ChatMessage`), REST API endpoints, fixture-based data loading pipeline, `wait_for_db.py` service-readiness probe |
| **Onur** | Web Scraping & Data | Five scraping pipelines (BeautifulSoup, Playwright, pdfplumber): 542 website pages, 78 PDFs, 1,783 Bologna/OBS catalogue pages, mevzuat.gov.tr regulatory data → 2,410+ total entries |
| **Demir** | Docker & DevOps | `docker-compose.yml` authoring, pgvector/pg15 selection, `ollama-init` optimization, background embedding generation for fast Gunicorn startup |
| **Deha** | AI Integration & RAG | Ollama HTTP integration, Hybrid RAG engine (vector + keyword), system prompts, regulation-aware logic, `smart_excerpt` sliding-window algorithm |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        docker-compose.yml                         │
│                                                                   │
│  ┌──────────┐    ┌─────────────────────┐    ┌─────────────────┐  │
│  │ web      │    │ ollama              │    │ db (PostgreSQL) │  │
│  │ Django   │───▶│ llama3.2:3b (chat)  │    │ + pgvector      │  │
│  │ :8002    │    │ nomic-embed-text    │    │ :5432           │  │
│  └────┬─────┘    │ (embeddings)        │    └─────────────────┘  │
│       │          │ :11434              │                          │
│       │          └─────────────────────┘    ┌─────────────────┐  │
│       │                                     │ redis           │  │
│       └─────────────────────────────────────│ cache :6379     │  │
│                                             └─────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**RAG Data Flow:**
1. User question (+ last user turn for follow-up context) → 768-dim vector embedding via `nomic-embed-text`
2. **Category routing** — question classified into a topic (fees, programs, admission, etc.); vector search scoped to that category
3. **Hybrid search** — pgvector cosine similarity + PostgreSQL keyword scoring, `0.5 × vector + 0.5 × keyword`
4. **Relevance threshold** — results below minimum confidence score filtered out to prevent hallucination
5. **Injection / Bypass** — curated summary entries prepended or returned directly for known query patterns (programs list, scholarships, contact, transport, dorms, double-major, international apply)
6. Top 5 entries selected; `smart_excerpt` finds the most relevant 800-char window per document
7. Context + question sent to `llama3.2:3b` via Ollama HTTP API (fully local)
8. Answer streamed token-by-token via SSE; cached in Redis (1-hour TTL)

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- ~6 GB free disk space (llama3.2:3b ~2 GB + nomic-embed-text ~700 MB + PostgreSQL data)

### 1. Clone
```bash
git clone https://github.com/rmz-oz/chatbot_vector.git
cd chatbot_vector
```

### 2. Configure
```bash
cp .env.example .env
# Defaults work out of the box
```

### 3. Start
```bash
docker compose up --build
```

> **First run:** `ollama-init` automatically downloads both models (~2.7 GB total). Takes 5–15 minutes depending on connection. The knowledge base fixture ships with pre-generated embeddings — **no re-embedding needed** on subsequent startups.

### 4. Open
| URL | Description |
|-----|-------------|
| http://localhost:8002 | Chat interface |
| http://localhost:8002/admin | Django admin |
| http://localhost:8002/api/status/ | System status |

### 5. Create admin user (first time)
```bash
docker compose exec web python manage.py createsuperuser
```

---

## Performance & GPU Acceleration

| Hardware | Setup | Speed | Response time |
|---|---|---|---|
| Apple Silicon (M1/M2/M3) | Native Ollama (Metal GPU) | ~8–15 tok/s | **~10–20s** |
| NVIDIA GPU | Docker + CUDA | ~30–60 tok/s | **~3–8s** |
| CPU only | Docker (default) | ~0.5–2 tok/s | **2–5 min** |

### Apple Silicon — Native Ollama (Recommended)

```bash
# Run Ollama natively to access Metal GPU
OLLAMA_HOST=0.0.0.0:11436 /Applications/Ollama.app/Contents/Resources/ollama serve &
```

Update `.env`:
```
OLLAMA_URL=http://host.docker.internal:11436
```

Then remove the `ollama` and `ollama-init` services from `docker-compose.yml` (and remove `ollama-init` from `web.depends_on`).

### NVIDIA GPU

Add to the `ollama` service in `docker-compose.yml`:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```
Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

### CPU Only (Default)
No changes needed. Works everywhere; responses may take 2–5 minutes.

---

## Project Structure

```
chatbot_vector/
├── docker-compose.yml              # All services: web, ollama, db, redis, ollama-init
├── .env.example                    # Environment variable template
├── README.md
├── docs/
│   └── report.pdf                  # Technical report
└── webapp/
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py
    ├── wait_for_db.py
    ├── config/                     # Django settings, urls, wsgi
    ├── chat/                       # Core chat application
    │   ├── models.py               # KnowledgeEntry, ChatSession, ChatMessage
    │   ├── views.py                # Chat interface + REST API + SSE streaming
    │   ├── llm.py                  # Ollama integration + Structured Hybrid RAG
    │   ├── admin.py
    │   └── templates/chat/index.html
    ├── scraper/                    # Data collection pipelines
    │   └── management/commands/
    │       ├── scrape_website.py           # BeautifulSoup crawler (~542 pages)
    │       ├── scrape_dynamic.py           # Playwright scraper (JS-rendered pages)
    │       ├── scrape_pdfs.py              # PDF text extraction (~78 documents)
    │       ├── scrape_obs_bologna.py       # Bologna/OBS academic data (~1,783 pages)
    │       ├── load_knowledge.py           # Seed data loader
    │       └── generate_embeddings.py      # pgvector embedding generation
    └── fixtures/
        └── knowledge_fixture.json.gz       # Pre-scraped knowledge base (with embeddings)
```

---

## REST API

### `POST /api/chat/`
```json
// Request
{ "question": "Bilgisayar Mühendisliği bölüm başkanı kim?" }

// Response
{
  "answer": "Bilgisayar Mühendisliği Bölüm Başkanı Prof. Dr. Ahmet BULUT'tur.",
  "response_time_ms": 10061
}
```

### `POST /api/stream/`
Same as `/api/chat/` but streams via Server-Sent Events (SSE):
```
data: {"token": "Bilgisayar"}
data: {"token": " Mühendisliği"}
...
data: {"done": true, "response_time_ms": 10061}
```

### `POST /api/feedback/<message_id>/`
```json
{ "feedback": "up" }   // or "down"
```

### `GET /api/sessions/`
Returns list of active chat sessions for the current user.

### `POST /api/sessions/new/`
Creates a new chat session.

### `POST /api/sessions/switch/`
```json
{ "session_id": "abc123" }
```

### `GET /api/status/`
```json
{ "status": "ok", "model": "llama3.2:3b", "knowledge_entries": 2418 }
```

---

## Knowledge Base

| Source | Method | Entries |
|--------|--------|---------|
| acibadem.edu.tr | BeautifulSoup + Playwright | ~542 |
| obs.acibadem.edu.tr (Bologna) | Playwright + BeautifulSoup | ~1,783 |
| PDF documents | pdfplumber | ~78 |
| mevzuat.gov.tr | PDF scraper | ~7 |
| Curated summaries | Manual | 10 |
| **Total** | | **~2,418** |

All entries stored with 768-dimensional `nomic-embed-text` embeddings in PostgreSQL (pgvector). The fixture ships with pre-computed embeddings — no re-generation needed on fresh installs.

**Curated summary entries** are hand-written for high-traffic topics where scraping quality is insufficient: programs list, scholarships, international application, contact info, campus transport, student dorms, double major/minor. These are returned directly (LLM bypass) for matching queries.

---

## AI & RAG Implementation

### Models
- **Chat:** `llama3.2:3b` via Ollama — fully local, no external API
- **Embedding:** `nomic-embed-text` — 768-dim vectors
- **Context window:** 4,096 tokens | **Max output:** 500 tokens

### Structured Hybrid RAG (`llm.py`)

**Category Routing**
Questions are classified into one of 9 categories (fees, programs, admission, campus, contact, international, student_life, research, courses) using keyword matching. Vector search is then scoped to that category for higher precision.

**Fallback Logic**
- If category-filtered results score below `0.45`, retries across all categories
- If global results score below `0.40`, returns empty context — model responds with "no information" instead of hallucinating

**Hybrid Scoring**
```
score = 0.5 × vector_score + 0.5 × keyword_score
```
Vector candidates: pgvector cosine similarity (top 50 pool)
Keyword candidates: PostgreSQL `ILIKE` filter (replaces full Python table scan)

**Multi-turn Context**
The last user message is prepended to the current question before embedding, so follow-up queries like "peki ücreti ne kadar?" retrieve the right entry without repeating context.

**Curated Injection / LLM Bypass**
For known broad queries (programs list, scholarships, international application, contact, transport, dorms, double-major), curated summary entries are injected at the top of results. If the top result is a bypass URL, the LLM is skipped entirely and the curated content is returned directly — eliminating hallucination for these high-traffic topics.

**Post-processing**
- Non-Latin character filter (strips CJK/Arabic hallucinations, preserves ₺ and €)
- Turkish vowel harmony correction (e.g. `BULUT'dür` → `BULUT'dur`)

**Smart Excerpt**
Sliding 800-char window finds the densest keyword match within long documents. Bypass entries skip this and pass full content to the LLM.

**Caching**
- Embeddings: Redis 24h TTL
- Answers: Redis 1h TTL

---

## Implemented Features

- ✅ **Streaming SSE** — token-by-token responses, no waiting
- ✅ **Structured Hybrid RAG** — category routing + vector + keyword
- ✅ **Multi-turn context** — last user message enriches retrieval for follow-up questions
- ✅ **Curated injection & LLM bypass** — 7 high-traffic topics returned without LLM involvement
- ✅ **Navigation junk cleanup** — 506 scraped entries cleaned of nav blocks, embeddings re-generated
- ✅ **PostgreSQL keyword search** — DB-side filtering (no full table scan)
- ✅ **Relevance threshold** — prevents hallucination from low-confidence results
- ✅ **Turkish character normalization** — "bolum baskani" matches "bölüm başkanı"
- ✅ **Post-processing** — non-Latin character filter (preserves ₺/€) + vowel harmony correction
- ✅ **pgvector** — 768-dim embeddings in PostgreSQL, no separate vector DB
- ✅ **Redis caching** — instant repeat answers, cached embeddings
- ✅ **Multi-session UI** — sidebar with session history, dark/light mode, mobile responsive
- ✅ **Upvote/downvote feedback** — stored per message; admin stats page + downvote logging
- ✅ **Playwright scraping** — JS-rendered pages (news, announcements)
- ✅ **PDF scraping** — pdfplumber extracts university documents and regulations
- ✅ **Bologna/OBS scraping** — full academic program and course catalog
- ✅ **Pre-built embeddings fixture** — sub-30-second startup on re-runs
- ✅ **Regulation-aware prompting** — rules/regulations include advisor disclaimer

---

## Future Improvements

**Short-term**
Admin panel knowledge entry editor (inline embedding refresh on save); rate limiting on `/api/chat/`; question suggestion buttons on chat open; cache invalidation on entry edit

**Medium-term**
CI/CD via GitHub Actions; Kubernetes migration; benchmark vs `mistral:7b` / `llama3.1:8b` / `gemma2:9b`; auto language detection; source URL surfacing in responses

**Long-term**
GPU acceleration (MPS/CUDA) targeting 2–3s response times; OBS integration for personalized student data; Elasticsearch + BM25 for Turkish morphological analysis

---

## Local Development (without Docker)

```bash
cd webapp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Run Ollama locally:
```bash
ollama pull llama3.2:3b
ollama pull nomic-embed-text
ollama serve
# Set OLLAMA_URL=http://localhost:11434 in .env
```
