"""
OBS Bologna Bilgi Sistemi Scraper (obs.acibadem.edu.tr)

ASP.NET iframe tabanlı portal:
- Ana kabuk: index.aspx  (navbar + IFRAME1)
- İçerik:    unitSelection.aspx, ShowPac.aspx, ShowCourseList.aspx, vb.

Strateji:
1. Her derece türü için unitSelection.aspx'e git → program ID'lerini topla
2. Her program için ShowPac.aspx, ShowCourseList.aspx vb. yükle
3. Course list içindeki ders linklerini de ziyaret et
4. Tüm içerikleri DB'ye kaydet

Kullanım:
    python manage.py scrape_obs_bologna
    python manage.py scrape_obs_bologna --delay 0.3
"""

import re
import time
import threading
import logging
from urllib.parse import urlencode

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import KnowledgeEntry

logger = logging.getLogger(__name__)

OBS_BASE = "https://obs.acibadem.edu.tr/oibs/bologna"
LANG     = "tr"

# Degree type → unitSelection.aspx type param
DEGREE_TYPES = [
    ("Ön Lisans",     "myo"),
    ("Lisans",        "lis"),
    ("Yüksek Lisans", "yls"),
    ("Doktora",       "dok"),
]

# Per-program inner pages (relative to OBS_BASE)
PROGRAM_PAGES = [
    ("ShowPac.aspx",             "program_hakkinda",   "Program Bilgileri"),
    ("ShowCourseList.aspx",      "ders_plani",         "Ders Planı"),
    ("ShowAcStaff.aspx",         "akademik_personel",  "Akademik Personel"),
    ("ShowAdmission.aspx",       "kabul",              "Kabul Koşulları"),
    ("ShowGraduation.aspx",      "mezuniyet",          "Mezuniyet Koşulları"),
    ("ShowQualification.aspx",   "yeterlilik",         "Yeterlilik Koşulları"),
    ("ShowEmployment.aspx",      "istihdam",           "İstihdam Olanakları"),
    ("ShowLearningOutcomes.aspx","ogrenme_ciktilari",  "Öğrenme Çıktıları"),
    ("ShowContact.aspx",         "iletisim",           "İletişim"),
]

# Institutional inner pages (no program params)
INSTITUTIONAL_PAGES = [
    ("ShowManagement.aspx",    "Yönetim"),
    ("ShowAbout.aspx",         "Üniversite Hakkında"),
    ("ShowBolognaCom.aspx",    "Bologna Komisyonu"),
    ("ShowContactGeneral.aspx","İletişim"),
    ("ShowEctsInfo.aspx",      "AKTS Kataloğu"),
    ("ShowCity.aspx",          "Şehir Hakkında"),
    ("ShowCampus.aspx",        "Kampüs"),
    ("ShowDining.aspx",        "Yemek"),
    ("ShowHealth.aspx",        "Sağlık Hizmetleri"),
    ("ShowSports.aspx",        "Spor ve Sosyal Yaşam"),
    ("ShowClubs.aspx",         "Öğrenci Kulüpleri"),
    ("ShowAccomodation.aspx",  "Konaklama"),
    ("ShowDisabled.aspx",      "Engelli Öğrenci Hizmetleri"),
    ("ShowErasmus.aspx",       "Erasmus Beyannamesi"),
    ("ShowBolognaProcess.aspx","Bologna Süreci"),
]

NOISE_TAGS = ["script", "style", "noscript", "svg", "head",
              "button", "meta", "link", "select", "option"]


# ── Utility helpers ────────────────────────────────────────────────────────────

def _obs_url(page_name: str, params: dict | None = None) -> str:
    base = f"{OBS_BASE}/{page_name}?lang={LANG}"
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
    parts: set[str] = set()
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{3,}", title.lower()):
        parts.add(w)
    stop = {"bir", "ile", "için", "olan", "veya", "gibi", "daha", "her",
            "and", "the", "for", "with", "that", "this", "are", "from"}
    freq: dict[str, int] = {}
    for w in re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{4,}", content.lower()):
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    parts.update(sorted(freq, key=lambda w: -freq[w])[:20])
    return " ".join(sorted(parts))


def _nav_to_frame(page, url: str, timeout_ms: int) -> str:
    """
    Navigate the outer index.aspx page's IFRAME1 to a given URL.
    Returns the iframe's rendered HTML, or '' on failure.
    """
    try:
        # Inject the URL into IFRAME1 directly via JS
        page.evaluate(f"document.getElementById('IFRAME1').src = '{url}';")
        page.wait_for_timeout(3000)

        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                html = frame.content()
                if len(html) > 300:
                    return html
            except Exception:
                pass
    except Exception as exc:
        logger.debug(f"_nav_to_frame failed for {url}: {exc}")
    return ""


