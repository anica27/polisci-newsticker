import datetime
import html
import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
APP_URL = "https://polisci-ticker.streamlit.app"
SEEN_FILE = "seen_ids.txt"

def lade_gesehene_ids():
    """Liest die bereits versendeten IDs aus der Textdatei ein."""
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def speichere_neue_ids(bestehende_ids, neue_ids):
    """Speichert die neuen IDs und begrenzt die Datei auf die letzten 500 Einträge."""
    aktualisierte_liste = list(bestehende_ids) + list(neue_ids)
    if len(aktualisierte_liste) > 500:
        aktualisierte_liste = aktualisierte_liste[-500:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for item_id in aktualisierte_liste:
            f.write(f"{item_id}\n")

def sende_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    res = requests.post(url, json=payload, timeout=10)
    res.raise_for_status()

# 1. Bisherige IDs laden
gesehene_ids = lade_gesehene_ids()

# 2. Publikationen der letzten 5 Tage abrufen
heute = datetime.date.today()
start = (heute - datetime.timedelta(days=5)).strftime("%Y-%m-%d")

params = {
    "filter": f"primary_location.source.type:journal,primary_topic.subfield.id:subfields/3320,is_paratext:false,from_publication_date:{start}",
    "sort": "publication_date:desc",
    "per_page": 50
}

res = requests.get("https://api.openalex.org/works", params=params, timeout=15)
roh_treffer = res.json().get("results", [])

# 3. Filtern: Weder ID noch Titel dürfen bisher vorgekommen sein
bereinigte_treffer = []
gesehene_titel_im_lauf = set()

for p in roh_treffer:
    p_id = p.get("id")
    rohtitel = (p.get("title") or "").strip().lower()

    if not p_id or not rohtitel:
        continue
    if p_id in gesehene_ids:
        continue
    if rohtitel in gesehene_titel_im_lauf:
        continue

    gesehene_titel_im_lauf.add(rohtitel)
    bereinigte_treffer.append(p)
    if len(bereinigte_treffer) == 10:
        break

# 4. Versenden und neue IDs wegschreiben
if bereinigte_treffer and BOT_TOKEN and CHAT_ID:
    datum_str = heute.strftime("%d.%m.%Y")
    parts = [f"<b>📢 PoliSci-Literaturüberblick ({datum_str})</b>\n<i>Die 10 neuesten politikwissenschaftlichen Publikationen:</i>\n"]
    
    versendete_ids = []
    for idx, p in enumerate(bereinigte_treffer, start=1):
        versendete_ids.append(p.get("id"))
        titel = html.escape(p.get("title") or "Ohne Titel")
        link = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
        
        # Open Access Status ermitteln
        ist_oa = p.get("open_access", {}).get("is_oa", False)
        status_badge = "🟢 Open Access" if ist_oa else "🔒 Paywall"

        autoren_namen = [a["author"]["display_name"] for a in p.get("authorships", [])]
        if len(autoren_namen) > 2:
            autoren_text = f"{autoren_namen[0]} et al."
        elif autoren_namen:
            autoren_text = ", ".join(autoren_namen)
        else:
            autoren_text = "Unbekannt"

        block = f"<b>{idx}. {titel}</b>\n   ✍️ <i>{html.escape(autoren_text)}</i>\n   {status_badge}"
        if link:
            block += f" • <a href='{link}'>Link</a>"
        parts.append(block)

    parts.append(f"\n🔍 <i>Alle Papers, Abstracts & Übersetzungen in der Web-App:</i>\n👉 <a href='{APP_URL}'>PoliSci Newsticker öffnen</a>")

    sende_telegram("\n\n".join(parts))
    speichere_neue_ids(gesehene_ids, versendete_ids)

# Streamlit-App aufwecken / wachhalten
try:
    requests.get(APP_URL, timeout=10)
except Exception:
    pass
