FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

WORKDIR /app

# Instalacja zależności Pythona
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiowanie kodu aplikacji
COPY src/ ./src/

# Wymuszenie natychmiastowego buforowania wyjścia logów (ważne dla docker logs)
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

CMD ["python3", "src/scheduler.py"]
