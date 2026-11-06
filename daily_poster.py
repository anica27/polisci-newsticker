import datetime
import html
import os
import re
import sys
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
APP_URL = "https://polisci-ticker.streamlit.app"
SEEN_FILE = "seen_ids.txt"
MAILTO = os.environ.get("OPENALEX_MAILTO")

if not BOT_TOKEN:
    print("Fehler: TELEGRAM_BOT_TOKEN fehlt als Umgebungsvariable.")
    sys.exit(1)
if not MAILTO:
    print("Fehler: OPENALEX_MAILTO fehlt. Bitte als GitHub Secret hinterlegen.")
    sys.exit(1)

# Disziplinübergreifender Ausschluss: Naturwissenschaften, Medizin, BWL
GLOBAL_AUSSCHLUSS = [
    # Medizin & Biologie
    "synaptic", "swallowing", "breathing", "diaphragm", "carotid",
    "striatum", "motor nucleus", "vagus", "pyroptotic", "sids",
    "respiratory", "pulmonary", "neuromuscular", "hypoglossal",
    "nurse", "nursing", "midwife", "midwifery", "prenatal", "clinical",
    "patient", "therapy", "cancer", "biomedical", "molecular", "cell",
    "hospital", "inpatient", "surgery", "disease", "pharmacology",
    "hiv", "genomic", "randomised", "randomized", "trial", "enrolment",
    # BWL, Marketing & Konsumforschung
    "marketing", "consumer", "msme", "smes", "supply chain", "firm performance",
    "stock market", "tourist", "hospitality", "hotel", "logistics"
]

UNERWUENSCHTE_TITEL = {
    "conclusion", "conclusions", "introduction", "preface", "index",
    "contents", "editorial", "book reviews", "front matter", "back matter"
}

# 6 trennscharfe Fachkanäle
KANAELE = [
    {
        "titel": "Internationale Beziehungen & Sicherheitspolitik",
        "chat_id": os.environ.get("CHAT_ID_IB"),
        "topics": "T10053",
        "exclude_terms": [
            "epistemology", "metaphysics", "formal logic", "household survey",
            "world bank", "surveillance review", "procurement", "visual essay",
            "fertility", "microfinance"
        ]
    },
    {
        "titel": "Vergleichende Regierungslehre, Wahlen & Parteien",
        "chat_id": os.environ.get("CHAT_ID_VERGLEICH"),
        "topics": "T10108|T11397|T11742",
        "exclude_terms": ["metaphysics", "theology", "macroeconomics"]
    },
    {
        "titel": "Politische Theorie & Ideengeschichte",
        "chat_id": os.environ.get("CHAT_ID_THEORIE"),
        "topics": "T10582|T12752|T11447",
        "exclude_terms": [
            # Wirtschaft & BWL
            "econometric", "consumer", "accounting", "banking", "policy making",
            # Medizin & Bioethik
            "neonatal", "biobank", "clinical", "hiv", "general practice", "participants' altruism", "trial",
            # IB & Außenpolitik
            "peacekeeping", "security council", "gaza", "united nations", "r2p", "asymmetric warfare", "foreign policy"
        ]
    },
    {
        "titel": "Public Policy & Verwaltungswissenschaft",
        "chat_id": os.environ.get("CHAT_ID_POLICY"),
        "topics": "T10289",
        "exclude_terms": [
            "habermas", "adorno", "hegel", "kant's", "theology",
            "external action", "foreign policy",
            # EU-Exklusion (strikt, um Überlappungen mit dem EU-Kanal zu verhindern)
            "europe", "european", "brexit", "euro", "eurosceptic", "euroscepticism",
            "spinelli", "trilogues", "commission", "council of europe", "subsidiarity"
        ]
    },
    {
        "titel": "Europäische Union & Regionale Integration",
        "chat_id": os.environ.get("CHAT_ID_EU"),
        "topics": "T10289|T10053",
        "must_include": [
            "eu", "european union", "european commission", "european parliament",
            "council of the european", "integration", "europeanization", "brussels", "member state"
        ],
        "exclude_terms": ["epistemology", "theology"]
    },
    {
        "titel": "Politische Soziologie, Partizipation & Protest",
        "chat_id": os.environ.get("CHAT_ID_SOZIOLOGIE"),
        "topics": "T13135|T13982",
        "exclude_terms": [
            "econometric", "consumer", "firm performance", "taxation",
            "fiscal", "tax law", "tax evasion", "revenue extraction", "better regulation"
        ]
    }
]

