import datetime
import html
import os
import re
import streamlit as st
import requests
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

st.set_page_config(
    page_title="PoliSci Newsticker",
    layout="wide"
)

# 1. Strikter globaler Ausschluss (Medizin, Naturwissenschaft, BWL & Konsumforschung)
GLOBAL_AUSSCHLUSS = [
    # Medizin & Biologie
    "synaptic", "swallowing", "breathing", "diaphragm", "carotid",
    "striatum", "motor nucleus", "vagus", "pyroptotic", "sids",
    "respiratory", "pulmonary", "neuromuscular", "hypoglossal",
    "nurse", "nursing", "midwife", "midwifery", "prenatal", "clinical",
    "patient", "therapy", "cancer", "biomedical", "molecular", "cell",
    "hospital", "inpatient", "surgery", "disease", "pharmacology",
    # BWL, Marketing & Konsumforschung
    "marketing", "consumer", "msme", "smes", "supply chain", "firm performance",
    "stock market", "tourist", "hospitality", "hotel", "logistics"
]

UNERWUENSCHTE_TITEL = {
    "conclusion", "conclusions", "introduction", "preface", "index",
    "contents", "editorial", "book reviews", "front matter", "back matter"
}

# 2. Synchronisierte Fachbereiche & Topics
FACHBEREICHE = {
    "Alle Teilbereiche": {
        "topics": "T10053|T11168|T10108|T11397|T11742|T10718|T13138|T10582|T11997|T10289",
        "exclude": []
    },
    "Internationale Beziehungen & Außenpolitik": {
        "topics": "T10053|T11168",
        "exclude": ["epistemology", "metaphysics", "formal logic"]
    },
    "Vergleichende Regierungslehre & Wahlsysteme": {
        "topics": "T10108|T11397|T11742",
        "exclude": ["metaphysics", "theology"]
    },
    "Politische Theorie & Ideengeschichte": {
        "topics": "T10718|T13138|T10582|T11997",
        "exclude": ["econometric", "consumer", "accounting", "banking"]
    },
    "Public Policy & Verwaltungswissenschaft": {
        "topics": "T10289",
        "exclude": ["habermas", "adorno", "hegel", "kant's", "theology"]
    }
}

def ist_deutsch_oder_englisch(text):
    if re.search(r'[\u0E00-\u0E7F\u4E00-\u9FFF\u0400-\u04FF\u0600-\u06FF]', text):
        return False
    try:
        sprache = detect(text)
        return sprache in ["de", "en"]
    except Exception:
        return False
        
# Header & Telegram-Hub-Hinweis
st.title("PoliSci Newsticker")
st.markdown(
    """
    tagesaktuelle Fachliteratur aus peer-reviewten Fachzeitschriften der Politikwissenschaft.
    
    => **Telegram-Kanalnetzwerk mit teilgebietsspezifischen Kanälen:** https://t.me/polisciticker
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

# OpenAlex Filter zusammenbauen
bereichs_daten = FACHBEREICHE[ausgewaehlter_bereich]
filter_regeln = [
    "type:article",
    "primary_location.source.type:journal",
    "is_paratext:false",
    "has_abstract:true",
    "language:de|en",
    f"from_publication_date:{start_datum}",
    "primary_topic.domain.id:2",  # Strikte Begrenzung auf Social Sciences
    f"primary_topic.id:{bereichs_daten['topics']}"
]

if nur_open_access:
    filter_regeln.append("is_oa:true")

params = {
    "filter": ",".join(filter_regeln),
    "sort": "publication_date:desc",
    "per_page": 50,
    "mailto": "research-ticker@example.com"
}

ausschluss_begriffe = GLOBAL_AUSSCHLUSS + bereichs_daten["exclude"]

# Daten abrufen
with st.spinner("Lade aktuelle Fachpublikationen aus OpenAlex..."):
    try:
        res = requests.get("https://api.openalex.org/works", params=params, timeout=15)
        res.raise_for_status()
        daten = res.json().get("results", [])
    except Exception as e:
        st.error(f"Fehler beim Abrufen der Publikationen: {e}")
        daten = []

# Manuelles Filtern gegen Titelausschlüsse und Rauschen
gefilterte_publikationen = []
for p in daten:
    titel = (p.get("title") or "").strip()
    titel_lower = titel.lower().rstrip(".")

    if not titel or len(titel) < 15:
        continue
    if titel_lower in UNERWUENSCHTE_TITEL:
        continue
    if any(begriff in titel_lower for begriff in ausschluss_begriffe):
        continue
    if not ist_deutsch_oder_englisch(titel):
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
        
        # Open Access Status
        ist_oa = p.get("open_access", {}).get("is_oa", False)
        oa_status = "🟢 Open Access" if ist_oa else "🔒 Paywall"

        # Quelle / Journal
        journal_name = (p.get("primary_location") or {}).get("source", {}).get("display_name") or "Fachzeitschrift nicht angegeben"

        # Topic
        topic_info = (p.get("primary_topic") or {}).get("display_name") or "Allgemein"

        # Autoren
        autoren = [a["author"]["display_name"] for a in p.get("authorships", [])]
        autoren_str = ", ".join(autoren) if autoren else "Keine Autorenangabe"

        # Abstract invertieren (OpenAlex Abstract Inverted Index)
        abstract_text = ""
        inv_index = p.get("abstract_inverted_index")
        if inv_index:
            wort_positionen = []
            for wort, pos_liste in inv_index.items():
                for pos in pos_liste:
                    wort_positionen.append((pos, wort))
            wort_positionen.sort()
            abstract_text = " ".join([w[1] for w in wort_positionen])

        # Card Rendering
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