def _direct_fetch(page, url: str, timeout_ms: int) -> str:
    """
    Navigate directly to an inner page URL (not via iframe).
    Returns rendered HTML.
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(2000)
        return page.content()
    except Exception as exc:
        logger.debug(f"_direct_fetch failed for {url}: {exc}")
    return ""


def _scrape_page(fetch_html_fn, url: str, fallback_title: str,
                 category: str, min_len: int,
                 out_results: list, out_log: list,
                 saved_urls: set) -> bool:
    """Fetch a page, extract content, append to out_results."""
    if url in saved_urls:
        return False

    html    = fetch_html_fn(url)
    content = _clean(html)
    if len(content) < min_len:
        out_log.append(f"    SKIP (short {len(content)}ch): {url}")
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            ),
            locale="tr-TR",
        )

        # ── Page A: outer shell (for iframe injection) ─────────────────────
        shell_page = ctx.new_page()
        shell_page.set_default_timeout(timeout_ms)

        # ── Page B: direct navigation to inner pages ───────────────────────
        inner_page = ctx.new_page()
        inner_page.set_default_timeout(timeout_ms)

        def fetch_direct(url: str) -> str:
            return _direct_fetch(inner_page, url, timeout_ms)

        # ── STEP 0: Load outer shell ───────────────────────────────────────
        shell_url = f"{OBS_BASE}/index.aspx?lang={LANG}"
        out_log.append(f"Loading shell: {shell_url}")
        try:
            shell_page.goto(shell_url, wait_until="domcontentloaded",
                            timeout=timeout_ms)
            shell_page.wait_for_timeout(3000)
            out_log.append("Shell loaded OK")
        except Exception as exc:
            out_log.append(f"Shell load warning: {exc}")

        # ── STEP 1: Discover programs ──────────────────────────────────────
        out_log.append("\n" + "=" * 60)
        out_log.append("STEP 1: Discovering programs...")
        out_log.append("=" * 60)

        program_list: list[dict] = []
        seen_ids: set[str] = set()

        for degree_label, dtype in DEGREE_TYPES:
            unit_url = _obs_url("unitSelection.aspx", {"type": dtype})
            out_log.append(f"\n[{degree_label}] → {unit_url}")

            html = fetch_direct(unit_url)
            if not html:
                out_log.append("  No response, trying alternate type params...")
                # Try alternate type spellings
                for alt in ["l", "ol", "yl", "dr", "myo2"]:
                    if alt == dtype:
                        continue
                    alt_url = _obs_url("unitSelection.aspx", {"type": alt})
                    html = fetch_direct(alt_url)
                    if len(html) > 500:
                        out_log.append(f"  Worked with type={alt}")
                        break

            if not html:
                out_log.append("  FAILED - skipping")
                continue

            # Parse program links from the listing page
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Look for links containing curUnit/curSunit
            found = 0
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)

                m_unit  = re.search(r"curUnit=(\d+)", href, re.IGNORECASE)
                m_sunit = re.search(r"curSunit=(\d+)", href, re.IGNORECASE)

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

            out_log.append(f"  Found {found} programs")

            # Also save the listing page itself
            content = _clean(html)
            if len(content) >= min_len:
                t = _title(html, f"OBS {degree_label} Programları")
                out_results.append({
                    "url":      unit_url,
                    "title":    t,
                    "category": "programs",
                    "content":  content[:6000],
                    "keywords": _keywords(t, content),
                })
                out_log.append(f"  Listing page saved ({len(content)}ch)")

        # Ensure known program is included for verification
        if not any(p["curSunit"] == "6246" for p in program_list):
            program_list.append({
                "degree": "Lisans", "name": "Bilgisayar Mühendisliği",
                "curUnit": "14", "curSunit": "6246",
            })

        out_log.append(f"\nTotal programs: {len(program_list)}")
        for prog in program_list:
            out_log.append(
                f"  [{prog['degree']}] {prog['name']} "
                f"(unit={prog['curUnit']}, sunit={prog['curSunit']})"
            )

        # ── STEP 2: Institutional pages ────────────────────────────────────
        out_log.append("\n" + "=" * 60)
        out_log.append("STEP 2: Institutional pages...")
        out_log.append("=" * 60)

        for page_file, label in INSTITUTIONAL_PAGES:
            url = _obs_url(page_file)
            out_log.append(f"\n→ {label}")
            _scrape_page(fetch_direct, url, f"OBS - {label}",
                         "general", min_len, out_results, out_log, saved_urls)
            time.sleep(delay)

        # ── STEP 3: Each program × sub-pages ──────────────────────────────
        out_log.append("\n" + "=" * 60)
        out_log.append(f"STEP 3: {len(program_list)} programs × "
                       f"{len(PROGRAM_PAGES)} pages each...")
        out_log.append("=" * 60)

        course_links: list[tuple[str, str]] = []  # (url, prog_name)

        for i, prog in enumerate(program_list, 1):
            name      = prog["name"]
            cur_unit  = prog["curUnit"]
            cur_sunit = prog["curSunit"]
            degree    = prog["degree"]
            params    = {"curUnit": cur_unit, "curSunit": cur_sunit}

            out_log.append(
                f"\n[{i}/{len(program_list)}] {degree} → {name} "
                f"(unit={cur_unit}, sunit={cur_sunit})"
            )

            for page_file, op_label, op_name in PROGRAM_PAGES:
                url = _obs_url(page_file, params)
                out_log.append(f"  [{op_name}] {url}")
                ok = _scrape_page(
                    fetch_direct, url,
                    f"{name} — {op_name}",
                    "programs", min_len,
                    out_results, out_log, saved_urls,
                )
                if ok:
                    time.sleep(delay)

                    # If this is the course list, collect course links
                    if page_file == "ShowCourseList.aspx":
                        html = inner_page.content()
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(html, "html.parser")
                        for a in soup.find_all("a", href=True):
                            href = a["href"]
                            if ("ShowCourse" in href or "curCourse" in href
                                    or "courseCode" in href):
                                if href.startswith("http"):
                                    full = href
                                elif href.startswith("/"):
                                    full = "https://obs.acibadem.edu.tr" + href
                                else:
                                    full = f"{OBS_BASE}/{href.lstrip('/')}"
                                if "lang=" not in full:
                                    sep = "&" if "?" in full else "?"
                                    full += f"{sep}lang={LANG}"
                                course_links.append((full, name))

        # ── STEP 4: Individual course pages ───────────────────────────────
        out_log.append("\n" + "=" * 60)
        out_log.append("STEP 4: Individual course detail pages...")
        out_log.append("=" * 60)

        # Deduplicate
        seen_courses: set[str] = set()
        unique_courses: list[tuple[str, str]] = []
        for (url, prog_name) in course_links:
            if url not in seen_courses and url not in saved_urls:
                seen_courses.add(url)
                unique_courses.append((url, prog_name))

        out_log.append(f"Unique course pages: {len(unique_courses)}")

        for i, (url, prog_name) in enumerate(unique_courses, 1):
            out_log.append(f"\n[{i}/{len(unique_courses)}] {prog_name}: {url}")
            ok = _scrape_page(
                fetch_direct, url,
                f"{prog_name} Ders Detayı",
                "programs", min_len,
                out_results, out_log, saved_urls,
            )
            if ok:
                time.sleep(delay)

        ctx.close()
        browser.close()

    out_log.append("\nPlaywright task complete.")


# ── Django management command ──────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Scrape obs.acibadem.edu.tr Bologna Information System"

    def add_arguments(self, parser):
        parser.add_argument("--delay", type=float, default=0.5,
                            help="Delay between requests (default: 0.5s)")
        parser.add_argument("--timeout", type=int, default=30,
                            help="Page timeout in seconds (default: 30)")
        parser.add_argument("--min-length", type=int, default=150,
                            help="Min content length to save (default: 150)")

    def handle(self, *args, **options):
        delay      = options["delay"]
        timeout_ms = options["timeout"] * 1000
        min_len    = options["min_length"]

        saved_urls = set(KnowledgeEntry.objects.values_list("source_url", flat=True))
        self.stdout.write(
            self.style.HTTP_INFO(
                f"OBS Bologna Scraper\n"
                f"  Already in DB : {len(saved_urls)} URLs\n"
                f"  Timeout       : {options['timeout']}s\n"
                f"  Delay         : {delay}s\n"
                f"  Min length    : {min_len} chars\n"
            )
        )

        out_results: list[dict] = []
        out_log:     list[str]  = []

        t = threading.Thread(
            target=_playwright_task,
            args=(saved_urls, delay, timeout_ms, min_len, out_results, out_log),
            daemon=True,
        )
        t.start()
        t.join(timeout=7200)  # max 2 hours

        for line in out_log:
            self.stdout.write(line)

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"Saving {len(out_results)} pages to DB...")
        self.stdout.write("=" * 60)

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
                        f"  NEW  [{item['category']}] {item['title'][:65]}"
                    ))
                else:
                    updated += 1
                    self.stdout.write(
                        f"  UPD  [{item['category']}] {item['title'][:65]}"
                    )
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.WARNING(f"  DB ERR: {exc}"))

        total = KnowledgeEntry.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"\n{'=' * 60}\n"
            f"Done!  Created={created} | Updated={updated} | "
            f"Errors={errors} | Total KB={total}\n"
            f"{'=' * 60}"
        ))
