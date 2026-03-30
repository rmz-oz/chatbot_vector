"""
Acibadem University website scraper.

Usage:
    python manage.py scrape_website              # full crawl
    python manage.py scrape_website --max 200    # limit pages
    python manage.py scrape_website --lang en    # only English pages
    python manage.py scrape_website --lang tr    # only Turkish pages
"""

import re
import time
import hashlib
import logging
from collections import deque
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

# ── Seed URLs ─────────────────────────────────────────────────────────────────
# Verified 2026-03 from live site — all return HTTP 200 with real content
SEED_URLS = [
    # Ana sayfa & üniversite
    "https://www.acibadem.edu.tr/",
    "https://www.acibadem.edu.tr/universite",
    # Lisans programları
    "https://www.acibadem.edu.tr/akademik/lisans",
    "https://www.acibadem.edu.tr/akademik/lisans/tip-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/eczacilik-fakultesi/eczacilik-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/yonetim",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/bilgisayar-muhendisligi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/biyomedikal-muhendisligi",
    "https://www.acibadem.edu.tr/akademik/lisans/muhendislik-ve-doga-bilimleri-fakultesi/bolumler/molekuler-biyoloji-ve-genetik",
    "https://www.acibadem.edu.tr/akademik/lisans/tip-fakultesi/yonetim",
    "https://www.acibadem.edu.tr/akademik/lisans/tip-fakultesi/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/yonetim",
    "https://www.acibadem.edu.tr/akademik/lisans/saglik-bilimleri-fakultesi/akademik-kadro",
    "https://www.acibadem.edu.tr/akademik/lisans/insan-ve-toplum-bilimleri-fakultesi/insan-ve-toplum-bilimleri-fakultesi",
    # Lisansüstü
    "https://www.acibadem.edu.tr/akademik/lisansustu",
    "https://www.acibadem.edu.tr/akademik/lisansustu/saglik-bilimleri-enstitusu",
    "https://www.acibadem.edu.tr/akademik/lisansustu/fen-bilimleri-enstitusu",
    "https://www.acibadem.edu.tr/akademik/lisansustu/sosyal-bilimler-enstitusu",
    "https://www.acibadem.edu.tr/akademik/lisansustu/senoloji-arastirma-enstitusu",
    # Önlisans
    "https://www.acibadem.edu.tr/akademik/onlisans",
    "https://www.acibadem.edu.tr/akademik/onlisans/saglik-hizmetleri-meslek-yuksekokulu",
    "https://www.acibadem.edu.tr/akademik/onlisans/meslek-yuksekokulu",
    "https://www.acibadem.edu.tr/akademik/ortak-dersler-bolumleri",
    "https://www.acibadem.edu.tr/programlar",
    # Aday / başvuru / ücretler
    "https://www.acibadem.edu.tr/aday/ogrenci",
    "https://www.acibadem.edu.tr/aday/ogrenci/egitim/burs/burs-olanaklari",
    "https://www.acibadem.edu.tr/aday/ogrenci/egitim/kontenjan-ve-puan-tablosu",
    "https://www.acibadem.edu.tr/ogrenci/odeme-yontemleri",
    # Araştırma
    "https://www.acibadem.edu.tr/arastirma",
    "https://www.acibadem.edu.tr/arastirma/arastirmaci",
    "https://www.acibadem.edu.tr/arastirma/endustri",
    "https://www.acibadem.edu.tr/arastirma/ogrenci",
    "https://www.acibadem.edu.tr/merkezler/case",
    "https://www.acibadem.edu.tr/arastirma-isbirlikleri",
    # Öğrenci hayatı
    "https://www.acibadem.edu.tr/ogrenci/acuda-yasam",
    "https://www.acibadem.edu.tr/ogrenci/acuda-yasam/ogrenci-kulupleri",
    "https://www.acibadem.edu.tr/ogrenci/acuda-yasam/spor-merkezi",
    "https://www.acibadem.edu.tr/ogrenci/ogrenci-isleri",
    "https://www.acibadem.edu.tr/ogrenci/ogrenci-isleri/akademik-takvim",
    "https://www.acibadem.edu.tr/kariyer-merkezi",
    "https://www.acibadem.edu.tr/ogrenci/oryantasyon-programi",
    # Uluslararası
    "https://www.acibadem.edu.tr/uluslararasi",
    "https://www.acibadem.edu.tr/uluslararasi-ofis",
    "https://www.acibadem.edu.tr/global-degisim-programlari",
    "https://www.acibadem.edu.tr/arastirmaci-degisim-programi",
    "https://www.acibadem.edu.tr/en/international-office/international-students",
    # İletişim & kampüs
    "https://www.acibadem.edu.tr/iletisim",
    "https://www.acibadem.edu.tr/kayit/iletisim/ulasim",
    "https://www.acibadem.edu.tr/surdurulebilir-kampus",
    # Haberler / duyurular
    "https://www.acibadem.edu.tr/haberler",
    "https://www.acibadem.edu.tr/duyurular",
    "https://www.acibadem.edu.tr/etkinlikler",
]

