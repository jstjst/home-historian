# vicare module: polls the Viessmann ViCare cloud API and publishes to MQTT.
FROM python:3.12-slim

# No buffering: otherwise log lines sit in a buffer until the container dies,
# which is useless when debugging a login or a rate limit.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Do not run as root. A fixed UID keeps the permissions on the volume
# predictable (the token is stored there with mode 0600).
RUN useradd --create-home --uid 1000 vicare

WORKDIR /app

# Dependencies first: when only the code changes, this layer stays cached.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vicare_publisher.py healthcheck.py ./

# Home of the OAuth session (token.pickle) and the heartbeat. Must be a
# volume, or every container restart is followed by a fresh login.
RUN mkdir -p /data && chown vicare:vicare /data
VOLUME ["/data"]

USER vicare

# Checks the heartbeat file, not the process: the publisher can be running
# and still not have fetched anything for hours.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["python", "-u", "vicare_publisher.py"]
