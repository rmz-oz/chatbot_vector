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
3. pgvector performs cosine similarity search across 2,400+ knowledge entries
4. Top 5 most relevant entries are selected as context (RAG)
5. Context + question sent to local Ollama (`llama3.2:3b`) via HTTP
6. LLM generates a natural-language answer
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
> This takes 5–15 minutes depending on your connection. The web container waits for all models to be ready before starting. Vector embeddings for the knowledge base are generated automatically on first startup.

### 4. Open the chatbot
- **Chat Interface:** http://localhost:8002
- **Admin Panel:** http://localhost:8002/admin
- **API Status:** http://localhost:8002/api/status/

### 5. Create admin user (first time)
```bash
docker compose exec web python manage.py createsuperuser
```

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
    │   ├── views.py            # Chat interface + REST API
    │   ├── llm.py              # Ollama integration + RAG (vector search)
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
        └── knowledge_fixture.json.gz   # Pre-scraped knowledge base
```

---

## REST API

### `POST /api/chat/`
Send a question and receive an AI-generated answer.

**Request:**
```json
{
  "question": "Bilgisayar Mühendisliği programı hakkında bilgi verir misin?"
}
```

**Response:**
```json
{
  "answer": "Acıbadem Üniversitesi Bilgisayar Mühendisliği programı...",
  "response_time_ms": 1842
}
```

### `GET /api/status/`
Check system status.

```json
{
  "status": "ok",
  "model": "llama3.2:3b",
  "knowledge_entries": 2403
}
```

---

## Knowledge Base

Data collected from two public sources:

| Source | Method | Entries |
|--------|--------|---------|
| acibadem.edu.tr | BeautifulSoup + Playwright | ~542 |
| obs.acibadem.edu.tr (Bologna) | Playwright + BeautifulSoup | ~1,783 |
| PDF documents | pdfplumber | ~78 |
| **Total** | | **~2,403** |

---

## AI Integration

- **Chat Model:** `llama3.2:3b` via [Ollama](https://ollama.com) — runs entirely locally, no external API
- **Embedding Model:** `nomic-embed-text` via Ollama — 768-dimensional vectors for semantic search
- **Serving:** Ollama HTTP API (`/api/chat`, `/api/embeddings`)
- **Context window:** 4,096 tokens
- **RAG Strategy:** Vector semantic search with pgvector
  - Question is embedded into a 768-dim vector
  - Cosine similarity search retrieves the 5 most relevant knowledge entries
  - Fallback to keyword scoring if embeddings are unavailable
  - Smart excerpt: finds the most relevant 2,000-char window within long documents
- **Caching:** Redis (answers: 1-hour TTL, embeddings: 24-hour TTL)
- **System Prompt:** Turkish, university-scoped, grounded in retrieved context

---

## Bonus Features Implemented

- ✅ **Vector database (pgvector)** — semantic search with cosine similarity instead of keyword matching
- ✅ **Redis caching** — repeated questions answered instantly (1h), embeddings cached (24h)
- ✅ **Playwright scraping** — handles JavaScript-rendered pages (news, announcements, dynamic content)
- ✅ **PDF scraping** — extracts text from university PDF documents via pdfplumber
- ✅ **Bologna/OBS scraping** — full academic program and course catalog database
- ✅ **Smart excerpt** — finds the most relevant section in long documents before sending to LLM

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