ALLOWED_DOMAINS = {"www.acibadem.edu.tr", "acibadem.edu.tr"}

# ── URL → Category mapping (Turkish URL structure, verified) ──────────────────
CATEGORY_PATTERNS = [
    (r"/akademik/lisans|/akademik/onlisans|/programlar"
     r"|/tip-fakultesi|/eczacilik|/saglik-bilimleri-fakultesi"
     r"|/muhendislik|/insan-ve-toplum",                          "programs"),
    (r"/akademik/lisansustu|/enstitusu|/doktora|/yuksek-lisans", "programs"),
    (r"/akademik/ortak-dersler",                                  "courses"),
    (r"/aday|/basvuru|/kontenjan|/kabul",                        "admission"),
    (r"/burs|/odeme-yontemleri|/ucret",                          "fees"),
    (r"/ulasim|/surdurulebilir-kampus",                          "campus"),
    (r"/arastirma|/merkezler|/arastirma-isbirlikleri"
     r"|/test-analiz",                                            "research"),
    (r"/ogrenci|/kariyer|/kulup|/spor|/oryantasyon|/mezuniyet",  "student_life"),
    (r"/uluslararasi|/global-degisim|/erasmus|/degisim"
     r"|/international|/arastirmaci-degisim",                    "international"),
    (r"/iletisim",                                               "contact"),
    (r"/universite|/haberler|/duyurular|/etkinlikler"
     r"|/surdurulebilirlik",                                      "general"),
]

# ── URLs to skip ──────────────────────────────────────────────────────────────
SKIP_PATTERNS = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|jpg|jpeg|png|gif|svg|ico|mp4|mp3|woff2?|ttf|css|js)$"
    r"|/(wp-admin|login|logout|admin|cart|checkout|feed|rss|sitemap|robots)",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AcibademChatbotScraper/1.0; "
        "+https://www.acibadem.edu.tr)"
    ),
    "Accept-Language": "tr,en;q=0.9",
}

# ── HTML noise tags to strip ──────────────────────────────────────────────────
NOISE_TAGS = [
    "script", "style", "noscript", "header", "footer", "nav",
    "aside", "form", "iframe", "button", "figure", "svg",
    "[document]", "head",
]


