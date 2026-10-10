FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

WORKDIR /app

# Instalacja zależności Pythona
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiowanie plików aplikacji
COPY vulcan_messages.py scheduler.py ./

# Wymuszenie natychmiastowego buforowania wyjścia logów (ważne dla docker logs)
ENV PYTHONUNBUFFERED=1

CMD ["python3", "scheduler.py"]
