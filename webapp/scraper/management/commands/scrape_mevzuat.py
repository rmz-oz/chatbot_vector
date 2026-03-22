"""
scrape_mevzuat.py
-----------------
Acibadem Üniversitesi'ne ait tüm yönetmelikleri mevzuat.gov.tr üzerinden
PDF olarak indirir, metni çıkarır ve KnowledgeEntry olarak DB'ye kaydeder.

Ek olarak acibadem.edu.tr/merkezler/uzem/hakkinda/mevzuat sayfasındaki
linkleri de tarar.
"""

import io
import time
import hashlib

import pdfplumber
import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from chat.models import KnowledgeEntry

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# mevzuat.gov.tr PDF URL pattern
PDF_BASE = "https://www.mevzuat.gov.tr/File/GeneratePdf?mevzuatNo={no}&mevzuatTur=UniversiteYonetmeligi&mevzuatTertip=5"

# Bilinen Acibadem yönetmelikleri (MevzuatNo → Başlık)
KNOWN_REGULATIONS = {
    "16620": "Acıbadem Üniversitesi Ön Lisans ve Lisans Eğitim-Öğretim ve Sınav Yönetmeliği",
    "23287": "Acıbadem Mehmet Ali Aydınlar Üniversitesi Lisansüstü Eğitim, Öğretim ve Sınav Yönetmeliği",
    "13735": "Acıbadem Üniversitesi Ana Yönetmeliği",
    "12561": "Acıbadem Üniversitesi Sürekli Eğitim ve Gelişim Merkezi Yönetmeliği",
    "20541": "Acıbadem Üniversitesi Sağlık Politikaları Uygulama ve Araştırma Merkezi Yönetmeliği",
    "23291": "Acıbadem Üniversitesi Uzaktan Eğitim Uygulama ve Araştırma Merkezi Yönetmeliği",
    "34758": "Acıbadem Mehmet Ali Aydınlar Üniversitesi Biyomalzeme Uygulama ve Araştırma Merkezi Yönetmeliği",
}

# Ayrıca taranan sayfalar (ek PDF linkleri için)
EXTRA_PAGES = [
    "https://www.acibadem.edu.tr/merkezler/uzem/hakkinda/mevzuat",
    "https://www.acibadem.edu.tr/universite/yonerge-yonetmelikler",
    "https://www.acibadem.edu.tr/universite/hakkinda/hukuki-dayanak",
    "https://case.acibadem.edu.tr/case/mevzuat",
]

# Acibadem.edu.tr yönerge PDF'leri (zaten bilinen ama kontrol)
KNOWN_YONERGE_PDFS = [
    (
        "https://www.acibadem.edu.tr/sites/default/files/document/2025/ACU%20%C3%87ift%20Anadal%20Yandal%20Y%C3%B6nergesi%2014.11.2023.pdf",
        "ACU Çift Anadal Yandal Yönergesi"
    ),
    (
        "https://www.acibadem.edu.tr/sites/default/files/document/2025/ACU%20%C4%B0NG%C4%B0L%C4%B0ZCE%20HAZIRLIK%20PROGRAMI%20%C4%B0LE%20%C4%B0NG%C4%B0L%C4%B0ZCE%20DERSLER%C4%B0%20Y%C3%96NERGES%C4%B0.pdf",
        "ACU İngilizce Hazırlık Programı ile İngilizce Dersleri Yönergesi"
    ),
    (
        "https://www.acibadem.edu.tr/sites/default/files/document/2024/acu-yatay-gecis-yonergesi-18.07.2023.pdf",
        "ACU Yatay Geçiş Yönergesi"
    ),
    (
        "https://www.acibadem.edu.tr/sites/default/files/document/2025/acu-katalog-2025.pdf",
        "Acıbadem Üniversitesi Tanıtım Kataloğu 2025"
    ),
    (
        "https://www.acibadem.edu.tr/sites/default/files/document/24.02.2020-acu-ihale-yonetmeligi.pdf",
        "ACU İhale Yönetmeliği"
    ),
]


def download_pdf_text(url: str) -> tuple[str, int]:
    """PDF'i indir, pdfplumber ile metin çıkar. (text, page_count)"""
    try:
        r = SESSION.get(url, timeout=30, stream=True)
        if r.status_code != 200:
            return "", 0
        data = r.content
        if len(data) < 500:
            return "", 0
        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    pages.append(t.strip())
        return "\n\n".join(pages), page_count
    except Exception as e:
        return "", 0


