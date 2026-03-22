"""
OBS Bologna Bilgi Sistemi Scraper (obs.acibadem.edu.tr)

Keşfedilen URL yapısı:
  - Kurum sayfaları : dynConPage.aspx?curPageId=100-401&lang=tr
  - Program listesi : unitSelection.aspx?type=lis|myo|yls|dok&lang=tr
  - Program detayı  : prog*.aspx?lang=tr&curSunit=XXXX
  - Ders planı      : progCourses.aspx?lang=tr&curSunit=XXXX
  - Ders detayı     : courseDetail.aspx?... (listeden toplanır)

Tüm iç sayfalar doğrudan erişilebilir (iframe kabuk gereksiz).

Kullanım:
    python manage.py scrape_obs_bologna
    python manage.py scrape_obs_bologna --delay 0.3 --timeout 25
"""

import re
import time
import threading
import logging
from urllib.parse import urlencode, urlparse, parse_qs, urljoin

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

OBS = "https://obs.acibadem.edu.tr/oibs/bologna"
LANG = "tr"

# ── Degree types for unitSelection.aspx ───────────────────────────────────────
DEGREE_TYPES = [
    ("Ön Lisans",     "myo"),
    ("Lisans",        "lis"),
    ("Yüksek Lisans", "yls"),
    ("Doktora",       "dok"),
]

# ── Institutional pages (dynConPage.aspx) ─────────────────────────────────────
INSTITUTIONAL = [
    (100, "Yönetim",                     "general"),
    (101, "Üniversite Hakkında",          "general"),
    (102, "Bologna Komisyonu",            "general"),
    (103, "İletişim",                     "contact"),
    (104, "AKTS Kataloğu",               "general"),
    (300, "Şehir Hakkında",              "campus"),
    (301, "Kampüs",                       "campus"),
    (302, "Yemek",                        "student_life"),
    (303, "Sağlık Hizmetleri",           "student_life"),
    (304, "Spor ve Sosyal Yaşam",        "student_life"),
    (305, "Öğrenci Kulüpleri",           "student_life"),
    (309, "Konaklama",                    "student_life"),
    (311, "Engelli Öğrenci Hizmetleri",  "student_life"),
    (400, "Bologna Süreci",              "general"),
    (401, "Erasmus+ Beyannamesi",        "international"),
]

# ── Per-program inner pages (only curSunit needed) ────────────────────────────
PROGRAM_PAGES = [
    ("progAbout.aspx",               "Program Hakkında"),
    ("progGoalsObjectives.aspx",     "Amaçlar ve Hedefler"),
    ("progProfile.aspx",             "Program Profili"),
    ("progOfficials.aspx",           "Program Yetkilileri"),
    ("progDegree.aspx",              "Alınacak Derece"),
    ("progAdmissionReq.aspx",        "Kabul Koşulları"),
    ("progAccessFurhterStudies.aspx","Üst Kademeye Geçiş"),
    ("progGraduationReq.aspx",       "Mezuniyet Koşulları"),
    ("progRecogPriorLearning.aspx",  "Önceki Öğrenmenin Tanınması"),
    ("progQualifyReqReg.aspx",       "Yeterlilik Koşulları"),
    ("progOccupationalProf.aspx",    "İstihdam Olanakları"),
    ("progLearnOutcomes.aspx",       "Program Yeterlikleri"),
    ("progCourses.aspx",             "Ders Planı"),
    ("progCourseMatrix.aspx",        "Ders-Program Yeterlilikleri"),
    ("progTYYCMatrix.aspx",          "TYYÇ-Program İlişkisi"),
    ("progAcademicStaff.aspx",       "Akademik Personel"),
    ("progContact.aspx",             "İletişim"),
]

