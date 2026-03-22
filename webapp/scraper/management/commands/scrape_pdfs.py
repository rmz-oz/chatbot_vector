"""
scrape_pdfs.py
--------------
Acibadem sitesindeki tüm PDF dosyalarını bulur, indirir, metni çıkarır
ve KnowledgeEntry olarak DB'ye kaydeder.

Strateji (hızlı):
  1. DB content'inde regex ile PDF linklerini bul (sayfa yeniden çekilmez)
  2. Bilinen PDF-zengin sayfaları fetch et
  3. Yönetmelik/mevzuat sayfalarını özel olarak tara
  4. Her PDF'i indir → pdfplumber → DB'ye kaydet
"""

import hashlib
import io
import re
import time

import pdfplumber
import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from chat.models import KnowledgeEntry

BASE = "https://www.acibadem.edu.tr"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 30
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# PDF link regex — hem tam URL hem de /sites/... path'leri
PDF_HREF_RE = re.compile(
    r'href=["\']([^"\']*\.pdf)["\']', re.IGNORECASE
)

# Bilinen PDF-zengin sayfalar
KNOWN_PDF_PAGES = [
    "/ogrenci/ogrenci-isleri/akademik-takvim",
    "/ogrenci/ogrenci-isleri",
    "/ogrenci/ogrenci-isleri/yatay-gecis/kurum-ici-yatay-gecis",
    "/ogrenci/ogrenci-isleri/yatay-gecis/kurumlar-arasi-yatay-gecis",
    "/ogrenci/ogrenci-isleri/dikey-gecis",
    "/ogrenci/ogrenci-isleri/cift-anadal-yandal-programlari",
    "/ogrenci/ogrenci-isleri/mezunlar",
    "/ogrenci/burslar",
    "/ogrenci/saglik-bilimleri-meslek-yuksekokulu/burs-ve-odeme-bilgileri",
    "/aday/ogrenci/kayit",
    "/aday/ogrenci/burs-ve-ucretler",
    "/universite/kurum-politikalari",
    "/universite/hakkinda/kisisel-verilerin-korunmasi",
    "/universite/hakkinda/uzaktan-egitim-kisisel-verilerin-korunmasi",
    "/universite/kalite-guvence-sistemi",
    "/universite/kalite-guvence-sistemi/hakkinda",
    "/universite/ogretim-elemani-el-kitabi-2021-2022",
    "/universite/ihaleler",
    "/universite/acu-mezunlari",
    "/uluslararasi-ofis/uluslararasi-ogrenciler/faydali-bilgiler",
    "/uluslararasi-ofis/uluslararasi-ogrenciler/kayit",
    "/uluslararasi-ofis/uluslararasi-ogrenciler/faydali-bilgiler/odeme-yontemleri",
    "/akademik/lisans/tip-fakultesi",
    "/akademik/on-lisans/saglik-bilimleri-meslek-yuksekokulu",
    "/arastirma/etik-kurulu",
    "/arastirma/arastirma-destek-ofisi",
    "/surdurulebilir-kampus",
    "/komisyonlar",
    "/komisyonlar/etik-kurul",
    "/komisyonlar/bagimlilikla-mucadele-komisyonu",
    "/komisyonlar/toplumsal-cinsiyet-esitligi-komisyonu/toplumsal-cinsiyet-esitligi-komisyonu-tocek",
    "/ogrenci/ogrenci-topluluklar",
    "/universite/hakkinda/stratejik-plan",
    "/universite/hakkinda/faaliyet-raporlari",
]