def save_entry(url: str, title: str, category: str, keywords: str, text: str, pages: int, stdout):
    """KnowledgeEntry olarak kaydet veya güncelle."""
    if len(text) < 50:
        stdout.write(f"    ✗ Metin çok kısa ({len(text)} kr), atlanıyor")
        return False, False

    uid = hashlib.md5(url.encode()).hexdigest()[:8]
    obj, created = KnowledgeEntry.objects.update_or_create(
        source_url=url,
        defaults={
            "title":    f"{title} [PDF-{uid}]",
            "category": category,
            "content":  text[:50_000],
            "keywords": keywords,
        }
    )
    tag = "YENİ ✓" if created else "Güncellendi ↻"
    stdout.write(f"    {tag}: {title[:70]} ({pages} sayfa, {len(text):,} kr)")
    return True, created


class Command(BaseCommand):
    help = "Acibadem yönetmelik ve yönergelerini mevzuat.gov.tr'den indirir ve DB'ye kaydeder"

    def handle(self, *args, **options):
        self.stdout.write("=" * 68)
        self.stdout.write("MEVZUAT SCRAPER BAŞLATILIYOR")
        self.stdout.write("=" * 68)

        created_total = updated_total = error_total = 0

        # ── 1. mevzuat.gov.tr yönetmelikleri ───────────────────────────
        self.stdout.write(f"\n[1/3] mevzuat.gov.tr — {len(KNOWN_REGULATIONS)} yönetmelik indiriliyor...")
        for mevzuat_no, title in KNOWN_REGULATIONS.items():
            url = PDF_BASE.format(no=mevzuat_no)
            self.stdout.write(f"\n  [{mevzuat_no}] {title[:60]}")
            text, pages = download_pdf_text(url)
            keywords = "yönetmelik, mevzuat, " + title.lower()[:80]
            ok, created = save_entry(url, title, "mevzuat", keywords, text, pages, self.stdout)
            if ok:
                if created:
                    created_total += 1
                else:
                    updated_total += 1
            else:
                error_total += 1
            time.sleep(0.5)

        # ── 2. Bilinen yönerge PDF'leri ────────────────────────────────
        self.stdout.write(f"\n[2/3] Bilinen yönerge PDF'leri — {len(KNOWN_YONERGE_PDFS)} dosya...")
        for url, title in KNOWN_YONERGE_PDFS:
            self.stdout.write(f"\n  {title[:65]}")
            text, pages = download_pdf_text(url)
            keywords = "yönerge, " + title.lower()[:80]
            ok, created = save_entry(url, title, "mevzuat", keywords, text, pages, self.stdout)
            if ok:
                if created:
                    created_total += 1
                else:
                    updated_total += 1
            else:
                error_total += 1
            time.sleep(0.4)

        # ── 3. Ek sayfalardan PDF link taraması ───────────────────────
        self.stdout.write(f"\n[3/3] Ek sayfalar taranıyor...")
        extra_urls: set[str] = set()
        for page_url in EXTRA_PAGES:
            try:
                r = SESSION.get(page_url, timeout=15)
                if r.status_code != 200:
                    self.stdout.write(f"  {r.status_code}: {page_url}")
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                found = 0
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if ".pdf" in href.lower():
                        if href.startswith("/"):
                            href = "https://www.acibadem.edu.tr" + href
                        if href not in extra_urls:
                            extra_urls.add(href)
                            found += 1
                self.stdout.write(f"  +{found} yeni PDF: {page_url}")
            except Exception as e:
                self.stdout.write(f"  HATA: {page_url} — {e}")
            time.sleep(0.4)

        # Yeni bulunan PDF'leri indir
        already_done = set(KNOWN_YONERGE_PDFS[i][0] for i in range(len(KNOWN_YONERGE_PDFS)))
        already_done.update(PDF_BASE.format(no=n) for n in KNOWN_REGULATIONS)
        new_pdfs = extra_urls - already_done - set(
            KnowledgeEntry.objects.filter(source_url__icontains=".pdf").values_list("source_url", flat=True)
        )

        if new_pdfs:
            self.stdout.write(f"\n  → {len(new_pdfs)} ek PDF indiriliyor...")
            for pdf_url in sorted(new_pdfs):
                fname = pdf_url.split("/")[-1][:60]
                self.stdout.write(f"  ⬇  {fname}")
                text, pages = download_pdf_text(pdf_url)
                title = fname.replace(".pdf", "").replace("-", " ").replace("_", " ").title()[:100]
                ok, created = save_entry(pdf_url, title, "mevzuat", "yönetmelik, yönerge", text, pages, self.stdout)
                if ok:
                    if created:
                        created_total += 1
                    else:
                        updated_total += 1
                else:
                    error_total += 1
                time.sleep(0.4)
        else:
            self.stdout.write("  → Ek yeni PDF bulunamadı")

        self.stdout.write("\n" + "=" * 68)
        self.stdout.write(
            f"TAMAMLANDI — YENİ={created_total} | Güncellendi={updated_total} | Hata={error_total}"
        )
        self.stdout.write(f"DB TOPLAM: {KnowledgeEntry.objects.count()}")
        self.stdout.write("=" * 68)
