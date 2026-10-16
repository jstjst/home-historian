# tuya module: listens to the values the Smart Life cloud pushes for Tuya
# devices and publishes them to MQTT.
FROM python:3.12-slim

# No buffering: otherwise log lines sit in a buffer until the container dies.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Do not run as root.
RUN useradd --create-home --uid 1000 tuya

WORKDIR /app

# Dependencies first: when only the code changes, this layer stays cached.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tuya_publisher.py tuya_login.py healthcheck.py ./

# /data holds the login (volume, see compose.yml); it has to belong to the
# user before the volume is created, which copies the ownership. /run/tuya
# is the heartbeat's tmpfs.
RUN mkdir -p /data /run/tuya && chown tuya:tuya /data /run/tuya
VOLUME ["/data"]

USER tuya

# Checks the heartbeat file, not the process: the publisher can be running
# while one of its connections is down.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["python", "-u", "tuya_publisher.py"]
