"""
Load a curated base knowledge set into the database.
Run this before scrape_website so the chatbot has something to answer with
even before the full crawl completes.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from chat.models import KnowledgeEntry

BASE_DATA = [
    {
        "title": "About Acıbadem University",
        "category": "general",
        "source_url": "https://www.acibadem.edu.tr/en/about-us/",
        "keywords": "acıbadem university founded 2007 istanbul private health sciences foundation yök accredited",
        "content": (
            "Acıbadem University (Acıbadem Üniversitesi) is a private, non-profit foundation university "
            "in Istanbul, Turkey, established in 2007 by the Acıbadem Healthcare Group. "
            "Accredited by the Council of Higher Education of Turkey (YÖK). Focuses on health sciences, "
            "medicine, dentistry, pharmacy, and related fields. Motto: 'Science, Technology, Ethics'. "
            "Over 5,000 students and ~500 academic staff. Unique strength: direct clinical integration "
            "with 20+ Acıbadem hospitals across Turkey."
        ),
    },
    {
        "title": "Vision, Mission and Values",
        "category": "general",
        "source_url": "https://www.acibadem.edu.tr/en/about-us/vision/",
        "keywords": "vision mission values quality international research ethics excellence innovation",
        "content": (
            "Vision: Internationally recognised university leading innovation in health sciences. "
            "Mission: Educate highest-quality healthcare professionals by integrating academic excellence "
            "with clinical practice, fostering research, and upholding ethical values. "
            "Core values: excellence, ethics and integrity, innovation, patient-centred philosophy, "
            "international collaboration, social responsibility."
        ),
    },
    {
        "title": "Faculty of Medicine",
        "category": "programs",
        "source_url": "https://www.acibadem.edu.tr/en/faculties/faculty-of-medicine/",
        "keywords": "medicine medical school tıp fakültesi 6 years MD doctor clinical MF-3 YKS",
        "content": (
            "6-year MD programme taught in Turkish (medical literature in English). "
            "Years 1–3: pre-clinical sciences. Years 4–6: clinical rotations in Acıbadem hospitals. "
            "Curriculum includes problem-based learning (PBL), simulation labs, early patient contact from year 2. "
            "Graduates receive 'Tıp Doktoru (MD)'. Admission: MF-3 score type in YKS."
        ),
    },
    {
        "title": "Faculty of Dentistry",
        "category": "programs",
        "source_url": "https://www.acibadem.edu.tr/en/faculties/faculty-of-dentistry/",
        "keywords": "dentistry diş hekimliği 5 years DDS dental oral surgery orthodontics MF-3",
        "content": (
            "5-year DDS programme. Clinical patient treatment starts from year 3 under supervision. "
            "Covers oral surgery, orthodontics, endodontics, prosthetics, periodontics. "
            "Modern digital dentistry lab (CAD/CAM). Admission: MF-3 score in YKS."
        ),
    },
    {
        "title": "Faculty of Pharmacy",
        "category": "programs",
        "source_url": "https://www.acibadem.edu.tr/en/faculties/faculty-of-pharmacy/",
        "keywords": "pharmacy eczacılık 5 years pharmacology drug hospital community",
        "content": (
            "5-year programme leading to 'Eczacı' (Pharmacist) degree. "
            "Covers pharmaceutical chemistry, pharmacology, clinical pharmacy, drug formulation. "
            "Graduates work in community pharmacies, hospitals, pharmaceutical industry, or regulatory agencies. "
            "Internships in both community and hospital settings."
        ),
    },
    {
        "title": "Faculty of Health Sciences",
        "category": "programs",
        "source_url": "https://www.acibadem.edu.tr/en/faculties/faculty-of-health-sciences/",
        "keywords": "nursing physiotherapy health management social work nutrition occupational therapy 4 years",
        "content": (
            "4-year Bachelor programmes: Nursing, Physiotherapy and Rehabilitation, Health Management, "
            "Social Work, Nutrition and Dietetics, Occupational Therapy. "
            "All include clinical placements in the Acıbadem hospital network."
        ),
    },
    {
        "title": "Graduate Programs (Master's and PhD)",
        "category": "programs",
        "source_url": "https://www.acibadem.edu.tr/en/graduate/",
        "keywords": "masters PhD doctoral graduate yüksek lisans doktora biochemistry anatomy molecular medicine",
        "content": (
            "Master's: Medical Biochemistry, Anatomy, Physiology, Medical Microbiology, Histology, "
            "Biophysics, Health Management, Clinical Psychology, Public Health, Nutrition, Molecular Medicine. "
            "PhD: Basic Medical Sciences, Molecular Medicine, Health Sciences. "
            "Some programmes offered with or without thesis."
        ),
    },
    {
        "title": "Vocational School of Health Services",
        "category": "programs",
        "source_url": "https://www.acibadem.edu.tr/en/vocational-school/",
        "keywords": "vocational 2 year associate önlisans medical imaging anesthesia laboratory emergency",
        "content": (
            "2-year associate degree programmes: Medical Imaging Techniques, Operating Room Services, "
            "Anesthesia, Oral and Dental Health, Medical Laboratory Techniques, First and Emergency Aid, "
            "Medical Documentation, Child Development, Elderly Care, Pharmacy Services."
        ),
    },
    {
        "title": "Undergraduate Admission – Turkish Citizens (YKS)",
        "category": "admission",
        "source_url": "https://www.acibadem.edu.tr/en/admissions/",
        "keywords": "admission YKS TYT AYT MF-3 score Turkish undergraduate lisans başvuru ÖSYM",
        "content": (
            "Turkish citizens apply via YKS central exam (ÖSYM). "
            "Medicine and Dentistry: MF-3 score. Health Sciences: MF-3, TM or YDİL depending on programme. "
            "Minimum scores vary annually — check yokatlas.yok.gov.tr. "
            "Applications submitted July/August at osym.gov.tr."
        ),
    },
    {
        "title": "International Student Admission",
        "category": "admission",
        "source_url": "https://www.acibadem.edu.tr/en/international/admissions/",
        "keywords": "international foreign student SAT IB documents uluslararası başvuru apply",
        "content": (
            "Accepted exams: SAT, ACT, A-Levels, IB Diploma, or equivalent. "
            "Required: high school diploma (notarised Turkish/English translation), transcripts, "
            "passport copy, exam results, language certificate. "
            "Fall applications: May–July. Spring: October–November. "
            "Contact: international@acibadem.edu.tr | +90 216 500 4444."
        ),
    },
    {
        "title": "Graduate Program Admission",
        "category": "admission",
        "source_url": "https://www.acibadem.edu.tr/en/graduate/admissions/",
        "keywords": "graduate masters PhD ALES GRE TOEFL IELTS lisansüstü admission",
        "content": (
            "Master's: Bachelor's degree, ALES ≥ 55 (or GRE/GMAT), TOEFL IBT ≥ 60 or IELTS ≥ 5.5, "
            "2 reference letters, statement of purpose. "
            "PhD: Master's degree, ALES ≥ 70, research proposal, interview. "
            "Contact: graduate@acibadem.edu.tr."
        ),
    },
    {
        "title": "Tuition Fees",
        "category": "fees",
        "source_url": "https://www.acibadem.edu.tr/en/admissions/tuition-fees/",
        "keywords": "tuition fees cost price ücret annual medicine dentistry nursing scholarship burs",
        "content": (
            "Approximate annual tuition (verify at acibadem.edu.tr — updated annually): "
            "Medicine: 450,000–550,000 TRY (~$15,000–18,000 USD). "
            "Dentistry: 350,000–450,000 TRY. Pharmacy: 300,000–400,000 TRY. "
            "Nursing: 200,000–300,000 TRY. Other health sciences: 180,000–280,000 TRY. "
            "Master's: 80,000–150,000 TRY. PhD: 100,000–180,000 TRY."
        ),
    },
    {
        "title": "Scholarships and Financial Aid",
        "category": "fees",
        "source_url": "https://www.acibadem.edu.tr/en/admissions/scholarships/",
        "keywords": "scholarship burs financial aid discount grant indirim merit top ranking KYK",
        "content": (
            "Merit-based (YKS ranking): top 1,000 → 100% scholarship; top 2,500 → 75%; "
            "top 5,000 → 50%; top 10,000 → 25%. Maintained on minimum GPA. "
            "Sibling discount: 10%. KYK student loans applicable. "
            "International scholarship packages: international@acibadem.edu.tr."
        ),
    },
    {
        "title": "Campus Location and Transportation",
        "category": "campus",
        "source_url": "https://www.acibadem.edu.tr/en/about-us/campus/",
        "keywords": "campus location Kayışdağı Ataşehir Istanbul address metro bus transport ulaşım",
        "content": (
            "Address: Kayışdağı Mah., Örnek Sok. No:1, 34752 Ataşehir/Istanbul. "
            "Transport: Metrobüs (Acıbadem Üniversitesi stop, E5), Metro M4, IETT buses, campus parking. "
            "~30 min from Kadıköy ferry, ~45 min from Taksim, ~60 min from Istanbul Airport."
        ),
    },
    {
        "title": "Campus Facilities",
        "category": "campus",
        "source_url": "https://www.acibadem.edu.tr/en/about-us/campus/facilities/",
        "keywords": "library lab simulation cafeteria sports health center dormitory kütüphane spor",
        "content": (
            "Digital library (50,000+ volumes, PubMed, Cochrane, UpToDate, ScienceDirect). "
            "Simulation and skills labs (SIMMER), anatomy lab, computer labs, smart lecture halls. "
            "Cafeteria, sports facilities (gym, courts), student health centre, counselling centre. "
            "Adjacent to Acıbadem Altunizade Hospital. No on-campus dormitory — nearby private residences available."
        ),
    },
    {
        "title": "Hospital Network – Clinical Training",
        "category": "campus",
        "source_url": "https://www.acibadem.edu.tr/en/about-us/hospital-network/",
        "keywords": "hospital network clinical training Altunizade Maslak Fulya Kadıköy Istanbul JCI",
        "content": (
            "Students train in 20+ Acıbadem hospitals. Key Istanbul sites: "
            "Altunizade (adjacent to campus, primary training), Maslak (largest), Fulya, Kadıköy, "
            "International, Taksim, Bakırköy. Also hospitals in Bursa, Adana, Eskişehir. "
            "60+ polyclinics nationwide. All JCI accredited."
        ),
    },
    {
        "title": "Research Centers and Institutes",
        "category": "research",
        "source_url": "https://www.acibadem.edu.tr/en/research/",
        "keywords": "research center genetics molecular medicine simulation sports TÜBİTAK EU AÜTAM SIMMER",
        "content": (
            "Key centres: Labgen (genetic diseases), AÜTAM – Center for Translational Medicine "
            "(cancer, cardiovascular, rare diseases), SIMMER – Medical Simulation Center (VR surgical training), "
            "Sports Medicine Research Center. Funded by TÜBİTAK, EU Horizon, Acıbadem Group."
        ),
    },
    {
        "title": "Student Clubs and Activities",
        "category": "student_life",
        "source_url": "https://www.acibadem.edu.tr/en/student-life/",
        "keywords": "student clubs activities sports social photography music theater debate kulüp",
        "content": (
            "50+ active clubs: Medical Students Association, Nursing Students Association, "
            "Emergency Medicine Club, Surgery Club, Photography, Theater, Music, Environmental Awareness, Debate; "
            "basketball, volleyball, football, swimming, martial arts teams. "
            "Annual events: Foundation Week (May), Health Sciences Olympiad, Cultural Festival, Career Fair."
        ),
    },
    {
        "title": "Student Support Services",
        "category": "student_life",
        "source_url": "https://www.acibadem.edu.tr/en/student-life/support-services/",
        "keywords": "counseling health psychological support disability career advising danışmanlık",
        "content": (
            "Free student health centre, discounted Acıbadem Hospital access. "
            "Free psychological counselling (individual & group), crisis intervention — fully confidential. "
            "Academic advising, peer tutoring, writing centre, career development centre. "
            "Disability support: accessibility services, assistive technology. "
            "Career services: fairs, internship placement, CV/interview coaching, alumni mentorship."
        ),
    },
    {
        "title": "Erasmus+ and Exchange Programs",
        "category": "international",
        "source_url": "https://www.acibadem.edu.tr/en/international/exchange/",
        "keywords": "Erasmus exchange abroad semester partner Europe Mevlana bilateral değişim",
        "content": (
            "Erasmus+ agreements with 60+ European universities (Germany, Netherlands, Spain, Italy, "
            "Czech Republic, Poland etc.). Study 1–2 semesters abroad or complete traineeships. "
            "Mevlana Exchange with 150+ countries. Medical students: 4-week international clinical elective in year 5/6. "
            "Erasmus applications: erasmus@acibadem.edu.tr."
        ),
    },
    {
        "title": "International Student Support",
        "category": "international",
        "source_url": "https://www.acibadem.edu.tr/en/international/",
        "keywords": "international student visa residence permit Turkish language buddy orientation yabancı uyruklu",
        "content": (
            "International Student Office: orientation week, buddy programme, Turkish language courses, "
            "visa and residence permit guidance, health insurance assistance, airport pickup (on request), housing assistance. "
            "Location: Main Campus, Administrative Building, Ground Floor. "
            "Email: international@acibadem.edu.tr | Tel: +90 216 500 4444."
        ),
    },
    {
        "title": "Contact Information",
        "category": "contact",
        "source_url": "https://www.acibadem.edu.tr/en/contact/",
        "keywords": "contact phone email address website iletişim telefon adres faks",
        "content": (
            "Address: Kayışdağı Mah., Örnek Sok. No:1, 34752 Ataşehir/Istanbul. "
            "Tel: +90 216 500 4444 | Fax: +90 216 576 4488 | Web: www.acibadem.edu.tr. "
            "Admissions: admissions@acibadem.edu.tr | International: international@acibadem.edu.tr | "
            "Graduate: graduate@acibadem.edu.tr | Erasmus: erasmus@acibadem.edu.tr | "
            "Student Affairs: studentaffairs@acibadem.edu.tr. Mon–Fri 08:30–17:30."
        ),
    },
]


class Command(BaseCommand):
    help = "Load base Acıbadem University knowledge into the database"

    def handle(self, *args, **kwargs):
        created = updated = 0
        for item in BASE_DATA:
            _, is_new = KnowledgeEntry.objects.update_or_create(
                source_url=item["source_url"],
                defaults={
                    "title":      item["title"],
                    "category":   item["category"],
                    "content":    item["content"],
                    "keywords":   item.get("keywords", ""),
                    "scraped_at": timezone.now(),
                },
            )
            if is_new:
                created += 1
            else:
                updated += 1

        total = KnowledgeEntry.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"Base knowledge loaded: {created} created, {updated} updated. Total: {total} entries."
        ))
