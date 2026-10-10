#!/usr/bin/env python3
"""
eduVULCAN - Bezpieczny harmonogram dla kontenera Docker.
Sprawdza wiadomości w szybkim trybie co 5 minut (w godz. 07:00-22:00) dzięki cache ciasteczek sesji.
W nocy (22:00-07:00) całkowicie pauzuje.
"""
import os
import subprocess
import time
from datetime import datetime
import schedule

# Interwał sprawdzania: domyślnie 5 minut
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))

def run_check():
    now = datetime.now()
    # Tryb nocny: od 22:00 do 07:00 szkoła nie pracuje, więc nie odpytujemy
    if now.hour >= 22 or now.hour < 7:
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Tryb nocny aktywny (22:00 - 07:00). Pomijam sprawdzanie.")
        return

    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Sprawdzanie eduVULCAN...")
    try:
        res = subprocess.run(["python3", "vulcan_messages.py"], capture_output=False, check=False)
        if res.returncode != 0:
            print(f"[!] Skrypt zakończył się kodem błędu: {res.returncode}")
    except Exception as e:
        print(f"[!] Błąd uruchomienia skryptu: {e}")

# Rejestracja zadania w harmonogramie
schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_check)

print(f"[*] eduVULCAN Scheduler uruchomiony.")
print(f"[*] Interwał sprawdzania: co {CHECK_INTERVAL_MINUTES} minut (aktywny w godz. 07:00 - 22:00).")
print(f"[*] Wykonuję pierwsze sprawdzenie na starcie...")

# Pierwsze sprawdzenie od razu po starcie kontenera
run_check()

# Pętla oczekiwania
while True:
    schedule.run_pending()
    time.sleep(10)