# Vorab-Kompilierung von Regex-Mustern mit echten Wortgrenzen (verhindert "trial" in "industrial" etc.)
def erstelle_wortgrenzen_regex(begriffsliste):
    if not begriffsliste:
        return None
    muster = r"(?<!\w)(" + "|".join(re.escape(b) for b in begriffsliste) + r")(?!\w)"
    return re.compile(muster, re.IGNORECASE)

GLOBAL_REGEX = erstelle_wortgrenzen_regex(GLOBAL_AUSSCHLUSS)
EU_POLICY_SPERRE_REGEX = re.compile(r"(?<!\w)(eu|emu|europe|european|brexit|euro|eurosceptic|euroscepticism)(?!\w)", re.IGNORECASE)

NON_LATIN_REGEX = re.compile(r'[\u0E00-\u0E7F\u4E00-\u9FFF\u0400-\u04FF\u0600-\u06FF]')

def ist_valide_sprache(paper, titel_text):
    # Ausschluss nicht-lateinischer Schriften (Kyrillisch, Arabisch, Chinesisch, Thai)
    if NON_LATIN_REGEX.search(titel_text):
        return False
    # OpenAlex Sprach-Tag bevorzugen
    sprache_meta = paper.get("language")
    if sprache_meta in ["de", "en"]:
        return True
    # Fallback auf langdetect bei unklaren Metadaten
    try:
        return detect(titel_text) in ["de", "en"]
    except Exception:
        return False

# Echtes FIFO-Management für seen_ids.txt
def lade_gesehene_ids():
    if not os.path.exists(SEEN_FILE):
        return [], set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        zeilen = [line.strip() for line in f if line.strip()]
    # Beibehaltung der Einfügereihenfolge
    eindeutige_liste = list(dict.fromkeys(zeilen))
    return eindeutige_liste, set(eindeutige_liste)

def haenge_neue_ids_an(bestehende_liste, gesehene_menge, neue_ids):
    for item_id in neue_ids:
        if item_id not in gesehene_menge:
            bestehende_liste.append(item_id)
            gesehene_menge.add(item_id)
    # Strikter FIFO-Puffer: maximal 3000 Einträge
    if len(bestehende_liste) > 3000:
        bestehende_liste = bestehende_liste[-3000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for item_id in bestehende_liste:
            f.write(f"{item_id}\n")
    return bestehende_liste, gesehene_menge

# Robuste HTTP-Session mit Retry & Exponential Backoff
def erstelle_http_session():
    session = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=2,  # Wartezeiten: 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

HTTP_SESSION = erstelle_http_session()

def sende_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=15)
    if not res.ok:
        print(f"Telegram-Fehler bei Chat {chat_id}: Status {res.status_code} - Antwort: {res.text}")
    res.raise_for_status()

def ermittle_country_code(paper):
    authorships = paper.get("authorships", []) or []
    if not authorships:
        return "UNKNOWN"
    # Erstautor bevorzugen
    first_author = authorships[0]
    for inst in first_author.get("institutions", []) or []:
        cc = inst.get("country_code")
        if cc:
            return cc.upper()
    # Fallback auf weitere Autoren
    for aut in authorships[1:]:
        for inst in aut.get("institutions", []) or []:
            cc = inst.get("country_code")
            if cc:
                return cc.upper()
    return "UNKNOWN"

