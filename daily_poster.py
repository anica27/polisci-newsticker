import datetime
import html
import os
import requests

# Zugangsdaten aus den GitHub Secrets
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def sende_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    res = requests.post(url, json=payload, timeout=10)
    res.raise_for_status()

# Publikationen der letzten 2 Tage abrufen
heute = datetime.date.today()
start = (heute - datetime.timedelta(days=2)).strftime("%Y-%m-%d")

params = {
    "filter": f"primary_location.source.type:journal,primary_topic.subfield.id:subfields/3320,is_paratext:false,from_publication_date:{start}",
    "sort": "publication_date:desc",
    "per_page": 3
}

res = requests.get("https://api.openalex.org/works", params=params, timeout=15)
treffer = res.json().get("results", [])

if treffer and BOT_TOKEN and CHAT_ID:
    parts = ["<b>📢 Täglicher PoliSci-Literaturüberblick:</b>\n"]
    for p in treffer:
        titel = html.escape(p.get("title") or "Ohne Titel")
        link = p.get("doi") or (p.get("primary_location") or {}).get("landing_page_url") or ""
        autoren = ", ".join([a["author"]["display_name"] for a in p.get("authorships", [])[:3]])
        
        block = f"• <b>{titel}</b>\n  <i>{html.escape(autoren)}</i>"
        if link:
            block += f"\n  🔗 <a href='{link}'>Volltext / Link</a>"
        parts.append(block)

    sende_telegram("\n\n".join(parts))