CATEGORY_MAP = [
    ("akademik-takvim",  "akademik"),
    ("yonetmelik",       "mevzuat"),
    ("yonerge",          "mevzuat"),
    ("mevzuat",          "mevzuat"),
    ("katalog",          "kurumsal"),
    ("burs",             "ogrenci"),
    ("kvkk",             "hukuki"),
    ("aydinlatma",       "hukuki"),
    ("kisisel-veri",     "hukuki"),
    ("basvuru",          "ogrenci"),
    ("enstitu",          "akademik"),
    ("etik",             "arastirma"),
    ("ihale",            "idari"),
    ("faaliyet",         "kurumsal"),
    ("strateji",         "kurumsal"),
    ("el-kitabi",        "personel"),
    ("mezun",            "ogrenci"),
    ("kayit",            "ogrenci"),
    ("yatay",            "ogrenci"),
    ("surdurulebilir",   "kurumsal"),
    ("erasmus",          "uluslararasi"),
    ("uluslararasi",     "uluslararasi"),
]


def guess_category(url: str) -> str:
    u = url.lower()
    for kw, cat in CATEGORY_MAP:
        if kw in u:
            return cat
    return "genel"


def guess_keywords(url: str, text: str) -> str:
    fname = url.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ")
    # URL encoding temizle
    fname = re.sub(r'%[0-9A-Fa-f]{2}', ' ', fname)
    words = set(fname.lower().split())
    for kw in ["yönetmelik", "takvim", "akademik", "lisans", "burs", "kayıt",
                "mezuniyet", "yönerge", "etik", "katalog", "mevzuat", "erasmus",
                "uluslararası", "strateji", "faaliyet", "ihale", "kvkk"]:
        if kw in text.lower():
            words.add(kw)
    return ", ".join(sorted(words)[:20])


def normalize_pdf_url(href: str) -> str | None:
    """Relative veya absolute PDF href'i tam URL'e çevir."""
    href = href.strip()
    if not href.lower().endswith(".pdf"):
        return None
    if href.startswith("http"):
        # Sadece acibadem.edu.tr PDF'leri
        if "acibadem.edu.tr" in href:
            return href
        return None  # Başka domain PDF'leri atla
    if href.startswith("/"):
        return BASE + href
    return None


def collect_pdf_links_from_page(url: str) -> list[str]:
    """Bir sayfadaki tüm PDF linklerini döndür."""
    try:
        r = SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        pdfs = []
        for a in soup.find_all("a", href=True):
            norm = normalize_pdf_url(a["href"])
            if norm:
                pdfs.append(norm)
        return list(set(pdfs))
    except Exception:
        return []


def extract_pdf_text(url: str) -> tuple[str, int]:
    """PDF'i indir ve metni çıkar. (text, page_count) döndür."""
    try:
        r = SESSION.get(url, timeout=TIMEOUT, stream=True)
        if r.status_code != 200:
            return "", 0
        content = r.content
        if len(content) < 200:
            return "", 0

        pages_text = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t.strip())

        full_text = "\n\n".join(pages_text)
        return full_text, page_count
    except Exception:
        return "", 0


def pdf_title_from_url(url: str, text: str) -> str:
    """PDF başlığı: ilk anlamlı satır veya dosya adından."""
    if text:
        for line in text.split("\n"):
            line = line.strip()
            if len(line) > 10:
                return line[:120]
    name = url.split("/")[-1].replace(".pdf", "")
    name = re.sub(r'%[0-9A-Fa-f]{2}', ' ', name)
    name = name.replace("-", " ").replace("_", " ")
    return name.title()[:120]


