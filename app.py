import datetime
import streamlit as st
import requests
from translate import Translator

st.set_page_config(page_title="PoliSci Newsticker", page_icon="📚", layout="centered")

# --- TELEGRAM-ZUGANGSDATEN ---
TELEGRAM_BOT_TOKEN = st.secrets["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = "st.secrets["TELEGRAM_CHAT_ID"]

def sende_telegram_paper(eintrag):
    """Formatiert ein Paper und sendet es via Telegram Bot API."""
    if not BOT_TOKEN or not CHAT_ID or BOT_TOKEN.startswith("DEIN_"):
        return False, "Zugangsdaten fehlen."
    
    titel = eintrag.get("title", "Kein Titel")
    datum = eintrag.get("publication_date", "Datum unbekannt")
    autoren_liste = [a["author"]["display_name"] for a in eintrag.get("authorships", [])]
    autoren = ", ".join(autoren_liste[:3]) + (" et al." if len(autoren_liste) > 3 else "")
    
    ort = eintrag.get("primary_location") or {}
    journal = (ort.get("source") or {}).get("display_name", "Fachjournal")
    link = eintrag.get("doi") or ort.get("landing_page_url")
    oa_status = "🟢 Open Access" if eintrag.get("open_access", {}).get("is_oa") else "🔒 Paywall"

    text = (
        f"📚 <b>Neues politikwissenschaftliches Paper</b>\n\n"
        f"<b>{titel}</b>\n\n"
        f"✍️ <i>{autoren or 'Unbekannte Autor:innen'}</i>\n"
        f"📖 <i>{journal}</i> ({datum}) • {oa_status}"
    )
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    if link:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "Zum Volltext / Verlag ↗", "url": link}]]
        }

    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200, res.text
    except Exception as e:
        return False, str(e)

# --- CACHING- & HILFSFUNKTIONEN ---

@st.cache_data(show_spinner=False, ttl=3600)
def lade_daten_von_api(url, params):
    res = requests.get(url, params=params, timeout=12)
    res.raise_for_status()
    return res.json().get("results", [])

def rekonstruiere_abstract(inverted_index):
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
        return " ".join([tr.translate(b) for b in bloecke])
    except Exception:
        return "Übersetzung nicht verfügbar."

# --- OBERFLÄCHE ---

st.title("📚 PoliSci Newsticker")
st.caption("Internationale Fachpublikationen mit variablen Filtern & Telegram-Push.")

SACHGEBIETE = {
    "Alle Sachgebiete": None,
    "Internationale Beziehungen & Außenpolitik": "topics/T10053",
    "Vergleichende Regierungslehre & Wahlsysteme": "topics/T10108",
    "Politische Theorie & Ideengeschichte": "topics/T11532",
    "Verwaltungswissenschaft & Public Policy": "topics/T10289",
    "Europäische Union & Regionalintegration": "topics/T10294",
}

# --- SEITENLEISTE: FILTER ---
st.sidebar.header("🔍 Filteroptionen")
suchbegriff = st.sidebar.text_input("Schlagwortsuche:", placeholder="z. B. Wahlsystem, Populismus...")
gewaehltes_gebiet = st.sidebar.selectbox("Sachgebiet eingrenzen:", list(SACHGEBIETE.keys()))
tage_zurueck = st.sidebar.slider("Zeitraum (letzte X Tage):", min_value=7, max_value=60, value=30)
anzahl = st.sidebar.slider("Anzahl der Einträge:", min_value=3, max_value=20, value=5)

nur_oa = st.sidebar.checkbox("Nur Open Access (frei lesbar)")
nur_mit_abstract = st.sidebar.checkbox("Nur Papers mit Zusammenfassung anzeigen", value=True)

# Datumsfenster
heute = datetime.date.today().strftime("%Y-%m-%d")
start_datum = (datetime.date.today() - datetime.timedelta(days=tage_zurueck)).strftime("%Y-%m-%d")

# Parameter aufbauen
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
        st.error(f"Fehler beim Abruf: {e}")
        treffer = []

# --- TELEGRAM PUSH BEREICH ---
with st.sidebar:
    st.divider()
    st.subheader("📲 Aufs Smartphone")
    st.caption("Sendet die oben gefilterten Einträge sofort per Push an deinen Telegram-Bot.")
    
    if st.button("🚀 Gefilterte Paper an Telegram senden", disabled=len(treffer) == 0):
        erfolge = 0
        with st.spinner("Sende Benachrichtigungen..."):
            for eintrag in treffer:
                ok, _ = sende_telegram_paper(eintrag)
                if ok:
                    erfolge += 1
        if erfolge > 0:
            st.success(f"{erfolge} Paper erfolgreich aufs Handy geschickt!")
        else:
            st.error("Fehler beim Versand. Bitte Token und Chat-ID prüfen.")

# --- FEED-ANZEIGE ---
st.markdown(f"**Gefundene Veröffentlichungen:** {len(treffer)}")

if not treffer:
    st.warning("Keine Treffer im gewählten Zeitraum.")

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
        
        col1, col2 = st.columns([2, 1])
        with col1:
            if link:
                st.link_button("Zum Volltext / zur Verlagsseite ↗", link)
        with col2:
            if st.button("📲 Nur dieses Paper senden", key=f"tg_single_{idx}"):
                ok, _ = sende_telegram_paper(eintrag)
                if ok:
                    st.toast("Paper an Telegram geschickt!", icon="✅")
                else:
                    st.toast("Versand fehlgeschlagen.", icon="⚠️")
        
        abstract_text = rekonstruiere_abstract(eintrag.get("abstract_inverted_index"))
        if abstract_text:
            with st.expander("📖 Zusammenfassung lesen"):
                st.markdown("**Originaltext:**")
                st.write(abstract_text)
                if sprache != "DE":
                    st.divider()
                    if st.checkbox("Auf Deutsch übersetzen", key=f"trans_{idx}"):
                        with st.spinner("Übersetze..."):
                            dt_text = uebersetze_ins_deutsche(abstract_text)
                            st.info(dt_text)
        else:
            st.caption("ℹ️ Keine Zusammenfassung beim Verlag hinterlegt.")

        st.divider()
