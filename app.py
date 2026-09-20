import datetime
import streamlit as st
import requests
from translate import Translator

st.set_page_config(page_title="PoliSci Newsticker", page_icon="📚", layout="centered")

# --- HILFSFUNKTIONEN (CACHING & TEXTVERARBEITUNG) ---

@st.cache_data(show_spinner=False, ttl=3600)
def lade_daten_von_api(url, params):
    """Holt Daten von OpenAlex und cacht das Ergebnis für 1 Stunde."""
    res = requests.get(url, params=params, timeout=12)
    res.raise_for_status()
    return res.json().get("results", [])

def rekonstruiere_abstract(inverted_index):
    """Rekonstruiert den Fließtext aus dem Inverted Index von OpenAlex."""
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
    """Übersetzt das Abstract blockweise ins Deutsche."""
    if not text:
        return ""
    try:
        tr = Translator(from_lang="en", to_lang="de")
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
st.caption("Aktuelle internationale politikwissenschaftliche Veröffentlichungen.")

# --- SEITENLEISTE (HARMONISIERTE SACHGEBIETE) ---

# Exakt synchron zu den geprüften Topics aus daily_poster.py
SACHGEBIETE = {
    "Alle Teilbereiche (Politikwissenschaft)": None,
    "Internationale Beziehungen & Außenpolitik": "T10053|T11168",
    "Vergleichende Regierungslehre & Wahlsysteme": "T10108|T11397|T11742",
    "Politische Theorie & Ideengeschichte": "T10718|T13138|T11997",
    "Public Policy & Verwaltungswissenschaft": "T10289",
}

st.sidebar.header("🔍 Filteroptionen")
suchbegriff = st.sidebar.text_input("Schlagwortsuche:", placeholder="z. B. Populismus, Koalition, Sanctions...")
gewaehltes_gebiet = st.sidebar.selectbox("Sachgebiet eingrenzen:", list(SACHGEBIETE.keys()))
tage_zurueck = st.sidebar.slider("Zeitraum (letzte X Tage):", min_value=7, max_value=60, value=30)
anzahl = st.sidebar.slider("Anzahl der Einträge:", min_value=5, max_value=30, value=10)

nur_oa = st.sidebar.checkbox("Nur Open Access (frei lesbar)")
nur_mit_abstract = st.sidebar.checkbox("Nur Papers mit Zusammenfassung anzeigen", value=True)

# Datumsfenster berechnen
heute = datetime.date.today().strftime("%Y-%m-%d")
start_datum = (datetime.date.today() - datetime.timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")

# Strikte disziplinäre Filterregeln
filter_regeln = [
    "type:article",
    "primary_location.source.type:journal",
    "language:de|en",
    "is_paratext:false",
    "primary_topic.domain.id:2",  # Schließt Naturwissenschaften und Medizin aus
    f"from_publication_date:{start_datum}",
    f"to_publication_date:{heute}",
]

if nur_mit_abstract:
    filter_regeln.append("has_abstract:true")

spezifische_topics = SACHGEBIETE[gewaehltes_gebiet]
if spezifische_topics:
    filter_regeln.append(f"primary_topic.id:{spezifische_topics}")
else:
    # Übergeordnetes Fachgebiet für Soziologie & Politikwissenschaft
    filter_regeln.append("primary_topic.subfield.id:3312")

if nur_oa:
    filter_regeln.append("is_oa:true")

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
            st.info(f"🔎 Suche kombiniert (DE + EN): **{orig}** | **{trans}**")
        else:
            query_params["search"] = f'"{orig}"'
    except Exception:
        query_params["search"] = f'"{orig}"'

# --- DATENABRUF ---

with st.spinner("Lade Publikationen..."):
    try:
        treffer = lade_daten_von_api("https://api.openalex.org/works", query_params)
    except Exception as e:
        st.error(f"Fehler beim Laden der Publikationen: {e}")
        treffer = []

st.markdown(f"**Gefundene Veröffentlichungen:** {len(treffer)}")

if not treffer:
    st.warning("Keine Treffer im gewählten Zeitraum. Erweitere das Zeitfenster oder passe den Suchbegriff an.")

# --- FEED ANZEIGEN ---

for idx, eintrag in enumerate(treffer):
    titel = eintrag.get("title") or "Kein Titel"
    datum = eintrag.get("publication_date") or "Unbekannt"
    sprache = eintrag.get("language", "en").upper()
    topic_name = (eintrag.get("primary_topic") or {}).get("display_name", "Politikwissenschaft")
    
    autoren_liste = [a["author"]["display_name"] for a in eintrag.get("authorships", [])]
    autoren = ", ".join(autoren_liste) if autoren_liste else "Unbekannte Autor:innen"
    
    ort = eintrag.get("primary_location") or {}
    journal = (ort.get("source") or {}).get("display_name", "Fachzeitschrift")
    link = eintrag.get("doi") or ort.get("landing_page_url")
    oa_badge = "🟢 Open Access" if eintrag.get("open_access", {}).get("is_oa", False) else "🔒 Paywall"

    abstract_text = rekonstruiere_abstract(eintrag.get("abstract_inverted_index"))

    with st.container():
        st.markdown(f"### {titel}")
        st.caption(f"📌 {topic_name} • 🌐 {sprache} • {oa_badge}")
        st.markdown(f"**Autor:innen:** {autoren}")
        st.markdown(f"**Erschienen am:** `{datum}` in *{journal}*")
        
        # Nur noch der Verlags-/Volltextlink (kein Telegram-Button mehr)
        if link:
            st.link_button("Zum Volltext / Verlag ↗", link)
        else:
            st.caption("Kein Direktlink vorhanden.")

        if abstract_text:
            with st.expander("📖 Zusammenfassung lesen"):
                st.markdown("**Originaltext:**")
                st.write(abstract_text)
                
                if sprache != "DE":
                    st.divider()
                    if st.checkbox("Auf Deutsch übersetzen", key=f"trans_{idx}"):
                        with st.spinner("Übersetze..."):
                            dt_text = uebersetze_ins_deutsche(abstract_text)
                            st.markdown("**Deutsche Fassung:**")
                            st.info(dt_text)
        else:
            st.caption("ℹ️ Keine Zusammenfassung beim Verlag hinterlegt.")

        st.divider()
