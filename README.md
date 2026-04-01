# ACU AI Chatbot

**CSE 322 – Cloud Computing | Spring 2026**

An AI-powered chatbot web application that answers questions about Acıbadem University using data collected from the university's websites. Built with Django, PostgreSQL, Redis, and a local LLM (Llama 3.2) served via Ollama — fully containerized with Docker Compose.

---

## Team Members

| Name | Student ID | Role |
|------|-----------|------|
| (Member 1) | | Django / Backend |
| (Member 2) | | Web Scraping / Data |
| (Member 3) | | Docker / DevOps |
| (Member 4) | | AI Integration / Prompting |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       docker-compose.yml                         │
│                                                                  │
│  ┌──────────┐    ┌────────────────────┐    ┌──────────────────┐  │
│  │ web      │    │ ollama             │    │ db (PostgreSQL)  │  │
│  │ Django   │───▶│ llama3.2:3b (chat) │    │ + pgvector       │  │
│  │ :8002    │    │ nomic-embed-text   │    │ :5432            │  │
│  └────┬─────┘    │ (embeddings)       │    └──────────────────┘  │
│       │          │ :11434             │                           │
│       │          └────────────────────┘    ┌──────────────────┐  │
│       │                                    │ redis            │  │
│       └────────────────────────────────────│ cache :6379      │  │
│                                            └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**Data Flow:**
1. User types a question in the chat interface
2. Django generates a vector embedding of the question via Ollama (`nomic-embed-text`)
3. **Hybrid search:** pgvector cosine similarity + keyword scoring, results combined and reranked
4. Top 5 most relevant entries are selected as context (RAG)
5. Context + question sent to local Ollama (`llama3.2:3b`) via streaming HTTP
6. LLM streams a natural-language answer token by token (SSE)
7. Answer cached in Redis (1-hour TTL) and returned to user

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- ~6 GB free disk space (Llama 3.2 3B model ~2 GB + nomic-embed-text ~700 MB + PostgreSQL data)

### 1. Clone the repository
```bash
git clone <repository-url>
cd chatbot_vector
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env if needed (defaults work out of the box)
```

### 3. Start all services
```bash
docker compose up --build
```

> **First run:** The `ollama-init` container automatically downloads both AI models:
> - `llama3.2:3b` (~2 GB) — the chat model
> - `nomic-embed-text` (~700 MB) — the embedding model for vector search
>
> This takes 5–15 minutes depending on your connection. The knowledge base fixture (`knowledge_fixture.json.gz`) ships with pre-generated embeddings, so **no re-embedding is needed on subsequent startups**.

### 4. Open the chatbot
- **Chat Interface:** http://localhost:8002
- **Admin Panel:** http://localhost:8002/admin
- **API Status:** http://localhost:8002/api/status/

### 5. Create admin user (first time)
```bash
docker compose exec web python manage.py createsuperuser
```

---

## Performance & GPU Acceleration

Response speed depends heavily on your hardware. The default Docker setup uses **CPU only**, which is slow. For a good experience, use native Ollama with GPU acceleration.

| Hardware | Setup | Speed | Response time |
|---|---|---|---|
| Apple Silicon (M1/M2/M3) | Native Ollama (Metal) | ~8–15 tok/s | **~10–20sn** |
| NVIDIA GPU | Docker + CUDA | ~30–60 tok/s | **~3–8sn** |
| Intel Mac / CPU only | Docker (default) | ~0.5–2 tok/s | **2–5 min** |

### Option A — Apple Silicon (Recommended for Mac)

Install and run Ollama natively so it can access the Metal GPU:

```bash
# 1. Download Ollama from https://ollama.com and install Ollama.app

# 2. Pull the required models
ollama pull llama3.2:3b
ollama pull nomic-embed-text

# 3. Start Ollama natively (run this before docker compose up)
ollama serve
```

Then update your `.env`:
```
OLLAMA_URL=http://host.docker.internal:11434
```

And remove the Docker Ollama dependency by editing `docker-compose.yml` — remove the `ollama` and `ollama-init` services, and remove `ollama-init` from the `web` service's `depends_on`.

### Option B — NVIDIA GPU (Linux / Windows WSL2)

Add the following to the `ollama` service in `docker-compose.yml`:

```yaml
  ollama:
    image: ollama/ollama:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Requires [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) to be installed.

### Option C — CPU only (Default, works everywhere)

No changes needed. Just run:
```bash
docker compose up --build
```
This works on any machine but responses may take 2–5 minutes. The streaming interface ensures you see text as it's generated rather than waiting for the full response.

---

## Project Structure

```
chatbot_vector/
├── docker-compose.yml          # All services: web, ollama, db, redis, ollama-init
├── .env.example                # Environment variable template
├── README.md
├── docs/
│   └── report.pdf              # Technical report
└── webapp/
    ├── Dockerfile
    ├── requirements.txt
    ├── manage.py
    ├── wait_for_db.py
    ├── config/                 # Django settings, urls, wsgi
    │   ├── settings.py
    │   ├── urls.py
    │   └── wsgi.py
    ├── chat/                   # Chat app
    │   ├── models.py           # KnowledgeEntry, ChatSession, ChatMessage
    │   ├── views.py            # Chat interface + REST API + SSE streaming
    │   ├── llm.py              # Ollama integration + Hybrid RAG (vector + keyword)
    │   ├── admin.py            # Django Admin configuration
    │   └── templates/chat/index.html
    ├── scraper/                # Data collection
    │   └── management/commands/
    │       ├── scrape_website.py       # BeautifulSoup crawler (~542 pages)
    │       ├── scrape_dynamic.py       # Playwright scraper (JS-rendered pages)
    │       ├── scrape_pdfs.py          # PDF text extraction (~78 documents)
    │       ├── scrape_obs_bologna.py   # Bologna/OBS academic data (~1,200 pages)
    │       ├── load_knowledge.py       # Seed data loader
    │       └── generate_embeddings.py  # pgvector embedding generation
    └── fixtures/
        └── knowledge_fixture.json.gz   # Pre-scraped knowledge base (with embeddings)
