import datetime
import html
import os
import re
import streamlit as st
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

st.set_page_config(
    page_title="PoliSci Newsticker",
    layout="wide"
)

MAILTO = os.environ.get("OPENALEX_MAILTO") or "polisciticker@example.org"

# 1. Strikter globaler Ausschluss mit Wortgrenzen
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

# 2. Exakt synchronisierte Fachbereiche & Topics
FACHBEREICHE = {
    "Alle Teilbereiche": {
        "topics": "T10053|T10108|T11397|T11742|T10582|T12752|T11447|T10289|T13135|T13982",
        "exclude_terms": []
    },
    "Internationale Beziehungen & Sicherheitspolitik": {
        "topics": "T10053",
        "exclude_terms": [
            "epistemology", "metaphysics", "formal logic", "household survey",
            "world bank", "surveillance review", "procurement", "visual essay",
            "fertility", "microfinance"
        ]
    },
    "Vergleichende Regierungslehre, Wahlen & Parteien": {
        "topics": "T10108|T11397|T11742",
        "exclude_terms": ["metaphysics", "theology", "macroeconomics"]
    },
    "Politische Theorie & Ideengeschichte": {
        "topics": "T10582|T12752|T11447",
        "exclude_terms": [
            "econometric", "consumer", "accounting", "banking", "policy making",
            "neonatal", "biobank", "clinical", "hiv", "general practice", "participants' altruism", "trial",
            "peacekeeping", "security council", "gaza", "united nations", "r2p", "asymmetric warfare", "foreign policy"
        ]
    },
    "Public Policy & Verwaltungswissenschaft": {
        "topics": "T10289",
        "exclude_terms": [
            "habermas", "adorno", "hegel", "kant's", "theology",
            "external action", "foreign policy",
            "europe", "european", "brexit", "euro", "eurosceptic", "euroscepticism",
            "spinelli", "trilogues", "commission", "council of europe", "subsidiarity"
        ]
    },
    "Europäische Union & Regionale Integration": {
        "topics": "T10289|T10053",
        "must_include": [
            "eu", "european union", "european commission", "european parliament",
            "council of the european", "integration", "europeanization", "brussels", "member state"
        ],
        "exclude_terms": ["epistemology", "theology"]
    },
    "Politische Soziologie, Partizipation & Protest": {
        "topics": "T13135|T13982",
        "exclude_terms": [
            "econometric", "consumer", "firm performance", "taxation",
            "fiscal", "tax law", "tax evasion", "revenue extraction", "better regulation"
        ]
    }
}

NON_LATIN_REGEX = re.compile(r'[\u0E00-\u0E7F\u4E00-\u9FFF\u0400-\u04FF\u0600-\u06FF]')
EU_POLICY_SPERRE_REGEX = re.compile(r"(?<!\w)(eu|emu|europe|european|brexit|euro|eurosceptic|euroscepticism)(?!\w)", re.IGNORECASE)

def erstelle_wortgrenzen_regex(begriffsliste):
    if not begriffsliste:
        return None
    muster = r"(?<!\w)(" + "|".join(re.escape(b) for b in begriffsliste) + r")(?!\w)"
    return re.compile(muster, re.IGNORECASE)

GLOBAL_REGEX = erstelle_wortgrenzen_regex(GLOBAL_AUSSCHLUSS)

def ist_valide_sprache(paper, titel_text):
    if NON_LATIN_REGEX.search(titel_text):
        return False
    sprache_meta = paper.get("language")
    if sprache_meta in ["de", "en"]:
        return True
    try:
        return detect(titel_text) in ["de", "en"]
    except Exception:
        return False

# Caching-Funktion für OpenAlex: Verhindert 429-Fehler bei UI-Interaktionen
@st.cache_data(ttl=1800, show_spinner=False)
def lade_openalex_daten(filter_string, mailto):
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))

    params = {
        "filter": filter_string,
        "sort": "publication_date:desc",
        "per_page": 200,
        "mailto": mailto
    }
    headers = {"User-Agent": f"PoliSciWebapp/1.0 (mailto:{mailto})"}
    res = session.get("https://api.openalex.org/works", params=params, headers=headers, timeout=20)
    res.raise_for_status()
    return res.json().get("results", [])

# Header
st.title("PoliSci Newsticker")
st.markdown(
    """
    Tagesaktuelle Fachliteratur aus peer-reviewten Fachzeitschriften der Politikwissenschaft.
    
    => **Telegram-Kanalnetzwerk mit fachspezifischen Kanälen:** https://t.me/polisciticker
    """
)

