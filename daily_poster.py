import datetime
import html
import os
import re
import sys
import time
import requests
from langdetect import detect, DetectorFactory

# Deterministische Ergebnisse bei der Spracherkennung
DetectorFactory.seed = 0

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
APP_URL = "https://polisci-ticker.streamlit.app"
SEEN_FILE = "seen_ids.txt"

if not BOT_TOKEN:
    print("TELEGRAM_BOT_TOKEN fehlt.")
    sys.exit(0)

# Disziplinübergreifender Ausschluss: Naturwissenschaften, Medizin, BWL, Makroökonomie & Bankenmechanik
GLOBAL_AUSSCHLUSS = [
    # Medizin & Biologie
    "synaptic", "swallowing", "breathing", "diaphragm", "carotid",
    "striatum", "motor nucleus", "vagus", "pyroptotic", "sids",
    "respiratory", "pulmonary", "neuromuscular", "hypoglossal",
    "nurse", "nursing", "midwife", "midwifery", "prenatal", "clinical",
    "patient", "therapy", "cancer", "biomedical", "molecular", "cell",
    "hospital", "inpatient", "surgery", "disease", "pharmacology",
    # BWL & Marketing
    "marketing", "consumer", "msme", "smes", "supply chain", "firm performance",
    "stock market", "tourist", "hospitality", "hotel", "logistics",
    # Reine Makroökonomie, Wirtschaftsmathematik & Demographie
    "eigenequation", "sraffa", "capital-labour", "wage rate", "production function",
    "fertility rates", "monetary policy", "interest rate shock", "macroeconomic modeling",
    "investment operations", "syndicated loan", "commercial bank", "microfinance"
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
        # T10053: International Relations & Security, T11168: Foreign Policy Analysis
        "topics": "T10053|T11168",
        "exclude_terms": ["epistemology", "metaphysics", "formal logic", "household survey"]
    },
    {
        "titel": "Vergleichende Regierungslehre, Wahlen & Parteien",
        "chat_id": os.environ.get("CHAT_ID_VERGLEICH"),
        # T10108: Electoral Systems & Voting Behavior, T11397: Legislative Politics & Parliaments, T11742: Political Parties
        "topics": "T10108|T11397|T11742",
        "exclude_terms": ["metaphysics", "theology", "macroeconomics"]
    },
    {
        "titel": "Politische Theorie & Ideengeschichte",
        "chat_id": os.environ.get("CHAT_ID_THEORIE"),
        # T10718: Political Theory & Philosophy, T10582: History of Political Thought
        "topics": "T10718|T10582",
        "exclude_terms": ["econometric", "consumer", "accounting", "banking", "policy making", "regulatory framework"]
    },
    {
        "titel": "Public Policy & Verwaltungswissenschaft",
        "chat_id": os.environ.get("CHAT_ID_POLICY"),
        # T10289: Public Administration & Policy Implementation
        "topics": "T10289",
        "exclude_terms": ["habermas", "adorno", "hegel", "kant's", "theology", "european council", "global gateway", "external action", "foreign policy"]
    },
    {
        "titel": "Europäische Union & Regionale Integration",
        "chat_id": os.environ.get("CHAT_ID_EU"),
        # T10289 & Sub-Cluster fokussiert auf europäische Institutionen & Governance
        "topics": "T10289|T10053",
        "must_include": ["eu", "european union", "european commission", "european parliament", "council of the european", "integration", "europeanization", "brussels", "member state"],
        "exclude_terms": ["epistemology", "theology"]
    },
    {
        "titel": "Politische Soziologie, Partizipation & Protest",
        "chat_id": os.environ.get("CHAT_ID_SOZIOLOGIE"),
        # T13135, T13982, T11997 statt T13138!
        "topics": "T13135|T13982|T11997",
        "exclude_terms": [
            "econometric", "consumer", "firm performance", "taxation", 
            "fiscal", "tax law", "tax evasion", "revenue extraction", "better regulation"]
    }
]

def ist_deutsch_oder_englisch(text):
    if re.search(r'[\u0E00-\u0E7F\u4E00-\u9FFF\u0400-\u04FF\u0600-\u06FF]', text):
        return False
    try:
        sprache = detect(text)
        return sprache in ["de", "en"]
    except Exception:
        return False

def lade_gesehene_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def speichere_neue_ids(bestehende_ids, neue_ids):
    aktualisiert = list(bestehende_ids) + list(neue_ids)
    if len(aktualisiert) > 3000:
        aktualisiert = aktualisiert[-3000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for item_id in aktualisiert:
            f.write(f"{item_id}\n")

def sende_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=12)
    res.raise_for_status()

def ermittle_country_code(paper):
    for aut in paper.get("authorships", []) or []:
        for inst in aut.get("institutions", []) or []:
            cc = inst.get("country_code")
            if cc:
                return cc.upper()
    return "UNKNOWN"

gesehene_ids = lade_gesehene_ids()
gesamt_neue_ids = []

heute = datetime.date.today()
datum_str = heute.strftime("%d.%m.%Y")
start_datum = (heute - datetime.timedelta(days=60)).strftime("%Y-%m-%d")

bereits_belieferte_chats = set()