NOISE_TAGS = ["script", "style", "noscript", "svg", "head",
              "button", "meta", "link", "select", "option", "iframe"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _url(page_name: str, params: dict | None = None) -> str:
    base = f"{OBS}/{page_name}?lang={LANG}"
    if params:
        base += "&" + urlencode(params)
    return base


def _clean(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _title(html: str, fallback: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for sel in ["h1", "h2", ".page-title", ".program-title", "title"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 3:
                return t[:250]
    return fallback[:250]


def _keywords(title: str, content: str) -> str:
    stop = {"bir", "ile", "için", "olan", "veya", "gibi", "daha", "her",
            "and", "the", "for", "with", "that", "this", "are", "from",
            "olan", "olan", "veya", "kadar", "sonra", "önce"}
    parts: set[str] = set()
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{3,}", title.lower()):
        parts.add(w)
    freq: dict[str, int] = {}
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{4,}", content.lower()):
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    parts.update(sorted(freq, key=lambda w: -freq[w])[:20])
    return " ".join(sorted(parts))


def _fetch(page, url: str, timeout_ms: int, wait_ms: int = 2000) -> str:
    """Navigate to URL and return rendered HTML."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(wait_ms)
        return page.content()
    except Exception as exc:
        logger.debug(f"fetch error {url}: {exc}")
        return ""


def _save(url: str, html: str, fallback_title: str, category: str,
          min_len: int, out_results: list, out_log: list,
          saved_urls: set) -> bool:
    if url in saved_urls:
        return False
    content = _clean(html)
    if len(content) < min_len:
        out_log.append(f"    SKIP (short {len(content)}ch)")
        return False
    t = _title(html, fallback_title)
    out_results.append({
        "url":      url,
        "title":    t,
        "category": category,
        "content":  content[:6000],
        "keywords": _keywords(t, content),
    })
    out_log.append(f"    OK [{len(content)}ch] {t[:65]}")
    return True


# ── Main Playwright task ───────────────────────────────────────────────────────

def _playwright_task(saved_urls: set, delay: float, timeout_ms: int,
                     min_len: int, out_results: list, out_log: list):
    from playwright.sync_api import sync_playwright
    from bs4 import BeautifulSoup

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

        # ── STEP 1: Discover all programs ─────────────────────────────────
        out_log.append("=" * 65)
        out_log.append("STEP 1 — Discovering all programs from unit selection pages")
        out_log.append("=" * 65)

        program_list: list[dict] = []
        seen_ids: set[str] = set()

        for degree_label, dtype in DEGREE_TYPES:
            sel_url = _url("unitSelection.aspx", {"type": dtype})
            out_log.append(f"\n[{degree_label}] {sel_url}")

            html = _fetch(page, sel_url, timeout_ms, wait_ms=2000)
            if not html:
                out_log.append("  → No response, skipping")
                continue

            soup = BeautifulSoup(html, "html.parser")
            found = 0

            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)

                # Program links pattern: index.aspx?lang=tr&curOp=showPac&curUnit=XX&curSunit=YY
                m_unit  = re.search(r"curUnit=(\d+)", href)
                m_sunit = re.search(r"curSunit=(\d+)", href)

                if m_unit and m_sunit:
                    uid = f"{m_unit.group(1)}_{m_sunit.group(1)}"
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        program_list.append({
                            "degree":   degree_label,
                            "name":     text[:200] or f"Program {m_sunit.group(1)}",
                            "curUnit":  m_unit.group(1),
                            "curSunit": m_sunit.group(1),
                        })
                        found += 1

            # Save unit selection listing page
            content = _clean(html)
            if len(content) >= min_len:
                listing_url = sel_url
                if listing_url not in saved_urls:
                    t = _title(html, f"OBS {degree_label} Programları")
                    out_results.append({
                        "url":      listing_url,
                        "title":    t,
                        "category": "programs",
                        "content":  content[:6000],
                        "keywords": _keywords(t, content),
                    })
                    out_log.append(f"  Listing page saved ({len(content)}ch)")

            out_log.append(f"  → {found} programs found")
            time.sleep(delay)

        out_log.append(f"\nTotal programs discovered: {len(program_list)}")
        for prog in program_list:
            out_log.append(
                f"  [{prog['degree']:12s}] {prog['name'][:50]:50s}"
                f" unit={prog['curUnit']:3s} sunit={prog['curSunit']}"
            )

        # ── STEP 2: Institutional pages ────────────────────────────────────
        out_log.append("\n" + "=" * 65)
        out_log.append("STEP 2 — Institutional pages (dynConPage.aspx)")
        out_log.append("=" * 65)

        for page_id, label, cat in INSTITUTIONAL:
            url = _url("dynConPage.aspx", {"curPageId": page_id})
            out_log.append(f"\n→ [{page_id}] {label}")
            html = _fetch(page, url, timeout_ms)
            _save(url, html, f"OBS – {label}", cat, min_len,
                  out_results, out_log, saved_urls)
            time.sleep(delay)

        # ── STEP 3: Per-program sub-pages ──────────────────────────────────
        out_log.append("\n" + "=" * 65)
        out_log.append(f"STEP 3 — {len(program_list)} programs × "
                       f"{len(PROGRAM_PAGES)} sub-pages each")
        out_log.append("=" * 65)

        course_detail_links: list[tuple[str, str]] = []  # (url, prog_name)

        for i, prog in enumerate(program_list, 1):
            name      = prog["name"]
            cur_sunit = prog["curSunit"]
            degree    = prog["degree"]

            out_log.append(
                f"\n[{i}/{len(program_list)}] {degree} → {name} (sunit={cur_sunit})"
            )

            for page_file, page_label in PROGRAM_PAGES:
                url = _url(page_file, {"curSunit": cur_sunit})
                out_log.append(f"  [{page_label}]")
                html = _fetch(page, url, timeout_ms)
                saved = _save(url, html, f"{name} — {page_label}", "programs",
                              min_len, out_results, out_log, saved_urls)
                if saved:
                    time.sleep(delay)

                    # Collect course detail links from course list page
                    if page_file == "progCourses.aspx":
                        soup = BeautifulSoup(html, "html.parser")
                        for a in soup.find_all("a", href=True):
                            href = a["href"]
                            if any(kw in href for kw in
                                   ["courseDetail", "ShowCourse", "curCourse",
                                    "courseCode", "dersKodu"]):
                                if href.startswith("http"):
                                    full = href
                                elif href.startswith("/"):
                                    full = "https://obs.acibadem.edu.tr" + href
                                else:
                                    full = f"{OBS}/{href.lstrip('/')}"
                                if "lang=" not in full:
                                    sep = "&" if "?" in full else "?"
                                    full += f"{sep}lang={LANG}"
                                course_detail_links.append((full, name))

        # ── STEP 4: Faculty info pages (facAbout.aspx) ─────────────────────
        out_log.append("\n" + "=" * 65)
        out_log.append("STEP 4 — Faculty info pages")
        out_log.append("=" * 65)

        # Collect unique curUnit values
        fac_units: set[str] = set()
        for prog in program_list:
            fac_units.add(prog["curUnit"])

        for unit in sorted(fac_units):
            url = _url("facAbout.aspx", {"curUnit": unit, "curOp": "facAbout"})
            out_log.append(f"\n→ Faculty unit={unit}")
            html = _fetch(page, url, timeout_ms)
            _save(url, html, f"Fakülte Hakkında (unit={unit})", "programs",
                  min_len, out_results, out_log, saved_urls)
            time.sleep(delay)

        # ── STEP 5: Course detail pages ─────────────────────────────────────
        out_log.append("\n" + "=" * 65)
        out_log.append("STEP 5 — Individual course detail pages")
        out_log.append("=" * 65)

        seen_courses: set[str] = set()
        unique_courses: list[tuple[str, str]] = []
        for (url, prog_name) in course_detail_links:
            if url not in seen_courses and url not in saved_urls:
                seen_courses.add(url)
                unique_courses.append((url, prog_name))

        out_log.append(f"Unique course detail pages: {len(unique_courses)}")

        for i, (url, prog_name) in enumerate(unique_courses, 1):
            out_log.append(f"\n[{i}/{len(unique_courses)}] {prog_name}: {url}")
            html = _fetch(page, url, timeout_ms)
            _save(url, html, f"{prog_name} – Ders Detayı", "programs",
                  min_len, out_results, out_log, saved_urls)
            time.sleep(delay)

        ctx.close()
        browser.close()

    out_log.append("\n✓ Playwright task complete.")


# ── Django management command ──────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Scrape obs.acibadem.edu.tr Bologna Information System"

    def add_arguments(self, parser):
        parser.add_argument("--delay", type=float, default=0.4,
                            help="Delay between requests (default: 0.4s)")
        parser.add_argument("--timeout", type=int, default=25,
                            help="Page timeout in seconds (default: 25)")
        parser.add_argument("--min-length", type=int, default=100,
                            help="Min content chars to save (default: 100)")

    def handle(self, *args, **options):
        delay      = options["delay"]
        timeout_ms = options["timeout"] * 1000
        min_len    = options["min_length"]

        saved_urls = set(KnowledgeEntry.objects.values_list("source_url", flat=True))
        self.stdout.write(
            f"OBS Bologna Scraper started\n"
            f"  DB entries : {len(saved_urls)}\n"
            f"  Timeout    : {options['timeout']}s | "
            f"Delay: {delay}s | Min-len: {min_len}\n"
        )

        out_results: list[dict] = []
        out_log:     list[str]  = []

        t = threading.Thread(
            target=_playwright_task,
            args=(saved_urls, delay, timeout_ms, min_len, out_results, out_log),
            daemon=True,
        )
        t.start()
        t.join(timeout=7200)

        for line in out_log:
            self.stdout.write(line)

        self.stdout.write(f"\n{'=' * 65}")
        self.stdout.write(f"Saving {len(out_results)} scraped pages to DB...")

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
                        f"  NEW  [{item['category']:12s}] {item['title'][:60]}"
                    ))
                else:
                    updated += 1
                    self.stdout.write(
                        f"  UPD  [{item['category']:12s}] {item['title'][:60]}"
                    )
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.WARNING(f"  DB ERR: {exc}"))

        total = KnowledgeEntry.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"\n{'=' * 65}\n"
            f"DONE  Created={created} | Updated={updated} | "
            f"Errors={errors} | Total KB={total}\n"
            f"{'=' * 65}"
        ))
