import datetime
import html
import os
import time
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
APP_URL = "https://polisci-ticker.streamlit.app"
SEEN_FILE = "seen_ids.txt"

# 1. Disziplinübergreifende Ausschlussbegriffe (in ALLEN Kanälen unerwünscht: Medizin & Naturwissenschaften)
GLOBAL_AUSSCHLUSS = [
    "synaptic", "swallowing", "breathing", "diaphragm", "carotid",
    "striatum", "motor nucleus", "vagus", "pyroptotic", "sids",
    "respiratory", "pulmonary", "neuromuscular", "hypoglossal",
    "nurse", "nursing", "midwife", "midwifery", "prenatal", "clinical",
    "patient", "therapy", "cancer", "biomedical", "molecular", "cell"
]

UNERWUENSCHTE_TITEL = {
    "conclusion", "conclusions", "introduction", "preface", "index",
    "contents", "editorial", "book reviews", "front matter", "back matter"
}

# 2. Fachkanäle mit individuellen Topic-IDs und kanalspezifischen Ausschlüssen
KANAELE = [
    {
        "titel": "Internationale Beziehungen & Außenpolitik",
        "chat_id": os.environ.get("CHAT_ID_IB"),
        "topics": "T10053|T11168",
        "ziel_anzahl": 10,
        "exclude_terms": [
            "formal logic", "epistemology", "epistemic", "ontology", 
            "truth conditional", "metaphysics"
        ]
    },
    {
        "titel": "Vergleichende Regierungslehre & Wahlsysteme",
        "chat_id": os.environ.get("CHAT_ID_VERGLEICH"),
        "topics": "T10108|T11397|T11742",
        "ziel_anzahl": 10,
        "exclude_terms": [
            "epistemology", "metaphysics"
        ]
    },
    {
        "titel": "Politische Theorie & Ideengeschichte",
        "chat_id": os.environ.get("CHAT_ID_THEORIE"),
        "topics": "T10718|T13138|T11997",
        "ziel_anzahl": 10,
        # Hier sind Habermas, Adorno, Hegel, Kant ausdrücklich ERLAUBT!
        "exclude_terms": [
            "econometric", "firm performance", "stock return", "supply chain"
        ]
    },
    {
        "titel": "Public Policy & Verwaltungswissenschaft",
        "chat_id": os.environ.get("CHAT_ID_POLICY"),
        "topics": "T10289",
        "ziel_anzahl": 10,
        # Hält reine Philosophie- und Ideengeschichte-Texte aus der Verwaltungspraxis fern
        "exclude_terms": [
            "habermas", "adorno", "hegel", "kant's", "kantian", 
            "ludwig von mises", "ideology critique", "metaphysics", "theology"
        ]
    }
]

def lade_gesehene_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def speichere_neue_ids(bestehende_ids, neue_ids):
    aktualisiert = list(bestehende_ids) + list(neue_ids)
    if len(aktualisiert) > 2000:
        aktualisiert = aktualisiert[-2000:]
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
    res = requests.post(url, json=payload, timeout=10)
    res.raise_for_status()

def ermittle_country_code(paper):
    authorships = paper.get("authorships", [])
    for aut in authorships:
        for inst in aut.get("institutions", []):
            cc = inst.get("country_code")
            if cc:
                return cc.upper()
    return "UNKNOWN"

gesehene_ids = lade_gesehene_ids()
gesamt_neue_ids = []

heute = datetime.date.today()
datum_str = heute.strftime("%d.%m.%Y")
start = (heute - datetime.timedelta(days=30)).strftime("%Y-%m-%d")

bereits_belieferte_chats = set()

