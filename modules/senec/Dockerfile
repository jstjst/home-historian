# senec module: reads a SENEC.Home battery storage over its local API
# (lala.cgi) and publishes to MQTT.
FROM python:3.12-slim

# No buffering: otherwise log lines sit in a buffer until the container dies.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Do not run as root.
RUN useradd --create-home --uid 1000 senec

WORKDIR /app

# Dependencies first: when only the code changes, this layer stays cached.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY senec_publisher.py healthcheck.py ./

# Home of the heartbeat. compose.yml puts a tmpfs here, so that touching it
# every pass never writes to the disk.
RUN mkdir -p /run/senec && chown senec:senec /run/senec

USER senec

# Checks the heartbeat file, not the process: the publisher can be running
# while the unit does not answer.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["python", "-u", "senec_publisher.py"]
