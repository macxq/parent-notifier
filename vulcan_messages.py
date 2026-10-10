#!/usr/bin/env python3
"""
eduVULCAN - Notyfikator wiadomości z obsługą pamięci sesji (Playwright storage_state).
Dzięki buforowaniu sesji sprawdzanie trwa ~0.5 sekundy (bez powtórnego logowania i bez Captchy).
Gdy sesja po kilku godzinach wygaśnie, skrypt automatycznie odświeża ją przez pełne logowanie.
"""
import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"

def load_dotenv():
    if ENV_PATH.exists():
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("VULCAN_USER", "USER", "LOGIN"):
                        os.environ.setdefault("VULCAN_USER", v)
                    elif k in ("VULCAN_PASSWORD", "PASSWORD", "PASS", "HASLO"):
                        os.environ.setdefault("VULCAN_PASSWORD", v)
                    elif k in ("SMTP_USER", "GMAIL_USER", "MAIL_USER"):
                        os.environ.setdefault("SMTP_USER", v)
                    elif k in ("SMTP_PASSWORD", "SMTP_PASS", "GMAIL_PASSWORD", "GMAIL_PASS"):
                        os.environ.setdefault("SMTP_PASSWORD", v)
                    elif k in ("EMAIL_TO", "MAIL_TO", "RECIPIENTS"):
                        os.environ.setdefault("EMAIL_TO", v)
                    else:
                        os.environ.setdefault(k, v)

load_dotenv()

# Katalog na dane i stan aplikacji (np. montowany wolumen Docker /app/data)
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data" if (BASE_DIR / "data").is_dir() else BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def resolve_data_file(filename: str) -> Path:
    """Zwraca ścieżkę do pliku w DATA_DIR z automatyczną migracją z BASE_DIR."""
    target = DATA_DIR / filename
    legacy = BASE_DIR / filename
    if not target.exists() and legacy.exists() and DATA_DIR != BASE_DIR:
        try:
            import shutil
            shutil.copy2(legacy, target)
        except Exception:
            return legacy
    return target

SEEN_FILE = resolve_data_file("seen_messages.json")
STORAGE_STATE_FILE = resolve_data_file("storage_state.json")
STUDENTS_CACHE_FILE = resolve_data_file("students_cache.json")
CALENDAR_EVENTS_FILE = resolve_data_file("added_calendar_events.json")

def get_service_account_path() -> Path:
    """Zwraca ścieżkę do klucza Service Account, sprawdzając DATA_DIR oraz BASE_DIR."""
    sa_env = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    candidates = []
    if sa_env:
        candidates.extend([Path(sa_env), DATA_DIR / sa_env, BASE_DIR / sa_env])
    candidates.extend([
        DATA_DIR / "service_account.json",
        BASE_DIR / "service_account.json"
    ])
    for c in candidates:
        if c.exists() and c.is_file():
            return c
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

def load_seen():
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, ensure_ascii=False, indent=2)