for kanal in KANAELE:
    chat_id = kanal.get("chat_id")
    if not chat_id or not BOT_TOKEN:
        print(f"Übersprungen: Kein Secret für '{kanal['titel']}'.")
        continue

    chat_id = str(chat_id).strip()
    if chat_id in bereits_belieferte_chats:
        continue

    # Kombinierte Ausschlussliste speziell für diesen Kanal
    kanal_ausschluss = GLOBAL_AUSSCHLUSS + kanal.get("exclude_terms", [])

    # Filter mit harter Domänen-Sperre (domain.id:2 = Social Sciences)
    filter_string = (
        f"type:article,"
        f"primary_location.source.type:journal,"
        f"is_paratext:false,"
        f"has_abstract:true,"
        f"language:de|en,"
        f"from_publication_date:{start},"
        f"primary_topic.domain.id:2,"
        f"primary_topic.id:{kanal['topics']}"
    )

    params = {
        "filter": filter_string,
        "sort": "publication_date:desc",
        "per_page": 100
    }

    try:
        res = requests.get("https://api.openalex.org/works", params=params, timeout=15)
        res.raise_for_status()
        roh_treffer = res.json().get("results", [])
    except Exception as e:
        print(f"Fehler bei OpenAlex-Abfrage für {kanal['titel']}: {e}")
        continue

    bereinigte_treffer = []
    journal_counter = {}
    region_counter = {}

    for p in roh_treffer:
        p_id = p.get("id")
        titel_raw = (p.get("title") or "").strip()
        titel_lower = titel_raw.lower().rstrip(".")
        journal_id = (p.get("primary_location") or {}).get("source", {}).get("id")
        country = ermittle_country_code(p)

        if not p_id or not titel_raw:
            continue
        if titel_lower in UNERWUENSCHTE_TITEL or len(titel_raw) < 15:
            continue
        # Prüft sowohl globale als auch kanalspezifische Ausschlüsse
        if any(term in titel_lower for term in kanal_ausschluss):
            continue
        if p_id in gesehene_ids or p_id in gesamt_neue_ids:
            continue

        # Journal- und Regions-Bremse (max. 2 pro Journal, max. 2 pro Land)
        if journal_id:
            if journal_counter.get(journal_id, 0) >= 2:
                continue
        if country != "UNKNOWN":
            if region_counter.get(country, 0) >= 2:
                continue

        if journal_id:
            journal_counter[journal_id] = journal_counter.get(journal_id, 0) + 1
        if country != "UNKNOWN":
            region_counter[country] = region_counter.get(country, 0) + 1

        bereinigte_treffer.append(p)
        gesamt_neue_ids.append(p_id)

        if len(bereinigte_treffer) == kanal["ziel_anzahl"]:
            break

    # Falls durch die Limits noch keine 10 voll sind: Auffüllen aus passenden Treffern
    if len(bereinigte_treffer) < kanal["ziel_anzahl"]:
        for p in roh_treffer:
            p_id = p.get("id")
            titel_raw = (p.get("title") or "").strip()
            titel_lower = titel_raw.lower().rstrip(".")

            if not p_id or not titel_raw or p_id in gesehene_ids or p_id in gesamt_neue_ids:
                continue
            if titel_lower in UNERWUENSCHTE_TITEL or len(titel_raw) < 15:
                continue
            if any(term in titel_lower for term in kanal_ausschluss):
                continue

            bereinigte_treffer.append(p)
            gesamt_neue_ids.append(p_id)
            if len(bereinigte_treffer) == kanal["ziel_anzahl"]:
                break

    if bereinigte_treffer:
        parts = [f"<b>📢 PoliSci Ticker: {kanal['titel']}</b>\n<i>Ausgabe vom {datum_str} ({len(bereinigte_treffer)} Papers):</i>\n"]
        
        for idx, p in enumerate(bereinigte_treffer, start=1):
            titel = html.escape(p.get("title") or "Ohne Titel")
            link = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
            ist_oa = p.get("open_access", {}).get("is_oa", False)
            oa_badge = "🟢 OA" if ist_oa else "🔒 Paywall"

            autoren_liste = [a["author"]["display_name"] for a in p.get("authorships", [])]
            autor_text = f"{autoren_liste[0]} et al." if len(autoren_liste) > 1 else (autoren_liste[0] if autoren_liste else "Unbekannt")

            zeile = f"<b>{idx}. {titel}</b>\n   ✍️ <i>{html.escape(autor_text)}</i> • {oa_badge}"
            if link:
                zeile += f" • <a href='{link}'>Link</a>"
            parts.append(zeile)

        parts.append(f"\n🔍 <i>Abstracts, Filter & Übersetzungen in der Web-App:</i>\n👉 <a href='{APP_URL}'>PoliSci Newsticker öffnen</a>")

        try:
            sende_telegram(chat_id, "\n\n".join(parts))
            bereits_belieferte_chats.add(chat_id)
            print(f"Erfolgreich {len(bereinigte_treffer)} Papers an '{kanal['titel']}' gesendet.")
            time.sleep(2)
        except Exception as e:
            print(f"Fehler beim Senden an {kanal['titel']}: {e}")
    else:
        print(f"Keine Treffer für '{kanal['titel']}' ermittelt.")

if gesamt_neue_ids:
    speichere_neue_ids(gesehene_ids, gesamt_neue_ids)

try:
    requests.get(APP_URL, timeout=10)
except Exception:
    pass