for kanal in KANAELE:
    chat_id = kanal.get("chat_id")
    if not chat_id:
        print(f"Übersprungen: Kein Secret für '{kanal['titel']}'.")
        continue

    chat_id = str(chat_id).strip()
    if chat_id in bereits_belieferte_chats:
        continue

    kanal_ausschluss = GLOBAL_AUSSCHLUSS + kanal.get("exclude_terms", [])
    must_include = kanal.get("must_include")

    filter_string = (
        f"type:article,"
        f"primary_location.source.type:journal,"
        f"is_paratext:false,"
        f"has_abstract:true,"
        f"language:de|en,"
        f"from_publication_date:{start_datum},"
        f"primary_topic.domain.id:2,"
        f"primary_topic.id:{kanal['topics']}"
    )

    params = {
        "filter": filter_string,
        "sort": "publication_date:desc",
        "per_page": 100,
        "mailto": "research-ticker@example.com"
    }

    try:
        res = requests.get("https://api.openalex.org/works", params=params, timeout=15)
        res.raise_for_status()
        roh_treffer = res.json().get("results", [])
    except Exception as e:
        print(f"Fehler bei OpenAlex für {kanal['titel']}: {e}")
        continue

    ausgewaehlte_treffer = []
    journal_counter = {}
    region_counter = {}

    # Durchlauf 1: Mit Quoten
    for p in roh_treffer:
        p_id = p.get("id")
        titel_raw = (p.get("title") or "").strip()
        titel_lower = titel_raw.lower().rstrip(".")
        journal_id = (p.get("primary_location") or {}).get("source", {}).get("id")
        country = ermittle_country_code(p)

        if not p_id or not titel_raw or len(titel_raw) < 15:
            continue
        if titel_lower in UNERWUENSCHTE_TITEL:
            continue
        if any(term in titel_lower for term in kanal_ausschluss):
            continue
        if must_include and not any(term in titel_lower for term in must_include):
            continue
        if not ist_deutsch_oder_englisch(titel_raw):
            continue
        if p_id in gesehene_ids or p_id in gesamt_neue_ids:
            continue

        if journal_id and journal_counter.get(journal_id, 0) >= 2:
            continue
        if country != "UNKNOWN" and region_counter.get(country, 0) >= 3:
            continue

        if journal_id:
            journal_counter[journal_id] = journal_counter.get(journal_id, 0) + 1
        if country != "UNKNOWN":
            region_counter[country] = region_counter.get(country, 0) + 1

        ausgewaehlte_treffer.append(p)
        gesamt_neue_ids.append(p_id)

        if len(ausgewaehlte_treffer) == 10:
            break

    # Durchlauf 2 (Fallback): Fehlende Plätze auffüllen
    if len(ausgewaehlte_treffer) < 10:
        for p in roh_treffer:
            p_id = p.get("id")
            titel_raw = (p.get("title") or "").strip()
            titel_lower = titel_raw.lower().rstrip(".")

            if not p_id or not titel_raw or len(titel_raw) < 15:
                continue
            if titel_lower in UNERWUENSCHTE_TITEL:
                continue
            if any(term in titel_lower for term in kanal_ausschluss):
                continue
            if must_include and not any(term in titel_lower for term in must_include):
                continue
            if not ist_deutsch_oder_englisch(titel_raw):
                continue
            if p_id in gesehene_ids or p_id in gesamt_neue_ids:
                continue

            ausgewaehlte_treffer.append(p)
            gesamt_neue_ids.append(p_id)

            if len(ausgewaehlte_treffer) == 10:
                break

   # Nachricht als sauberes HTML formatieren
    if ausgewaehlte_treffer:
        parts = [
            f"<b>📢 PoliSci Ticker: {kanal['titel']}</b>\n"
            f"<i>Ausgabe vom {datum_str} ({len(ausgewaehlte_treffer)} Papers):</i>\n"
        ]
        
        for idx, p in enumerate(ausgewaehlte_treffer, start=1):
            titel = html.escape(p.get("title") or "Ohne Titel")
            link = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
            ist_oa = p.get("open_access", {}).get("is_oa", False)
            oa_badge = "🟢 OA" if ist_oa else "🔒 Paywall"

            autoren_liste = [a["author"]["display_name"] for a in p.get("authorships", []) or []]
            autor_text = f"{autoren_liste[0]} et al." if len(autoren_liste) > 1 else (autoren_liste[0] if autoren_liste else "Unbekannt")

            zeile = f"<b>{idx}. {titel}</b>\n   ✍️ <i>{html.escape(autor_text)}</i> • {oa_badge}"
            if link:
                zeile += f" • <a href='{link}'>Link</a>"
            parts.append(zeile)

        parts.append(
            f"\n<i>🔍 Abstracts, Filter & Volltexte in der Web-App:</i>\n"
            f"👉 <a href='{APP_URL}'>PoliSci Newsticker öffnen</a>"
        )

        try:
            sende_telegram(chat_id, "\n\n".join(parts))
            bereits_belieferte_chats.add(chat_id)
            print(f"OK: {len(ausgewaehlte_treffer)} Papers an '{kanal['titel']}' gesendet.")
            time.sleep(2)
        except Exception as e:
            print(f"Fehler beim Senden an {kanal['titel']}: {e}")

if gesamt_neue_ids:
    speichere_neue_ids(gesehene_ids, gesamt_neue_ids)

try:
    requests.get(APP_URL, timeout=8)
except Exception:
    pass
