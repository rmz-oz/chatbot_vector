"""
Dynamic page scraper using Playwright (headless Chromium).

Targets JS-rendered pages that BeautifulSoup cannot read:
  - /haberler        (news)
  - /haberler/arsiv  (news archive)
  - /duyurular       (announcements)
  - /duyurular/arsiv (announcements archive)
  - /programlar      (program list)
  - /global-degisim-programlari
  - /ogrenci/acuda-yasam
  - /etkinlikler

Usage:
    python manage.py scrape_dynamic
    python manage.py scrape_dynamic --max-links 300
"""

import re
import time
import logging
import threading
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

BASE = "https://www.acibadem.edu.tr"

HUB_PAGES = [
    ("/haberler",                   "general"),
    ("/haberler/arsiv",             "general"),
    ("/duyurular",                  "general"),
    ("/duyurular/arsiv",            "general"),
    ("/programlar",                 "programs"),
    ("/global-degisim-programlari", "international"),
    ("/ogrenci/acuda-yasam",        "student_life"),
    ("/etkinlikler",                "general"),
]

NOISE_TAGS = ["script", "style", "noscript", "header", "footer",
              "nav", "aside", "form", "iframe", "button", "svg", "head"]

SKIP_RE = re.compile(
    r"\.(pdf|docx?|xlsx?|zip|jpg|jpeg|png|gif|svg|ico|mp4|mp3|css|js)$"
    r"|/(login|logout|admin|feed|rss|sitemap|robots)",
    re.IGNORECASE,
)

URL_CATEGORY = [
    (r"/akademik|/programlar|/tip-|/eczacilik|/saglik-bil|/muhendislik"
     r"|/insan-ve-toplum|/lisansustu|/onlisans",       "programs"),
    (r"/aday|/basvuru|/kontenjan|/kabul",              "admission"),
    (r"/burs|/odeme|/ucret",                           "fees"),
    (r"/ulasim|/kampus|/surdurulebilir",               "campus"),
    (r"/arastirma|/merkezler",                         "research"),
    (r"/ogrenci|/kariyer|/kulup|/spor",                "student_life"),
    (r"/uluslararasi|/global-degisim|/erasmus"
     r"|/international|/degisim",                      "international"),
    (r"/iletisim",                                     "contact"),
    (r"/haberler|/duyurular|/etkinlikler|/universite", "general"),
]


def _category(url: str) -> str:
    for pattern, cat in URL_CATEGORY:
        if re.search(pattern, url, re.IGNORECASE):
            return cat
    return "general"