def ist_kandidat_gueltig(p, kanal, kanal_regex, must_include_regex, gesehene_menge, gesamt_neue_menge):
    p_id = p.get("id")
    titel_raw = (p.get("title") or "").strip()
    titel_lower = titel_raw.lower().rstrip(".")

    if not p_id or not titel_raw or len(titel_raw) < 15:
        return False
    if titel_lower in UNERWUENSCHTE_TITEL:
        return False
    if any(muster in titel_lower for muster in ["reply to", "response to", "responses to", "country report"]):
        return False

    # Strikter Ausschluss von EU-Themen in Public Policy
    if kanal["titel"] == "Public Policy & Verwaltungswissenschaft":
        if EU_POLICY_SPERRE_REGEX.search(titel_lower):
            return False

    # Disziplinübergreifender und kanalspezifischer Ausschluss mit Wortgrenzen
    if GLOBAL_REGEX.search(titel_lower):
        return False
    if kanal_regex and kanal_regex.search(titel_lower):
        return False

    # Pflichtbegriffe für den EU-Kanal (mit Wortgrenzen)
    if must_include_regex and not must_include_regex.search(titel_lower):
        return False

    if not ist_valide_sprache(p, titel_raw):
        return False

    if p_id in gesehene_menge or p_id in gesamt_neue_menge:
        return False

    return True

# Hauptausführung
gesehene_liste, gesehene_menge = lade_gesehene_ids()
gesamt_neue_menge = set()

heute = datetime.date.today()
datum_str = heute.strftime("%d.%m.%Y")
start_datum = (heute - datetime.timedelta(days=60)).strftime("%Y-%m-%d")

bereits_belieferte_chats = set()

headers = {
    "User-Agent": f"PoliSciDailyPoster/1.0 (mailto:{MAILTO})"
}