class Command(BaseCommand):
    help = "Acibadem sitesindeki tüm PDF dosyalarını bulur ve DB'ye kaydeder"

    def add_arguments(self, parser):
        parser.add_argument("--delay", type=float, default=0.3,
                            help="PDF indirme arası bekleme (sn)")
        parser.add_argument("--min-length", type=int, default=100,
                            help="Min metin uzunluğu (karakter)")
        parser.add_argument("--skip-existing", action="store_true", default=False,
                            help="Zaten DB'de olan PDF URL'leri atla")

    def handle(self, *args, **options):
        delay = options["delay"]
        min_length = options["min_length"]
        skip_existing = options["skip_existing"]

        self.stdout.write("=" * 65)
        self.stdout.write("PDF SCRAPER BAŞLATILIYOR")
        self.stdout.write("=" * 65)

        all_pdf_urls: set[str] = set()

        # ── Adım 1: DB içeriklerinde PDF linklerini regex ile bul ──────
        self.stdout.write("\n[1/3] DB içeriklerinde PDF linkleri aranıyor (regex)...")
        entries = KnowledgeEntry.objects.all().values_list("source_url", "content")
        for src_url, content in entries:
            # Content'teki href'leri regex ile tara
            for match in PDF_HREF_RE.finditer(content or ""):
                norm = normalize_pdf_url(match.group(1))
                if norm:
                    all_pdf_urls.add(norm)
            # source_url'nin kendisi PDF mi?
            if src_url and src_url.lower().endswith(".pdf"):
                norm = normalize_pdf_url(src_url)
                if norm:
                    all_pdf_urls.add(norm)

        self.stdout.write(f"  → DB taramasından {len(all_pdf_urls)} PDF URL bulundu")

        # ── Adım 2: Bilinen PDF-zengin sayfaları fetch et ───────────────
        self.stdout.write(f"\n[2/3] {len(KNOWN_PDF_PAGES)} bilinen sayfa fetch ediliyor...")
        for i, path in enumerate(KNOWN_PDF_PAGES, 1):
            url = BASE + path
            found = collect_pdf_links_from_page(url)
            new = [u for u in found if u not in all_pdf_urls]
            all_pdf_urls.update(found)
            self.stdout.write(
                f"  [{i:02d}/{len(KNOWN_PDF_PAGES)}] +{len(new):3d} yeni | "
                f"Toplam={len(all_pdf_urls)} | {path}"
            )
            time.sleep(delay * 0.5)

        self.stdout.write(f"\n  → Toplam {len(all_pdf_urls)} unique PDF URL")

        # ── Adım 3: Her PDF indir ve kaydet ────────────────────────────
        self.stdout.write(f"\n[3/3] {len(all_pdf_urls)} PDF indiriliyor ve işleniyor...")
        created = updated = skipped = errors = 0
        pdf_list = sorted(all_pdf_urls)

        for i, pdf_url in enumerate(pdf_list, 1):
            fname = pdf_url.split("/")[-1][:55]

            if skip_existing and KnowledgeEntry.objects.filter(source_url=pdf_url).exists():
                self.stdout.write(f"  [{i}/{len(pdf_list)}] ATLA (var): {fname}")
                skipped += 1
                continue

            self.stdout.write(f"  [{i}/{len(pdf_list)}] ⬇  {fname}")
            text, page_count = extract_pdf_text(pdf_url)

            if len(text) < min_length:
                self.stdout.write(f"    ✗ Metin çok kısa ({len(text)} kr) — atlanıyor")
                errors += 1
                time.sleep(delay)
                continue

            title = pdf_title_from_url(pdf_url, text)
            category = guess_category(pdf_url)
            keywords = guess_keywords(pdf_url, text)
            content_saved = text[:50_000]   # Max 50k karakter
            uid = hashlib.md5(pdf_url.encode()).hexdigest()[:8]

            obj, was_created = KnowledgeEntry.objects.update_or_create(
                source_url=pdf_url,
                defaults={
                    "title":    f"{title} [PDF-{uid}]",
                    "category": category,
                    "content":  content_saved,
                    "keywords": keywords,
                }
            )

            if was_created:
                created += 1
                self.stdout.write(
                    f"    ✓ YENİ: {title[:60]} ({page_count} sayfa, {len(content_saved)} kr)"
                )
            else:
                updated += 1
                self.stdout.write(f"    ↻ Güncellendi: {title[:60]}")

            time.sleep(delay)

        self.stdout.write("\n" + "=" * 65)
        self.stdout.write(
            f"TAMAMLANDI — Oluşturuldu={created} | Güncellendi={updated} | "
            f"Atlandı={skipped} | Hata/Kısa={errors}"
        )
        self.stdout.write(f"DB TOPLAM KAYIT: {KnowledgeEntry.objects.count()}")
        self.stdout.write("=" * 65)