def _clean(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()


def _title(html: str, url: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True) and "gezinti" not in h1.get_text(strip=True).lower():
        return h1.get_text(strip=True)[:250]
    t = soup.find("title")
    if t:
        raw = re.sub(r"\s*[|\-–]\s*Acıbadem.*$", "", t.get_text(strip=True), flags=re.IGNORECASE)
        return raw.strip()[:250]
    return urlparse(url).path.rstrip("/").split("/")[-1].replace("-", " ").title()[:250]


def _keywords(title: str, content: str, url: str) -> str:
    parts = set()
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{3,}", title.lower()):
        parts.add(w)
    slug = urlparse(url).path.replace("-", " ").replace("/", " ")
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{3,}", slug.lower()):
        parts.add(w)
    stopwords = {"the", "and", "for", "with", "that", "this", "are", "from",
                 "bir", "ile", "için", "olan", "veya", "gibi", "daha", "her"}
    freq: dict[str, int] = {}
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{4,}", content.lower()):
        if w not in stopwords:
            freq[w] = freq.get(w, 0) + 1
    parts.update(sorted(freq, key=lambda w: -freq[w])[:15])
    return " ".join(sorted(parts))


def _playwright_task(saved_urls: set, max_links: int, timeout_ms: int,
                     delay: float, min_length: int,
                     out_results: list, out_log: list):
    """Runs entirely in its own thread — no Django ORM calls here."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            ),
            locale="tr-TR",
        )
        page = ctx.new_page()
        page.set_default_timeout(timeout_ms)

        collected_links: list[tuple[str, str]] = []

        # ── Collect links from hub pages ──────────────────────────────────────
        for hub_path, hub_cat in HUB_PAGES:
            hub_url = BASE + hub_path
            out_log.append(f"Hub: {hub_url}")
            try:
                page.goto(hub_url, wait_until="networkidle")
                page.wait_for_timeout(2000)
                # Scroll to trigger lazy loading
                for _ in range(6):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(400)

                # Collect internal links
                hrefs = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href'))"
                )
                new_count = 0
                for href in hrefs:
                    if not href or not href.startswith("/"):
                        continue
                    href = href.rstrip("/")
                    full = BASE + href
                    if full in saved_urls or SKIP_RE.search(href):
                        continue
                    collected_links.append((full, _category(full)))
                    new_count += 1

                # Save hub page itself
                html    = page.content()
                content = _clean(html)
                if len(content) >= min_length:
                    title = _title(html, hub_url)
                    out_results.append({
                        "url":      hub_url,
                        "title":    title,
                        "category": hub_cat,
                        "content":  content[:4000],
                        "keywords": _keywords(title, content, hub_url),
                    })
                    out_log.append(f"  → hub saved ({len(content)} chars, {new_count} new links)")
                else:
                    out_log.append(f"  → hub too short ({len(content)} chars), {new_count} new links")

            except Exception as exc:
                out_log.append(f"  Hub error: {exc}")

        # Deduplicate
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for url, cat in collected_links:
            if url not in seen and url not in saved_urls:
                seen.add(url)
                unique.append((url, cat))

        out_log.append(f"\nScraping {min(len(unique), max_links)} detail pages...")

        # ── Scrape detail pages ───────────────────────────────────────────────
        for i, (url, cat) in enumerate(unique[:max_links], 1):
            out_log.append(f"  [{i}/{min(len(unique), max_links)}] {url}")
            try:
                page.goto(url, wait_until="networkidle")
                page.wait_for_timeout(1500)

                html    = page.content()
                content = _clean(html)

                if len(content) < min_length:
                    out_log.append(f"    SKIP (short: {len(content)} chars)")
                    continue

                title = _title(html, url)
                out_results.append({
                    "url":      url,
                    "title":    title,
                    "category": cat,
                    "content":  content[:4000],
                    "keywords": _keywords(title, content, url),
                })
                out_log.append(f"    OK [{cat}] {title[:55]} ({len(content)} chars)")
                time.sleep(delay)

            except Exception as exc:
                out_log.append(f"    ERROR: {exc}")

        ctx.close()
        browser.close()


class Command(BaseCommand):
    help = "Scrape JS-rendered pages using Playwright headless Chromium"

    def add_arguments(self, parser):
        parser.add_argument("--max-links", type=int, default=300,
                            help="Max detail pages to scrape (default: 300)")
        parser.add_argument("--timeout", type=int, default=20,
                            help="Page load timeout in seconds (default: 20)")
        parser.add_argument("--delay", type=float, default=0.5,
                            help="Delay between requests in seconds (default: 0.5)")
        parser.add_argument("--min-length", type=int, default=200,
                            help="Minimum content length to save (default: 200)")

    def handle(self, *args, **options):
        max_links  = options["max_links"]
        timeout_ms = options["timeout"] * 1000
        delay      = options["delay"]
        min_length = options["min_length"]

        saved_urls = set(KnowledgeEntry.objects.values_list("source_url", flat=True))

        out_results: list[dict] = []
        out_log:     list[str]  = []

        # Playwright must run in its own OS thread (no Django async context)
        t = threading.Thread(
            target=_playwright_task,
            args=(saved_urls, max_links, timeout_ms, delay, min_length,
                  out_results, out_log),
            daemon=True,
        )
        t.start()
        t.join()

        # Print collected logs
        for line in out_log:
            self.stdout.write(line)

        # Save to DB (main thread — safe for Django ORM)
        created = updated = errors = 0
        for item in out_results:
            try:
                _, is_new = KnowledgeEntry.objects.update_or_create(
                    source_url=item["url"],
                    defaults={
                        "title":      item["title"],
                        "category":   item["category"],
                        "content":    item["content"],
                        "keywords":   item["keywords"],
                        "scraped_at": timezone.now(),
                    },
                )
                if is_new:
                    created += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  SAVED (new) [{item['category']}] {item['title'][:60]}"
                    ))
                else:
                    updated += 1
                    self.stdout.write(
                        f"  SAVED (upd) [{item['category']}] {item['title'][:60]}"
                    )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  DB error: {exc}"))
                errors += 1

        total = KnowledgeEntry.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Created: {created} | Updated: {updated} | "
            f"Errors: {errors} | Total KB: {total}"
        ))