```

---

## REST API

### `POST /api/chat/`
Send a question and receive a complete AI-generated answer.

**Request:**
```json
{ "question": "Bilgisayar Mühendisliği programı hakkında bilgi verir misin?" }
```

**Response:**
```json
{
  "answer": "Acıbadem Üniversitesi Bilgisayar Mühendisliği programı...",
  "response_time_ms": 14200
}
```

### `POST /api/stream/`
Same as `/api/chat/` but streams the answer token by token via Server-Sent Events (SSE). The chat interface uses this endpoint by default.

**Response (SSE stream):**
```
data: {"token": "Acı"}
data: {"token": "badem"}
data: {"token": " Üniversitesi"}
...
data: {"done": true, "response_time_ms": 14200}
```

### `GET /api/status/`
Check system status.

```json
{
  "status": "ok",
  "model": "llama3.2:3b",
  "knowledge_entries": 2410
}
```

---

## Knowledge Base

Data collected from public sources (scraped March 2026):

| Source | Method | Entries |
|--------|--------|---------|
| acibadem.edu.tr | BeautifulSoup + Playwright | ~542 |
| obs.acibadem.edu.tr (Bologna) | Playwright + BeautifulSoup | ~1,783 |
| PDF documents | pdfplumber | ~78 |
| mevzuat.gov.tr | PDF scraper | ~7 |
| **Total** | | **~2,410** |

All entries are stored with 768-dimensional `nomic-embed-text` embeddings in PostgreSQL (pgvector). The fixture ships with embeddings pre-computed — no re-generation needed on fresh installs.

---

## AI Integration

- **Chat Model:** `llama3.2:3b` via [Ollama](https://ollama.com) — runs entirely locally, no external API
- **Embedding Model:** `nomic-embed-text` via Ollama — 768-dimensional vectors for semantic search
- **Serving:** Ollama HTTP API with streaming (`/api/chat`, `/api/embeddings`)
- **Context window:** 2,048 tokens
- **RAG Strategy:** Hybrid search (vector + keyword)
  - Question is embedded into a 768-dim vector
  - pgvector cosine similarity retrieves top 50 candidates
  - Keyword scoring with Turkish character normalization (ş→s, ö→o, etc.)
  - Results combined: `0.5 × vector_score + 0.5 × keyword_score`, top 5 selected
  - Smart excerpt: finds the most relevant 800-char window within long documents
  - Regulation-based answers automatically include a disclaimer to consult an advisor
- **Streaming:** Responses stream token by token via SSE (Server-Sent Events)
- **Caching:** Redis (answers: 1-hour TTL, embeddings: 24-hour TTL)
- **System Prompt:** Turkish, university-scoped, grounded in retrieved context

---

## Bonus Features Implemented

- ✅ **Streaming responses (SSE)** — answers appear token by token, no waiting for full response
- ✅ **Hybrid RAG (vector + keyword)** — combines pgvector cosine similarity with keyword scoring
- ✅ **Turkish character normalization** — "bolum baskani" correctly matches "bölüm başkanı"
- ✅ **pgvector (PostgreSQL)** — 768-dim embeddings stored natively, no separate vector DB needed
- ✅ **Redis caching** — repeated questions answered instantly (1h TTL), embeddings cached (24h TTL)
- ✅ **Playwright scraping** — handles JavaScript-rendered pages (news, announcements, dynamic content)
- ✅ **PDF scraping** — extracts text from university PDF documents and regulation files via pdfplumber
- ✅ **Bologna/OBS scraping** — full academic program and course catalog database
- ✅ **Smart excerpt** — sliding window finds the most relevant section in long documents
- ✅ **Pre-built embeddings fixture** — `knowledge_fixture.json.gz` ships with embeddings, no recomputation on fresh install
- ✅ **Background embedding generation** — server starts immediately, embeddings generated in background

---

## Local Development (without Docker)

```bash
cd webapp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start PostgreSQL and Redis locally, then:
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver

# Scrape data (optional — use fixture instead)
python manage.py scrape_website --max 500
python manage.py scrape_dynamic --max-links 300
python manage.py scrape_pdfs
```

Run Ollama locally and update your `.env`:
```bash
ollama pull llama3.2:3b
ollama pull nomic-embed-text
ollama serve
# Set OLLAMA_URL=http://localhost:11434 in .env
```
