"""
Management command: generate_embeddings
---------------------------------------
Calls Ollama nomic-embed-text for every KnowledgeEntry that has no embedding
yet, and saves the 768-dim vector to the DB.

Usage:
    python manage.py generate_embeddings            # all missing
    python manage.py generate_embeddings --batch 50 # 50 per run (resume-safe)
    python manage.py generate_embeddings --reset     # re-embed everything
"""

import time
import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)


def _embed(text: str) -> list[float] | None:
    try:
        resp = requests.post(
            f"{settings.OLLAMA_URL}/api/embeddings",
            json={"model": settings.EMBEDDING_MODEL, "prompt": text[:4000]},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception as e:
        logger.error("Embedding failed: %s", e)
        return None


class Command(BaseCommand):
    help = "Generate pgvector embeddings for all KnowledgeEntry rows"

    def add_arguments(self, parser):
        parser.add_argument("--batch",  type=int, default=0,     help="Stop after N entries (0 = all)")
        parser.add_argument("--reset",  action="store_true",      help="Re-embed even existing vectors")
        parser.add_argument("--delay",  type=float, default=0.05, help="Seconds between requests")

    def handle(self, *args, **options):
        reset  = options["reset"]
        batch  = options["batch"]
        delay  = options["delay"]

        qs = KnowledgeEntry.objects.all()
        if not reset:
            qs = qs.filter(embedding=None)

        total = qs.count()
        if batch:
            qs = qs[:batch]

        self.stdout.write(f"Embedding {total} entries (batch={batch or 'all'}, model={settings.EMBEDDING_MODEL})")

        done = errors = 0
        for entry in qs.iterator():
            # Embed title + keywords + first 3000 chars of content
            text = f"{entry.title}. {entry.keywords}. {entry.content[:3000]}"
            vector = _embed(text)

            if vector:
                entry.embedding = vector
                entry.save(update_fields=["embedding"])
                done += 1
            else:
                errors += 1

            if (done + errors) % 50 == 0:
                self.stdout.write(f"  {done + errors}/{total} — OK={done} ERR={errors}")

            if delay:
                time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\nDONE — Embedded={done} | Errors={errors} | Total entries in DB: {KnowledgeEntry.objects.count()}"
        ))