def load_added_calendar_events():
    if CALENDAR_EVENTS_FILE.exists():
        try:
            with open(CALENDAR_EVENTS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_added_calendar_events(events_set):
    with open(CALENDAR_EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(events_set), f, ensure_ascii=False, indent=2)

def load_cached_students():
    if STUDENTS_CACHE_FILE.exists():
        try:
            with open(STUDENTS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_cached_students(students):
    with open(STUDENTS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(students, f, ensure_ascii=False, indent=2)

def clean_html(raw_html):
    if not raw_html:
        return ""
    # Odkodowanie encji HTML (np. &nbsp;, &amp;, polskie znaki)
    text = html.unescape(raw_html)
    text = text.replace("\u00a0", " ")
    # Normalizacja tagów podziału linii
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|h[1-6]|li)>", "\n", text, flags=re.IGNORECASE)
    # Usunięcie pozostałych tagów HTML
    text = re.sub(r"<[^>]+>", "", text)
    # Przycięcie białych znaków i spacji z każdej pojedynczej linii
    lines = [line.strip() for line in text.splitlines()]
    cleaned_text = "\n".join(lines)
    # Redukcja wielokrotnych pustych linii (maks. 1 pusta linia odstępu między akapitami)
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
    return cleaned_text.strip()

def add_event_to_calendar(event_data):
    """Wysyła wydarzenie bezpośrednio do Google Calendar API z natywnym powiadomieniem (dzwoneczek na telefonie)."""
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    sa_file = get_service_account_path()
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not calendar_id:
        return False, "Brak GOOGLE_CALENDAR_ID w .env"

    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests
    except ImportError:
        return False, "Brak biblioteki google-auth (zainstaluj: pip install google-auth)"

    try:
        scopes = ["https://www.googleapis.com/auth/calendar"]
        if sa_json:
            sa_info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
        elif sa_file.exists() and sa_file.is_file():
            creds = service_account.Credentials.from_service_account_file(str(sa_file), scopes=scopes)
        else:
            return False, f"Nie znaleziono pliku klucza Service Account ({sa_file}) ani zmiennej GOOGLE_SERVICE_ACCOUNT_JSON"

        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
        access_token = creds.token
    except Exception as e:
        return False, f"Błąd autoryzacji Google Service Account: {e}"

    summary = event_data.get("summary", "Wydarzenie szkolne")
    start_date = event_data.get("start_date")
    if not start_date:
        return False, "Brak daty rozpoczęcia wydarzenia (start_date)"

    desc = event_data.get("description", "")
    reminder_days = int(event_data.get("reminder_days_before", 1) or 1)
    reminder_reason = event_data.get("reminder_reason", "")
    reminder_note = f"[⏰ Przypomnienie: {reminder_days} dni wcześniej - {reminder_reason}]" if reminder_reason else f"[⏰ Przypomnienie: {reminder_days} dni wcześniej]"
    full_desc = f"{reminder_note}\n\n{desc}".strip() if desc else reminder_note

    start_time = event_data.get("start_time")
    end_time = event_data.get("end_time")

    # Minuty wyprzedzenia powiadomienia (1 dzień = 1440 min, 2 dni = 2880 min)
    reminder_minutes = reminder_days * 24 * 60

    payload = {
        "summary": summary,
        "description": full_desc,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {
                    "method": "popup",
                    "minutes": reminder_minutes
                }
            ]
        }
    }

    if start_time:
        payload["start"] = {
            "dateTime": f"{start_date}T{start_time}:00",
            "timeZone": "Europe/Warsaw"
        }
        if end_time:
            payload["end"] = {
                "dateTime": f"{start_date}T{end_time}:00",
                "timeZone": "Europe/Warsaw"
            }
        else:
            try:
                hour = int(start_time.split(":")[0])
                payload["end"] = {
                    "dateTime": f"{start_date}T{hour+1:02d}:00:00",
                    "timeZone": "Europe/Warsaw"
                }
            except Exception:
                payload["end"] = {
                    "dateTime": f"{start_date}T{start_time}:00",
                    "timeZone": "Europe/Warsaw"
                }
    else:
        payload["start"] = {"date": start_date}
        try:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            payload["end"] = {"date": (s_dt + timedelta(days=1)).strftime("%Y-%m-%d")}
        except Exception:
            payload["end"] = {"date": start_date}

    encoded_cal_id = urllib.parse.quote(calendar_id)
    url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_cal_id}/events"

    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                return True, f"Dodano bezpośrednio do Kalendarza Google z przypomnieniem {reminder_days}d"
            return False, f"Status Google API: {resp.status}"
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")[:250]
        return False, f"Błąd Google Calendar API ({e.code}): {err_msg}"
    except Exception as e:
        return False, f"Błąd połączenia z Google Calendar API: {e}"