for kanal in KANAELE:
    print(f"\n--- Bearbeite Kanal: {kanal['titel']} ---")
    chat_id = kanal.get("chat_id")
    
    if not chat_id:
        print(f"Übersprungen: Kein Secret für '{kanal['titel']}'.")
        continue

    chat_id = str(chat_id).strip()
    if chat_id in bereits_belieferte_chats:
        print(f"Übersprungen: Chat-ID {chat_id} wurde in diesem Durchlauf bereits beliefert.")
        continue

    kanal_regex = erstelle_wortgrenzen_regex(kanal.get("exclude_terms", []))
    must_include_regex = erstelle_wortgrenzen_regex(kanal.get("must_include", []))

    filter_string = (
        f"type:article,"
        f"primary_location.source.type:journal,"
        f"is_paratext:false,"
        f"has_abstract:true,"
        f"language:de|en,"
        f"from_publication_date:{start_datum},"
        f"primary_topic.id:{kanal['topics']}"
    )

    params = {
        "filter": filter_string,
        "sort": "publication_date:desc",
        "per_page": 100,
        "mailto": MAILTO,
        # Schlankes Payload für schnellere Übertragung ohne Timeouts
        "select": "id,title,doi,primary_location,open_access,authorships,language,publication_date"
    }

    try:
        res = HTTP_SESSION.get("https://api.openalex.org/works", params=params, headers=headers, timeout=25)
        res.raise_for_status()
        roh_treffer = res.json().get("results", [])
        print(f"OpenAlex lieferte {len(roh_treffer)} Roh-Treffer für {kanal['titel']}")
        time.sleep(2)
    except Exception as e:
        print(f"Fehler bei OpenAlex für {kanal['titel']}: {e}")
        time.sleep(3)
        continue

    # Alle fachlich qualifizierten Treffer ermitteln (DRY-Prinzip)
    qualifizierte_treffer = [
        p for p in roh_treffer
        if ist_kandidat_gueltig(p, kanal, kanal_regex, must_include_regex, gesehene_menge, gesamt_neue_menge)
    ]

    ausgewaehlte_treffer = []
    journal_counter = {}
    region_counter = {}

    # Durchlauf 1: Mit Diversitätsquoten (max. 2 pro Journal, max. 3 pro Land)
    for p in qualifizierte_treffer:
        journal_id = (p.get("primary_location") or {}).get("source", {}).get("id")
        country = ermittle_country_code(p)

        if journal_id and journal_counter.get(journal_id, 0) >= 2:
            continue
        if country != "UNKNOWN" and region_counter.get(country, 0) >= 3:
            continue

        if journal_id:
            journal_counter[journal_id] = journal_counter.get(journal_id, 0) + 1
        if country != "UNKNOWN":
            region_counter[country] = region_counter.get(country, 0) + 1

        ausgewaehlte_treffer.append(p)
        if len(ausgewaehlte_treffer) == 10:
            break

    # Durchlauf 2 (Fallback): Auffüllen ohne Quoten, falls < 10
    if len(ausgewaehlte_treffer) < 10:
        bereits_gewaehlte_ids = {x.get("id") for x in ausgewaehlte_treffer}
        for p in qualifizierte_treffer:
            if p.get("id") not in bereits_gewaehlte_ids:
                ausgewaehlte_treffer.append(p)
                if len(ausgewaehlte_treffer) == 10:
                    break

    # Nachricht erstellen und versenden
    if ausgewaehlte_treffer:
        parts = [
            f"<b>📢 PoliSci Ticker: {html.escape(kanal['titel'])}</b>\n"
            f"<i>Ausgabe vom {datum_str} ({len(ausgewaehlte_treffer)} Papers):</i>\n"
        ]
        
        for idx, p in enumerate(ausgewaehlte_treffer, start=1):
            titel = html.escape(p.get("title") or "Ohne Titel")
            link = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
            ist_oa = p.get("open_access", {}).get("is_oa", False)
            oa_badge = "🟢 OA" if ist_oa else "🔒 Paywall"

            autoren_liste = [a["author"]["display_name"] for a in p.get("authorships", []) or []]
            autor_text = f"{autoren_liste[0]} et al." if len(autoren_liste) > 1 else (autoren_liste[0] if autoren_liste else "Unbekannt")

            # Wichtig: html.escape mit quote=True verhindert 400 Bad Request bei & in Links!
            zeile = f"<b>{idx}. {titel}</b>\n   ✍️ <i>{html.escape(autor_text)}</i> • {oa_badge}"
            if link:
                sicherer_link = html.escape(link, quote=True)
                zeile += f" • <a href='{sicherer_link}'>Link</a>"
            parts.append(zeile)

        parts.append(
            f"\n<i>🔍 Abstracts, Filter & Volltexte in der Web-App:</i>\n"
            f"👉 <a href='{APP_URL}'>PoliSci Newsticker öffnen</a>"
        )

        nachricht = "\n\n".join(parts)

        try:
            sende_telegram(chat_id, nachricht)
            bereits_belieferte_chats.add(chat_id)

            neue_ids_dieser_kanal = [p.get("id") for p in ausgewaehlte_treffer if p.get("id")]
            gesamt_neue_menge.update(neue_ids_dieser_kanal)

            # Sofortiges Speichern nach erfolgreichem Versand (Verhinderung von Datenverlust bei Teilabstürzen)
            gesehene_liste, gesehene_menge = haenge_neue_ids_an(
                gesehene_liste, gesehene_menge, neue_ids_dieser_kanal
            )

            print(f"OK: {len(ausgewaehlte_treffer)} Papers an '{kanal['titel']}' gesendet und IDs persistiert.")
            time.sleep(2)
        except Exception as e:
            print(f"Fehler beim Senden an {kanal['titel']}: {e}")
    else:
        print(f"Keine passenden Treffer für '{kanal['titel']}'.")

# Keep-Alive Ping für Streamlit Cloud
try:
    ping_res = requests.get(APP_URL, timeout=10)
    print(f"Keep-Alive-Ping an Streamlit gesendet (Status: {ping_res.status_code}).")
except Exception as e:
    print(f"Keep-Alive-Ping fehlgeschlagen: {e}")
