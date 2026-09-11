import datetime
import streamlit as st
import requests
from translate import Translator

st.set_page_config(page_title="PoliSci Newsticker", page_icon="📚", layout="centered")

# --- HILFSFUNKTIONEN MIT CACHING (Verhindert Ruckeln und ständiges Neuladen) ---

@st.cache_data(show_spinner=False, ttl=3600)
def lade_daten_von_api(url, params):
    """Holt Daten von OpenAlex und merkt sich das Ergebnis für 1 Stunde."""
    res = requests.get(url, params=params, timeout=12)
    res.raise_for_status()
    return res.json().get("results", [])

def rekonstruiere_abstract(inverted_index):
    """Rekonstruiert den Fließtext aus dem OpenAlex Inverted Index."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return None
    wort_positionen = []
    for wort, positionen in inverted_index.items():
        for pos in positionen:
            wort_positionen.append((pos, wort))
    wort_positionen.sort(key=lambda x: x[0])
    return " ".join([wort for _, wort in wort_positionen])

@st.cache_data(show_spinner=False)
def uebersetze_ins_deutsche(text):
    """Übersetzt das Abstract sauber und speichert das Resultat im Cache."""
    if not text:
        return ""
    try:
        tr = Translator(from_lang="en", to_lang="de")
        # Zerlegen nach Sätzen/Sinnabschnitten bis 450 Zeichen
        saetze = text.split(". ")
        bloecke, aktueller_block = [], ""
        for satz in saetze:
            if len(aktueller_block) + len(satz) < 400:
                aktueller_block += satz + ". "
            else:
                bloecke.append(aktueller_block)
                aktueller_block = satz + ". "
        if aktueller_block:
            bloecke.append(aktueller_block)

        uebersetzte_bloecke = [tr.translate(b) for b in bloecke]
        return " ".join(uebersetzte_bloecke)
    except Exception:
        return "Übersetzung konnte nicht geladen werden. Bitte das englische Original lesen."

# --- HAUPTSEITE ---

st.title("📚 PoliSci Newsticker")
st.caption("Aktuelle internationale politikwissenschaftliche Veröffentlichungen mit Abstract-Vorschau.")

# --- SEITENLEISTE (FILTER) ---
SACHGEBIETE = {
    "Alle Sachgebiete": None,
    "Internationale Beziehungen & Außenpolitik": "topics/T10053",
    "Vergleichende Regierungslehre & Wahlsysteme": "topics/T10108",
    "Politische Theorie & Ideengeschichte": "topics/T11532",
    "Verwaltungswissenschaft & Public Policy": "topics/T10289",
    "Europäische Union & Regionalintegration": "topics/T10294",
}

st.sidebar.header("🔍 Filteroptionen")
suchbegriff = st.sidebar.text_input("Schlagwortsuche:", placeholder="z. B. Wahlsystem, Populismus...")
gewaehltes_gebiet = st.sidebar.selectbox("Sachgebiet eingrenzen:", list(SACHGEBIETE.keys()))
tage_zurueck = st.sidebar.slider("Zeitraum (letzte X Tage):", min_value=7, max_value=60, value=30)
anzahl = st.sidebar.slider("Anzahl der Einträge:", min_value=5, max_value=25, value=10)

nur_oa = st.sidebar.checkbox("Nur Open Access (frei lesbar)")
nur_mit_abstract = st.sidebar.checkbox("Nur Papers mit Zusammenfassung anzeigen", value=True)

# Datumsfenster berechnen
heute = datetime.date.today().strftime("%Y-%m-%d")
start_datum = (datetime.date.today() - datetime.timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")

# Filter-Parameter für API
filter_regeln = [
    "primary_location.source.type:journal",
    "language:de|en",
    "is_paratext:false",
    f"from_publication_date:{start_datum}",
    f"to_publication_date:{heute}",
]

if nur_mit_abstract:
    filter_regeln.append("has_abstract:true")

spezifische_topic_id = SACHGEBIETE[gewaehltes_gebiet]
if spezifische_topic_id:
    filter_regeln.append(f"primary_topic.id:{spezifische_topic_id}")
else:
    filter_regeln.append("primary_topic.subfield.id:subfields/3320")

if nur_oa:
    filter_regeln.append("is_oa:true")

# Suchanfrage aufbauen
query_params = {
    "filter": ",".join(filter_regeln),
    "sort": "publication_date:desc",
    "per_page": anzahl
}

if suchbegriff.strip():
    orig = suchbegriff.strip()
    try:
        tr_suche = Translator(from_lang="de", to_lang="en")
        trans = tr_suche.translate(orig)
        if trans and "error" not in trans.lower() and trans.lower() != orig.lower():
            query_params["search"] = f'"{orig}" OR "{trans}"'
            st.info(f"🔎 Suche kombiniert: **{orig}** + **{trans}**")
        else:
            query_params["search"] = f'"{orig}"'
    except Exception:
        query_params["search"] = f'"{orig}"'

# --- DATEN ABRUFEN ---
with st.spinner("Lade Publikationen..."):
    try:
        treffer = lade_daten_von_api("https://api.openalex.org/works", query_params)
    except Exception as e:
        st.error(f"Fehler beim Laden der Publikationen: {e}")
        treffer = []

st.markdown(f"**Gefundene Veröffentlichungen:** {len(treffer)}")

if not treffer:
    st.warning("Keine Treffer im gewählten Zeitraum. Probiere einen größeren Zeitraum oder ein anderes Schlagwort.")

# --- FEED ANZEIGEN ---
for idx, eintrag in enumerate(treffer):
    titel = eintrag.get("title", "Kein Titel")
    datum = eintrag.get("publication_date", "Unbekannt")
    sprache = eintrag.get("language", "en").upper()
    topic_name = (eintrag.get("primary_topic") or {}).get("display_name", "Politikwissenschaft")
    
    autoren_liste = [a["author"]["display_name"] for a in eintrag.get("authorships", [])]
    autoren = ", ".join(autoren_liste) if autoren_liste else "Unbekannte Autor:innen"
    
    ort = eintrag.get("primary_location") or {}
    journal = (ort.get("source") or {}).get("display_name", "Fachzeitschrift")
    link = eintrag.get("doi") or ort.get("landing_page_url")
    oa_badge = "🟢 Open Access" if eintrag.get("open_access", {}).get("is_oa", False) else "🔒 Paywall"

    with st.container():
        st.markdown(f"### {titel}")
        st.caption(f"📌 {topic_name} • 🌐 {sprache} • {oa_badge}")
        st.markdown(f"**Autor:innen:** {autoren}")
        st.markdown(f"**Erschienen am:** `{datum}` in *{journal}*")
        
        # Link zur Verlagsseite
        if link:
            st.link_button("Zum Volltext / zur Verlagsseite ↗", link)
        
        # Zusammenfassung direkt unter dem Paper zum Aufklappen
        abstract_text = rekonstruiere_abstract(eintrag.get("abstract_inverted_index"))
        if abstract_text:
            with st.expander("📖 Zusammenfassung lesen"):
                st.markdown("**Originaltext:**")
                st.write(abstract_text)
                
                # Nur übersetzen, wenn der Text auf Englisch ist
                if sprache != "DE":
                    st.divider()
                    if st.checkbox("Auf Deutsch übersetzen", key=f"trans_{idx}"):
                        with st.spinner("Übersetze..."):
                            dt_text = uebersetze_ins_deutsche(abstract_text)
                            st.markdown("**Deutsche Fassung (automatisch generiert):**")
                            st.info(dt_text)
        else:
            st.caption("ℹ️ Keine Zusammenfassung beim Verlag hinterlegt.")

        st.divider()