def _normalise_url(url: str) -> str:
    """Remove fragment and trailing slash inconsistencies."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def _url_category(url: str) -> str:
    for pattern, cat in CATEGORY_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return cat
    return "general"


def _clean_text(soup: BeautifulSoup) -> str:
    """Remove noise tags and return clean visible text."""
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_keywords(title: str, text: str, url: str) -> str:
    """Build a keyword string from title words + URL slugs."""
    parts = set()
    # title words
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{3,}", title):
        parts.add(w.lower())
    # URL slug words
    slug = urlparse(url).path.replace("-", " ").replace("/", " ")
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{3,}", slug):
        parts.add(w.lower())
    # Most frequent meaningful words from text (top 15)
    freq: dict[str, int] = {}
    stopwords = {
        "the", "and", "for", "with", "that", "this", "are", "from",
        "bir", "ile", "için", "olan", "veya", "gibi", "daha", "her",
        "you", "our", "your", "will", "have", "been", "they", "can",
    }
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{4,}", text.lower()):
        if w not in stopwords:
            freq[w] = freq.get(w, 0) + 1
    top = sorted(freq, key=lambda w: -freq[w])[:15]
    parts.update(top)
    return " ".join(sorted(parts))


def _page_title(soup: BeautifulSoup, url: str) -> str:
    # Try <h1> first, then <title>
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)[:250]
    title_tag = soup.find("title")
    if title_tag:
        raw = title_tag.get_text(strip=True)
        # Strip site name suffix (e.g., " | Acıbadem University")
        raw = re.sub(r"\s*[|\-–]\s*Acıbadem.*$", "", raw, flags=re.IGNORECASE)
        return raw.strip()[:250]
    # Fallback: last URL segment
    seg = urlparse(url).path.rstrip("/").split("/")[-1]
    return seg.replace("-", " ").title()[:250] or url


class Command(BaseCommand):
    help = "Crawl Acıbadem University website and save pages to KnowledgeEntry"

    def add_arguments(self, parser):
        parser.add_argument(
            "--max", type=int, default=500,
            help="Maximum number of pages to scrape (default: 500)",
        )
        parser.add_argument(
            "--lang", choices=["tr", "en", "both"], default="both",
            help="Language filter: tr, en, or both (default: both)",
        )
        parser.add_argument(
            "--delay", type=float, default=0.5,
            help="Delay in seconds between requests (default: 0.5)",
        )
        parser.add_argument(
            "--min-length", type=int, default=150,
            help="Minimum content length to save (default: 150 chars)",
        )

    def handle(self, *args, **options):
        max_pages   = options["max"]
        lang        = options["lang"]
        delay       = options["delay"]
        min_length  = options["min_length"]

        session = requests.Session()
        session.headers.update(HEADERS)

        visited:  set[str] = set()
        queue:    deque[str] = deque()
        created = updated = skipped = errors = 0

        # Seed queue — site is mostly Turkish; /en/ prefix only for international page
        for url in SEED_URLS:
            norm = _normalise_url(url)
            queue.append(norm)
            visited.add(norm)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Starting crawl — max={max_pages}, delay={delay}s, seeds={len(queue)}"
        ))

        page_count = 0

        while queue and page_count < max_pages:
            url = queue.popleft()
            page_count += 1

            self.stdout.write(f"  [{page_count}/{max_pages}] {url}")

            # ── Fetch ─────────────────────────────────────────────────────────
            try:
                resp = session.get(url, timeout=15, allow_redirects=True)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"    SKIP (fetch error): {exc}"))
                errors += 1
                continue

            if resp.status_code != 200:
                self.stdout.write(self.style.WARNING(
                    f"    SKIP (HTTP {resp.status_code})"
                ))
                errors += 1
                time.sleep(delay)
                continue

            ct = resp.headers.get("content-type", "")
            if "text/html" not in ct:
                skipped += 1
                continue

            # ── Parse ─────────────────────────────────────────────────────────
            soup = BeautifulSoup(resp.text, "html.parser")

            # Collect links before stripping tags
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                abs_url = _normalise_url(urljoin(url, href))
                parsed  = urlparse(abs_url)
                if parsed.netloc not in ALLOWED_DOMAINS:
                    continue
                if SKIP_PATTERNS.search(abs_url):
                    continue
                if abs_url not in visited and len(queue) < max_pages * 2:
                    visited.add(abs_url)
                    queue.append(abs_url)

            # ── Extract content ───────────────────────────────────────────────
            title   = _page_title(soup, url)
            content = _clean_text(soup)

            if len(content) < min_length:
                self.stdout.write(f"    SKIP (content too short: {len(content)} chars)")
                skipped += 1
                time.sleep(delay)
                continue

            # Truncate very long pages — keep first 4000 chars (enough for RAG)
            if len(content) > 4000:
                content = content[:4000] + "…"

            category = _url_category(url)
            keywords = _extract_keywords(title, content, url)

            # ── Save ──────────────────────────────────────────────────────────
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            unique_title = f"{title} [{url_hash}]"

            obj, is_new = KnowledgeEntry.objects.update_or_create(
                source_url=url,
                defaults={
                    "title":      unique_title,
                    "category":   category,
                    "content":    content,
                    "keywords":   keywords,
                    "scraped_at": timezone.now(),
                },
            )
            if is_new:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"    CREATED [{category}] {title[:60]}"))
            else:
                updated += 1
                self.stdout.write(f"    UPDATED [{category}] {title[:60]}")

            time.sleep(delay)

        total = KnowledgeEntry.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Pages crawled: {page_count} | "
            f"Created: {created} | Updated: {updated} | "
            f"Skipped: {skipped} | Errors: {errors} | "
            f"Total KB entries: {total}"
        ))