# Sidebar Filter
st.sidebar.header("Filter & Einstellungen")
ausgewaehlter_bereich = st.sidebar.selectbox("Teilbereich auswählen", list(FACHBEREICHE.keys()))
zeitraum_tage = st.sidebar.slider("Veröffentlichungszeitraum (letzte Tage)", min_value=7, max_value=90, value=30, step=7)
nur_open_access = st.sidebar.checkbox("Nur Open Access (frei zugänglich)", value=False)
suchbegriff = st.sidebar.text_input("Im Titel suchen (optional)").strip().lower()

heute = datetime.date.today()
start_datum = (heute - datetime.timedelta(days=zeitraum_tage)).strftime("%Y-%m-%d")

bereichs_daten = FACHBEREICHE[ausgewaehlter_bereich]
filter_regeln = [
    "type:article",
    "primary_location.source.type:journal",
    "is_paratext:false",
    "has_abstract:true",
    "language:de|en",
    f"from_publication_date:{start_datum}",
    f"primary_topic.id:{bereichs_daten['topics']}"
]

if nur_open_access:
    filter_regeln.append("is_oa:true")

kanal_regex = erstelle_wortgrenzen_regex(bereichs_daten.get("exclude_terms", []))
must_include_regex = erstelle_wortgrenzen_regex(bereichs_daten.get("must_include", []))

# Daten abrufen (mit Caching)
with st.spinner("Lade aktuelle Fachpublikationen aus OpenAlex..."):
    try:
        daten = lade_openalex_daten(",".join(filter_regeln), MAILTO)
    except Exception as e:
        st.error(f"Fehler beim Abrufen der Publikationen von OpenAlex: {e}")
        daten = []

# Filterung
gefilterte_publikationen = []
for p in daten:
    titel = (p.get("title") or "").strip()
    titel_lower = titel.lower().rstrip(".")

    if not titel or len(titel) < 15:
        continue
    if titel_lower in UNERWUENSCHTE_TITEL:
        continue
    if any(m in titel_lower for m in ["reply to", "response to", "responses to", "country report"]):
        continue

    # Public Policy Exklusion
    if ausgewaehlter_bereich == "Public Policy & Verwaltungswissenschaft":
        if EU_POLICY_SPERRE_REGEX.search(titel_lower):
            continue

    if GLOBAL_REGEX.search(titel_lower):
        continue
    if kanal_regex and kanal_regex.search(titel_lower):
        continue
    if must_include_regex and not must_include_regex.search(titel_lower):
        continue

    if not ist_valide_sprache(p, titel):
        continue
    if suchbegriff and suchbegriff not in titel_lower:
        continue

    gefilterte_publikationen.append(p)

# Ergebnisse anzeigen
st.subheader(f"Ergebnisse ({len(gefilterte_publikationen)} Publikationen gefunden)")

if not gefilterte_publikationen:
    st.info("Keine Publikationen gefunden, die den aktuellen Filterkriterien entsprechen. Erweitere eventuell den Zeitraum in der linken Leiste.")
else:
    for p in gefilterte_publikationen:
        titel = p.get("title") or "Ohne Titel"
        doi_url = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
        pub_datum = p.get("publication_date") or "Unbekannt"
        
        ist_oa = p.get("open_access", {}).get("is_oa", False)
        oa_status = "🟢 Open Access" if ist_oa else "🔒 Paywall"

        journal_name = (p.get("primary_location") or {}).get("source", {}).get("display_name") or "Fachzeitschrift nicht angegeben"
        topic_info = (p.get("primary_topic") or {}).get("display_name") or "Allgemein"

        autoren = [a["author"]["display_name"] for a in p.get("authorships", []) or []]
        autoren_str = ", ".join(autoren) if autoren else "Keine Autorenangabe"

        # Abstract invertieren
        abstract_text = ""
        inv_index = p.get("abstract_inverted_index")
        if inv_index:
            words = {}
            for word, positions in inv_index.items():
                for pos in positions:
                    words[pos] = word
            abstract_text = " ".join(words[pos] for pos in sorted(words))

        with st.container():
            st.markdown(f"### {titel}")
            st.caption(f"📌 **Thema:** {topic_info} • 📅 **Erschienen am:** {pub_datum} in *{journal_name}* • {oa_status}")
            st.markdown(f"**Autor:innen:** {autoren_str}")

            col1, col2 = st.columns([1, 4])
            with col1:
                if doi_url:
                    st.link_button("Zum Volltext / Verlag ↗", doi_url)
            
            if abstract_text:
                with st.expander("📖 Abstract anzeigen"):
                    st.write(abstract_text)

            st.divider()
