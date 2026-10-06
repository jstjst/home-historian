# Ingester: MQTT -> TimescaleDB.
FROM python:3.12-slim

# psycopg[binary] ships libpq, so no system build tools are needed.
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mqtt_ingest.py .

# Do not run as root.
RUN useradd --create-home --uid 1000 ingest
USER ingest

# Unbuffered logs, so they show up in `docker compose logs` straight away.
ENV PYTHONUNBUFFERED=1

CMD ["python", "mqtt_ingest.py"]