def test_calendar_connection():
    """Diagnostyka i test połączenia z Google Calendar API."""
    print("=" * 70)
    print(" 🔍 TEST POŁĄCZENIA Z KALENDARZEM GOOGLE")
    print("=" * 70)

    google_cal_id = os.getenv("GOOGLE_CALENDAR_ID")
    sa_file = get_service_account_path()
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    print(f"    - ID kalendarza:   {google_cal_id or '(BRAK w .env!)'}")
    print(f"    - Klucz (plik):    {sa_file} ({'istnieje' if sa_file.exists() and sa_file.is_file() else 'brak pliku'})")
    print(f"    - Klucz (zmienna): {'obecna' if sa_json else 'brak'}")

    if not google_cal_id:
        print("\n[!] BŁĄD: Uzupełnij GOOGLE_CALENDAR_ID w pliku .env")
        print("    Skopiujesz go z: calendar.google.com -> Ustawienia kalendarza -> Integracja kalendarza")
        return

    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests
    except ImportError:
        print("\n[!] BŁĄD: Brak biblioteki google-auth. Zainstaluj: pip install google-auth")
        return

    try:
        scopes = ["https://www.googleapis.com/auth/calendar"]
        if sa_json:
            sa_info = json.loads(sa_json)
            creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
        elif sa_file.exists() and sa_file.is_file():
            creds = service_account.Credentials.from_service_account_file(str(sa_file), scopes=scopes)
        else:
            print(f"\n[!] BŁĄD: Nie znaleziono klucza Service Account ({sa_file})")
            return

        auth_req = google.auth.transport.requests.Request()
        creds.refresh(auth_req)
        token = creds.token
        print(f"[✓] Autoryzacja Service Account udana:")
        print(f"    E-mail konta usługi: {creds.service_account_email}")
    except Exception as e:
        print(f"\n[!] BŁĄD autoryzacji Google Service Account: {e}")
        return

    # Sprawdzenie odczytu metadanych kalendarza
    encoded_cal_id = urllib.parse.quote(google_cal_id)
    cal_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_cal_id}"
    req = urllib.request.Request(cal_url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cal_name = data.get("summary", "Nieznana")
            tz = data.get("timeZone", "Nieznana")
            print(f"[✓] Połączenie z kalendarzem Google udane!")
            print(f"    - Nazwa kalendarza: {cal_name}")
            print(f"    - Strefa czasowa:   {tz}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"\n[!] BŁĄD 404 Not Found:")
            print(f"    Konto usługi ({creds.service_account_email}) nie ma dostępu do kalendarza '{google_cal_id}'.")
            print("    👉 W Google Calendar (calendar.google.com) kliknij Ustawienia kalendarza -> 'Udostępnij określonym osobom'")
            print(f"       i dodaj adres: {creds.service_account_email} z uprawnieniem 'Wprowadzanie zmian w wydarzeniach'.")
        else:
            print(f"\n[!] BŁĄD HTTP Google Calendar API ({e.code}): {e.read().decode('utf-8', errors='ignore')[:250]}")
        return
    except Exception as e:
        print(f"\n[!] Błąd połączenia: {e}")
        return

    # Test dodania i usunięcia testowego wydarzenia
    today_str = datetime.now().strftime("%Y-%m-%d")
    next_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    test_payload = {
        "summary": "[TEST] eduVULCAN Połączenie Kalendarza",
        "description": "Testowe wydarzenie sprawdzające uprawnienia do tworzenia i przypomnień dzwoneczkiem.",
        "start": {"date": today_str},
        "end": {"date": next_str},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 1440}]
        }
    }
    events_url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_cal_id}/events"
    try:
        req_post = urllib.request.Request(
            events_url,
            data=json.dumps(test_payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST"
        )
        ev_id = None
        with urllib.request.urlopen(req_post, timeout=10) as resp_post:
            ev_data = json.loads(resp_post.read().decode("utf-8"))
            ev_id = ev_data.get("id")
            print(f"[✓] Test zapisu: Pomyślnie utworzono wydarzenie testowe z przypomnieniem 1440 min!")

        if ev_id:
            del_url = f"{events_url}/{ev_id}"
            req_del = urllib.request.Request(del_url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
            with urllib.request.urlopen(req_del, timeout=10):
                print(f"[✓] Porządki: Usunięto wydarzenie testowe.")
        print("\n🎉 Kalendarz Google z powiadomieniami dzwoneczkiem działa w 100%!")
    except Exception as e:
        print(f"[!] Błąd podczas testu zapisu do kalendarza: {e}")
        return



def analyze_importance_with_ai(subject, body_text, message_date_str=None, student_name=""):
    """Analizuje treść wiadomości przy użyciu modelu Gemini Flash, oceniając ważność oraz wyodrębniając wydarzenia do kalendarza."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"is_important": False, "important_note": "", "highlighted_body": body_text, "calendar_events": []}

    today_str = datetime.now().strftime("%Y-%m-%d")
    ref_date = message_date_str or today_str

    prompt = (
        "Jesteś asystentem rodzica analizującym wiadomości ze szkoły (dziennik eduVULCAN).\n"
        f"Dzisiejsza data: {today_str}\n"
        f"Data wysłania wiadomości (punkt odniesienia dla słów 'jutro', 'w piątek' itp.): {ref_date}\n"
        f"Uczeń: {student_name}\n\n"
        "Twoim zadaniem jest:\n"
        "1. Ocenić, czy wiadomość jest NAPRAWDĘ WAŻNA dla rodzica (is_important: true/false).\n"
        "2. Wyodrębnić WSZYSTKIE KONKRETNE WYDARZENIA do kalendarza rodzinnego (calendar_events - tablica obiektów).\n\n"
        "Zasady dla kalendarza:\n"
        "- Dodawaj: wycieczki, wyjścia klasowe, zebrania z rodzicami, uroczystości szkolne, dni wolne, zawody, sprawdziany/dyktanda, ostateczne terminy odbioru (np. odbiór zgubionych rzeczy).\n"
        "- NIE dodawaj: ofert komercyjnych (np. płatne zajęcia z klocków/robotyki/programowania), pism ogólnych o bezpieczeństwie, spraw bieżących bez daty.\n"
        "- Zmiany terminów: jeśli wiadomość informuje o zmianie terminu wyjazdu/wydarzenia, ZAWSZE uwzględnij ten NOWY termin (np. wyjazd do lasu przeniesiony na 15.10 -> start_date: 2026-10-15)!\n"
        "- Jeśli wiadomość wymienia więcej niż jedno wydarzenie (np. rejs 7.10 oraz wyjazd 15.10), umieść OBA w tablicy calendar_events.\n"
        "- Data wydarzenia (start_date) musi być w formacie RRRR-MM-DD obliczona na podstawie daty wysłania wiadomości i treści.\n"
        "- Przypomnienie (reminder_days_before): domyślnie 1 dzień wcześniej (1), ale jeśli treść wymaga zrobienia zakupów, wpłaty pieniędzy, przygotowania stroju galowego lub prowiantu, ustaw 2 dni wcześniej (2).\n\n"
        "Zwróć WYŁĄCZNIE poprawny JSON o strukturze:\n"
        "{\n"
        '  "is_important": true/false,\n'
        '  "important_note": "Krótka notatka w 1 zwięzłym zdaniu (co/kiedy/co zrobić) - wypełnij TYLKO gdy is_important=true, w przeciwnym razie pusty string",\n'
        '  "highlighted_body": "Treść wiadomości z wyboldowanymi (używając **słowo**) kluczowymi terminami, godzinami, kwotami lub poleceniami. Jeśli nie jest ważna, zwróć oryginalną treść.",\n'
        '  "calendar_events": [\n'
        "    {\n"
        '      "summary": "Tytuł wydarzenia (np. [' + (student_name or 'Uczeń') + '] Wycieczka do lasu)",\n'
        '      "start_date": "RRRR-MM-DD",\n'
        '      "start_time": "GG:MM" lub null,\n'
        '      "end_time": "GG:MM" lub null,\n'
        '      "reminder_days_before": 1 lub 2,\n'
        '      "reminder_reason": "Dlaczego 1 czy 2 dni",\n'
        '      "description": "Zwięzły opis co zabrać / kiedy powrót"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    models_to_try = ["gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-3-flash-preview"]
    payload = {
        "contents": [{"parts": [{"text": f"{prompt}\n\nWIADOMOŚĆ DO ANALIZY:\nTEMAT: {subject}\nTREŚĆ:\n{body_text}"}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }

    data_bytes = json.dumps(payload).encode("utf-8")

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                text_out = res_data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text_out)
                events = parsed.get("calendar_events") or []
                if not events and parsed.get("calendar_event"):
                    ce = parsed["calendar_event"]
                    if ce.get("has_event") and ce.get("start_date"):
                        events = [ce]
                return {
                    "is_important": bool(parsed.get("is_important", False)),
                    "important_note": parsed.get("important_note", "").strip(),
                    "highlighted_body": parsed.get("highlighted_body", body_text).strip(),
                    "calendar_events": events
                }
        except Exception:
            continue

    return {"is_important": False, "important_note": "", "highlighted_body": body_text, "calendar_events": []}


def send_notification_email(student_name, student_class, school_name, sender_name, date_str, subject, body_text, ai_info=None, calendar_status=""):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO")

    if not smtp_user or not smtp_password or not email_to:
        raise ValueError("Brak konfiguracji SMTP w .env (wymagane: SMTP_USER, SMTP_PASSWORD, EMAIL_TO)")

    recipients = [r.strip() for r in email_to.split(",") if r.strip()]
    if not recipients:
        raise ValueError("Lista odbiorców EMAIL_TO jest pusta")

    is_important = bool(ai_info and ai_info.get("is_important"))
    important_note = ai_info.get("important_note", "") if ai_info else ""
    display_body = ai_info.get("highlighted_body", body_text) if (ai_info and ai_info.get("highlighted_body")) else body_text

    msg = MIMEMultipart("alternative")
    if is_important:
        msg["Subject"] = f"[eduVULCAN - {student_name}][📌 WAŻNE] {subject}"
        msg["X-Priority"] = "1"
        msg["Importance"] = "High"
    else:
        msg["Subject"] = f"[eduVULCAN - {student_name}] {subject}"

    msg["From"] = f"eduVULCAN <{smtp_user}>"
    msg["To"] = ", ".join(recipients)

    note_plain = f"📌 WAŻNE: {important_note}\n\n" if (is_important and important_note) else ""
    cal_plain = f"📅 KALENDARZ DOMOWY: {calendar_status}\n\n" if calendar_status else ""
    plain_text = (
        f"Wiadomość z dziennika eduVULCAN{' [📌 WAŻNE]' if is_important else ''}\n"
        f"============================================================\n"
        f"Uczeń:   {student_name} (klasa: {student_class or 'brak'})\n"
        f"Szkoła:  {school_name}\n"
        f"Od:      {sender_name}\n"
        f"Data:    {date_str}\n"
        f"Temat:   {subject}\n"
        f"============================================================\n\n"
        f"{note_plain}"
        f"{cal_plain}"
        f"{display_body}\n\n"
        f"------------------------------------------------------------\n"
        f"Wysłano automatycznie przez skrypt eduVULCAN CLI."
    )

    body_escaped = html.escape(display_body)
    body_html_formatted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body_escaped)

    note_box_html = ""
    if is_important and important_note:
        note_box_html = f"""
        <div style="background-color: #fef7e0; border-left: 4px solid #f9ab00; padding: 12px 16px; margin-bottom: 14px; border-radius: 4px; font-size: 14px; color: #b06000;">
          <strong>📌 Ważne:</strong> {html.escape(important_note)}
        </div>
        """

    cal_box_html = ""
    if calendar_status:
        cal_box_html = f"""
        <div style="background-color: #e6f4ea; border-left: 4px solid #34a853; padding: 10px 15px; margin-bottom: 14px; border-radius: 4px; font-size: 13px; color: #137333;">
          📅 <strong>Kalendarz domowy:</strong> {html.escape(calendar_status)}
        </div>
        """

    header_badge = ' <span style="background-color: #ea4335; color: #fff; padding: 2px 7px; border-radius: 4px; font-size: 11px; margin-left: 8px; vertical-align: middle;">📌 WAŻNE</span>' if is_important else ""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #202124; line-height: 1.5; padding: 15px; margin: 0; background-color: #f8f9fa;">
      <div style="max-width: 650px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #dadce0; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
        <div style="background-color: #006bd6; color: #ffffff; padding: 14px 20px;">
          <h2 style="margin: 0; font-size: 18px; font-weight: 600;">eduVULCAN &bull; Nowa wiadomość{header_badge}</h2>
          <div style="font-size: 13px; opacity: 0.9; margin-top: 3px;">{school_name}</div>
        </div>
        <div style="background-color: #f1f3f4; padding: 12px 20px; border-bottom: 1px solid #e0e0e0; font-size: 14px;">
          <table style="width: 100%; border-collapse: collapse;">
            <tr>
              <td style="padding: 3px 0; color: #5f6368; width: 85px;"><strong>Uczeń:</strong></td>
              <td style="padding: 3px 0; font-weight: bold; color: #1a73e8;">{student_name} <span style="font-weight: normal; color: #5f6368;">(klasa {student_class or 'brak'})</span></td>
            </tr>
            <tr>
              <td style="padding: 3px 0; color: #5f6368;"><strong>Od:</strong></td>
              <td style="padding: 3px 0; color: #202124;">{sender_name}</td>
            </tr>
            <tr>
              <td style="padding: 3px 0; color: #5f6368;"><strong>Data:</strong></td>
              <td style="padding: 3px 0; color: #202124;">{date_str}</td>
            </tr>
            <tr>
              <td style="padding: 3px 0; color: #5f6368;"><strong>Temat:</strong></td>
              <td style="padding: 3px 0; font-weight: 600; color: #202124;">{subject}</td>
            </tr>
          </table>
        </div>
        <div style="padding: 24px 20px; font-size: 15px; color: #202124; line-height: 1.6; white-space: pre-wrap;">
{note_box_html}{cal_box_html}{body_html_formatted}
        </div>
        <div style="background-color: #fafafa; border-top: 1px solid #eeeeee; padding: 12px 20px; font-size: 11px; color: #70757a; text-align: center;">
          Powiadomienie wygenerowane automatycznie przez skrypt eduVULCAN CLI.
        </div>
      </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    clean_password = smtp_password.replace(" ", "")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, clean_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
    except smtplib.SMTPAuthenticationError as auth_err:
        raise RuntimeError(
            "Błąd logowania Gmail: Wymagane 16-znakowe Hasło aplikacji z https://myaccount.google.com/apppasswords"
        ) from auth_err

def full_login_and_save_session(p):
    """Wykonuje pełne logowanie przez Playwright, rozwiązuje PoW i zapisuje storage_state oraz dane uczniów."""
    user = os.getenv("VULCAN_USER")
    password = os.getenv("VULCAN_PASSWORD")

    if not user or not password:
        print("[!] Błąd: Brak VULCAN_USER lub VULCAN_PASSWORD w pliku .env.")
        sys.exit(1)

    print("[*] Wykonywanie pełnego logowania przez Playwright...")
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://eduvulcan.pl/logowanie?ReturnUrl=dostep-do-dziennik")

    # Usunięcie overlayów cookies
    try:
        privacy_btn = page.frame_locator("#respect-privacy-frame").locator("button:has-text('Zgadzam się')")
        privacy_btn.click(timeout=3000)
    except Exception:
        pass

    page.evaluate("""() => {
        document.querySelectorAll('.cookie-wrapper, #respect-privacy-wrapper, #respect-privacy-frame, #information-clause-wrapper').forEach(el => el.remove());
    }""")

    page.fill("#UserName", user)
    page.locator("#btNext").click(force=True)
    page.wait_for_selector("#wizard2", state="visible", timeout=15000)

    # Rozwiązanie PoW Captcha
    page.evaluate('''async () => {
        const captcha = document.getElementById('captcha');
        if (!captcha || window.getComputedStyle(captcha).display === 'none') return;
        const respInput = document.getElementById('captcha-response');
        if (respInput && respInput.value) return;
        const wrapper = document.querySelector('.captcha-wrapper');
        const challenge = wrapper.dataset.challenge;
        const difficulty = +wrapper.dataset.difficulty || 100000;
        const rounds = +wrapper.dataset.rounds || 15;
        const solveRound = async (prefix, targetDiff) => {
            for (let nonce = 1; nonce < 1e8; nonce++) {
                const msg = prefix + nonce;
                const buf = new Uint8Array(msg.length);
                for (let i = 0; i < msg.length; i++) buf[i] = msg.charCodeAt(i) & 255;
                const hashBuf = await crypto.subtle.digest('SHA-256', buf);
                const n = new Uint8Array(hashBuf);
                if (((n[0] << 24 | n[1] << 16 | n[2] << 8 | n[3]) >>> 0) < targetDiff) return nonce;
            }
        };
        const nonces = [];
        for (let r = 0; r < rounds; r++) {
            nonces.push(await solveRound(challenge + nonces.join(''), difficulty));
        }
        respInput.value = nonces.join(';');
    }''')

    page.evaluate("""() => {
        document.querySelectorAll('.cookie-wrapper, #respect-privacy-wrapper, #respect-privacy-frame').forEach(el => el.remove());
    }""")

    page.fill("#Password", password)
    page.wait_for_timeout(300)
    page.locator("#btLogOn").click(force=True)
    page.wait_for_url(lambda u: "logowanie" not in u, timeout=25000)

    # Wyszukanie linków do dzienników uczniów
    diary_links = []
    for selector in ["a[href*='dziennik?']", "a[href*='uczen.']"]:
        for el in page.locator(selector).all():
            href = el.get_attribute("href")
            if href and href not in diary_links:
                diary_links.append(href)

    if not diary_links:
        for selector in ["a.panel-access__profile", "a.connected-account", "a[title*='Dziennik']"]:
            for el in page.locator(selector).all():
                href = el.get_attribute("href")
                if href and ("uczen." in href or "dziennik" in href) and href not in diary_links:
                    diary_links.append(href)

    all_students = []
    seen_students_keys = set()
    visited_cities = set()

    for link in diary_links:
        if not link.startswith("http"):
            continue

        try:
            page.goto(link, wait_until="networkidle", timeout=30000)
        except Exception:
            page.wait_for_load_state("domcontentloaded")

        if "/End/NieaktywnyUczen" in page.url:
            continue

        match = re.search(r"uczen\.([^/]+)/([^/]+)", page.url)
        if not match:
            continue

        domain, city = match.group(1), match.group(2)

        try:
            ctx_res = page.request.get(f"https://uczen.{domain}/{city}/api/Context")
            if ctx_res.status == 200:
                ctx_data = ctx_res.json()
                for u in ctx_data.get("uczniowie", []):
                    st_key = u.get("key")
                    if st_key and st_key not in seen_students_keys:
                        seen_students_keys.add(st_key)
                        all_students.append({
                            "name": u.get("uczen", "Nieznany uczeń"),
                            "class": u.get("oddzial", ""),
                            "school": u.get("jednostka", ""),
                            "mailbox_key": u.get("globalKeySkrzynka"),
                            "domain": domain,
                            "city": city,
                            "is_przedszkolak": u.get("isPrzedszkolak", False)
                        })
        except Exception as e:
            print(f"[!] Błąd odczytu kontekstu uczniów: {e}")

        # Nawigacja do aplikacji Wiadomości, aby wymienić tokeny SSO i otrzymać EduVulcan.Wiadomosci.Sso
        if (domain, city) not in visited_cities:
            try:
                page.goto(f"https://wiadomosci.{domain}/{city}/App", wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1000)
            except Exception as e:
                print(f"[!] Ostrzeżenie przy otwieraniu wiadomości: {e}")
            visited_cities.add((domain, city))

    # Weryfikacja czy sesja wiadomości działa prawidłowo przed zapisem
    if all_students:
        for s in all_students:
            mb = s.get("mailbox_key")
            if mb:
                verify_url = f"https://wiadomosci.{s['domain']}/{s['city']}/api/OdebraneSkrzynka?globalKeySkrzynka={mb}&idLastWiadomosc=0&pageSize=1"
                chk = page.request.get(verify_url)
                if chk.status != 200:
                    page.goto(f"https://wiadomosci.{s['domain']}/{s['city']}/App", wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(1000)
                break

    # Zapisanie stanu sesji Playwright (ciasteczka wszystkich subdomen)
    context.storage_state(path=str(STORAGE_STATE_FILE))
    save_cached_students(all_students)
    browser.close()

    print(f"[✓] Odświeżono i zapisano stan sesji ({STORAGE_STATE_FILE.name}) dla {len(all_students)} uczniów.")
    return all_students

def fetch_messages_with_state(p, students, fetch_limit, show_all, unread_only, seen_ids):
    """Szybkie pobieranie wiadomości przez Playwright APIRequestContext z użyciem zapisanego storage_state (~0.5s)."""
    if not STORAGE_STATE_FILE.exists() or not students:
        return None, False

    t0 = time.time()
    browser = p.chromium.launch(headless=True)
    # Wczytanie zapisanego stanu sesji
    context = browser.new_context(storage_state=str(STORAGE_STATE_FILE))

    messages_by_student = {}
    success = True

    for s in students:
        domain = s["domain"]
        city = s["city"]
        mb_key = s["mailbox_key"]
        s_name = s["name"]

        if not mb_key:
            continue

        if s.get("is_przedszkolak"):
            inbox_url = f"https://wiadomosci.{domain}/{city}/api/Odebrane?pageSize={fetch_limit}"
        else:
            inbox_url = f"https://wiadomosci.{domain}/{city}/api/OdebraneSkrzynka?globalKeySkrzynka={mb_key}&idLastWiadomosc=0&pageSize={fetch_limit}"

        res = context.request.get(inbox_url)

        # Jeśli status != 200, sesja wygasła
        if res.status != 200:
            print(f"[!] Zapytanie do skrzynki ({s_name}) zwróciło kod {res.status}. Sesja wymaga odświeżenia.")
            success = False
            break

        try:
            raw_msgs = res.json()
        except Exception:
            success = False
            break

        if not isinstance(raw_msgs, list):
            success = False
            break

        student_msgs = []
        for m in raw_msgs:
            m_id = m.get("apiGlobalKey") or m.get("id")
            if not m_id:
                continue

            is_read_in_vulcan = m.get("przeczytana", False)
            if unread_only and is_read_in_vulcan:
                continue

            if show_all or unread_only or (m_id not in seen_ids):
                det_url = f"https://wiadomosci.{domain}/{city}/api/WiadomoscSzczegoly?apiGlobalKey={m_id}"
                det_res = context.request.get(det_url)
                body_text = ""
                if det_res.status == 200:
                    try:
                        body_text = det_res.json().get("tresc", "")
                    except Exception:
                        pass

                student_msgs.append({
                    "id": m_id,
                    "date": m.get("data", "")[:16].replace("T", " "),
                    "sender": m.get("korespondenci", "Nieznany nadawca"),
                    "subject": m.get("temat", "(brak tematu)"),
                    "body": clean_html(body_text),
                    "already_read_in_vulcan": is_read_in_vulcan,
                    "is_new_for_script": m_id not in seen_ids
                })

        messages_by_student[s_name] = {
            "student_info": s,
            "messages": student_msgs
        }

    browser.close()

    if success:
        elapsed = time.time() - t0
        print(f"[✓] Szybkie sprawdzenie z zapamiętanej sesji ukończone w {elapsed:.2f} s!")
        return messages_by_student, True
    else:
        return None, False

def run():
    parser = argparse.ArgumentParser(description="Pobieranie wiadomości z eduVULCAN oraz wysyłka na e-mail z pamięcią sesji")
    parser.add_argument("--last", "-n", type=int, default=None, help="Wyświetl/wyślij N ostatnich wiadomości na ucznia (np. --last 1)")
    parser.add_argument("--unread", "-u", action="store_true", help="Pobierz TYLKO wiadomości ze statusem nieprzeczytane w eduVULCAN")
    parser.add_argument("--no-email", action="store_true", help="Nie wysyłaj e-maili (tylko podgląd w terminalu)")
    parser.add_argument("--force-login", action="store_true", help="Wymuś ponowne pełne logowanie przez Playwright")
    parser.add_argument("--reset", action="store_true", help="Wyczyść historię przeczytanych wiadomości")
    parser.add_argument("--reset-calendar", action="store_true", help="Wyczyść pamięć dodanych wydarzeń kalendarza (umożliwia ponowne ich dodanie)")
    parser.add_argument("--test-calendar", action="store_true", help="Przetestuj połączenie z kalendarzem Google")
    args = parser.parse_args()

    if args.test_calendar:
        test_calendar_connection()
        return

    seen_ids = load_seen()
    added_calendar_events = load_added_calendar_events()

    if args.reset_calendar:
        save_added_calendar_events(set())
        added_calendar_events = set()
        print("[✓] Pamięć dodanych wydarzeń kalendarza (added_calendar_events.json) została wyczyszczona.")
        if not args.last and not args.unread:
            return

    if args.reset:
        save_seen(set())
        seen_ids = set()
        print("[✓] Pamięć przeczytanych wiadomości (seen_messages.json) została wyczyszczona.")
        if not args.last and not args.unread:
            return

    if not args.no_email:
        smtp_user = os.getenv("SMTP_USER")
        smtp_password = os.getenv("SMTP_PASSWORD")
        email_to = os.getenv("EMAIL_TO")
        if not smtp_user or not smtp_password or not email_to:
            print("[!] Ostrzeżenie: Brak pełnej konfiguracji SMTP w .env. Tryb --no-email.")
            args.no_email = True

    show_all = args.last is not None
    fetch_limit = args.last if args.last else 20

    if not sync_playwright:
        print("[!] BŁĄD: Brak biblioteki playwright. Zainstaluj: pip install playwright")
        return

    with sync_playwright() as p:
        students = None
        messages_by_student = None
        session_valid = False

        # Próba szybkiej ścieżki z zapamiętanej sesji
        if not args.force_login and STORAGE_STATE_FILE.exists():
            students = load_cached_students()
            if students:
                messages_by_student, session_valid = fetch_messages_with_state(
                    p, students, fetch_limit, show_all, args.unread, seen_ids
                )

        # Jeśli brak sesji lub sesja wygasła -> pełne logowanie
        if not session_valid:
            if STORAGE_STATE_FILE.exists():
                print("[!] Zapamiętana sesja wygasła. Odświeżam logowanie...")
            else:
                print("[*] Pierwsze uruchomienie. Wykonuję logowanie początkowe...")

            students = full_login_and_save_session(p)
            messages_by_student, session_valid = fetch_messages_with_state(
                p, students, fetch_limit, show_all, args.unread, seen_ids
            )
            if not session_valid:
                print("[!] Błąd: Nie udało się pobrać wiadomości nawet po świeżym logowaniu.")
                sys.exit(1)

    total_found = sum(len(data["messages"]) for data in messages_by_student.values())
    if total_found == 0:
        print("[i] Brak nowych wiadomości dla żadnego z uczniów. Żadne e-maile nie zostały wysłane.")
        return

    print(f"\n{'='*75}")
    print(f" RAPORT WIADOMOŚCI (Łącznie do przetworzenia: {total_found})")
    print(f"{'='*75}")

    emails_sent_count = 0
    today = datetime.now().date()

    for s_name, data in messages_by_student.items():
        st = data["student_info"]
        msgs = data["messages"]
        print(f"\n👤 UCZEŃ: {s_name} | Klasa: {st['class'] or 'brak'} | Szkoła: {st['school']}")
        print(f"{'─'*75}")

        if not msgs:
            print("  (Brak wiadomości do wyświetlenia)")
            continue

        for i, m in enumerate(msgs, 1):
            tag = "[NOWA]" if m["is_new_for_script"] else "[POPRZEDNIA]"
            vulcan_tag = "Przeczytana w Vulcan" if m["already_read_in_vulcan"] else "Nieprzeczytana w Vulcan"

            # Inteligentna analiza ważności wiadomości i kalendarza (Gemini Flash)
            msg_date_short = m["date"][:10] if m.get("date") else None
            ai_info = analyze_importance_with_ai(m["subject"], m["body"], message_date_str=msg_date_short, student_name=s_name)
            is_important = ai_info.get("is_important", False)
            prio_tag = " [📌 WAŻNE]" if is_important else ""

            # Kalendarz Google (tylko wydarzenia na przyszłość >= dzisiaj)
            calendar_status_text = ""
            cal_events_to_add = ai_info.get("calendar_events") or []

            for cal_ev in cal_events_to_add:
                if not cal_ev or not cal_ev.get("start_date"):
                    continue
                try:
                    ev_date = datetime.strptime(cal_ev["start_date"], "%Y-%m-%d").date()
                    ev_key = f"{cal_ev['start_date']}_{cal_ev.get('summary', '')}"
                    if ev_date >= today:
                        if ev_key not in added_calendar_events:
                            success, msg_status = add_event_to_calendar(cal_ev)
                            if success:
                                added_calendar_events.add(ev_key)
                                save_added_calendar_events(added_calendar_events)
                                calendar_status_text = f"{cal_ev['summary']} ({cal_ev['start_date']})"
                                print(f"      📅 Kalendarz: Dodano pomyślnie! ({cal_ev['summary']} na {cal_ev['start_date']})")
                            else:
                                print(f"      [!] Kalendarz: {msg_status}")
                        else:
                            calendar_status_text = f"{cal_ev['summary']} ({cal_ev['start_date']})"
                            print(f"      📅 Kalendarz: Wydarzenie już w kalendarzu ({cal_ev['summary']})")
                    else:
                        print(f"      📅 Kalendarz: Pominięto przeszłe wydarzenie ({cal_ev['start_date']})")
                except Exception as ex:
                    print(f"      [!] Błąd kalendarza: {ex}")


            print(f"  [{i}] {tag}{prio_tag} ({vulcan_tag})")
            print(f"      📅 Data:    {m['date']}")
            print(f"      ✉️  Od:      {m['sender']}")
            print(f"      📌 Temat:   {m['subject']}")
            if is_important and ai_info.get("important_note"):
                print(f"      💡 Notatka: {ai_info['important_note']}")
            print(f"      📝 Treść:")
            display_lines = (ai_info.get("highlighted_body") or m["body"]).splitlines()
            for line in display_lines:
                print(f"         {line}")

            if not args.no_email:
                try:
                    send_notification_email(
                        student_name=s_name,
                        student_class=st["class"],
                        school_name=st["school"],
                        sender_name=m["sender"],
                        date_str=m["date"],
                        subject=m["subject"],
                        body_text=m["body"],
                        ai_info=ai_info,
                        calendar_status=calendar_status_text
                    )
                    emails_sent_count += 1
                    prio_info = " (Wysoki priorytet 📌)" if is_important else ""
                    print(f"      📧 E-mail:  Wysłano pomyślnie!{prio_info}")
                    seen_ids.add(m["id"])
                except Exception as e:
                    print(f"      [!] Błąd wysyłki e-mail: {e}")
            else:
                seen_ids.add(m["id"])

            print(f"  {'·'*71}")

    save_seen(seen_ids)
    save_added_calendar_events(added_calendar_events)
    if emails_sent_count > 0:
        print(f"\n[✓] Zakończono! Wysłano łącznie {emails_sent_count} wiadomości e-mail.")
    print(f"[✓] Zaktualizowano bazę odczytanych wiadomości ({SEEN_FILE.name}).")

if __name__ == "__main__":
    run()
