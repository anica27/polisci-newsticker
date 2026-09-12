import datetime
import html
import os
import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
APP_URL = "https://polisci-ticker.streamlit.app"  # Link zu deiner Web-App

def sende_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True  # Verhindert riesige Link-Vorschaukarten bei 10 Links
    }
    res = requests.post(url, json=payload, timeout=10)
    res.raise_for_status()

# Publikationen der letzten 3 Tage abrufen
heute = datetime.date.today()
start = (heute - datetime.timedelta(days=3)).strftime("%Y-%m-%d")

# Wir fordern 30 Treffer an, um sicher auf 10 bereinigte Titel zu kommen
params = {
    "filter": f"primary_location.source.type:journal,primary_topic.subfield.id:subfields/3320,is_paratext:false,from_publication_date:{start}",
    "sort": "publication_date:desc",
    "per_page": 30
}

res = requests.get("https://api.openalex.org/works", params=params, timeout=15)
roh_treffer = res.json().get("results", [])

# --- DUBLETTEN HERAUSFILTERN ---
bereinigte_treffer = []
gesehene_titel = set()

for p in roh_treffer:
    rohtitel = (p.get("title") or "").strip().lower()
    if not rohtitel or rohtitel in gesehene_titel:
        continue
    gesehene_titel.add(rohtitel)
    bereinigte_treffer.append(p)
    if len(bereinigte_treffer) == 10:  # Genau die Top 10
        break

# --- NACHRICHT FORMATIEREN & VERSENDEN ---
if bereinigte_treffer and BOT_TOKEN and CHAT_ID:
    datum_str = heute.strftime("%d.%m.%Y")
    parts = [f"<b>📢 PoliSci-Literaturüberblick ({datum_str})</b>\n<i>Die 10 neuesten politikwissenschaftlichen Publikationen:</i>\n"]
    
    for idx, p in enumerate(bereinigte_treffer, start=1):
        titel = html.escape(p.get("title") or "Ohne Titel")
        link = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
        
        # Autoren kompakt (maximal die ersten 2 Autoren)
        autoren_namen = [a["author"]["display_name"] for a in p.get("authorships", [])]
        if len(autoren_namen) > 2:
            autoren_text = f"{autoren_namen[0]} et al."
        elif autoren_namen:
            autoren_text = ", ".join(autoren_namen)
        else:
            autoren_text = "Unbekannt"

        block = f"<b>{idx}. {titel}</b>\n   ✍️ <i>{html.escape(autoren_text)}</i>"
        if link:
            block += f" • <a href='{link}'>Link</a>"
        parts.append(block)

    parts.append(f"\n🔍 <i>Alle Papers, Abstracts & Übersetzungen in der Web-App:</i>\n👉 <a href='{APP_URL}'>PoliSci Newsticker öffnen</a>")

    sende_telegram("\n\n".join(parts))
