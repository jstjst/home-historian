# mbus module: reads wired M-Bus meters over a serial port and publishes to MQTT.
FROM python:3.12-slim

# No buffering: otherwise log lines sit in a buffer until the container dies.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Do not run as root. Access to the serial device comes from the group
# "dialout", added in compose.yml.
RUN useradd --create-home --uid 1000 mbus

WORKDIR /app

# Dependencies first: when only the code changes, this layer stays cached.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mbus_publisher.py healthcheck.py mbus_dump.py ./

# Home of the heartbeat. compose.yml puts a tmpfs here, so that touching it
# every pass never writes to the disk.
RUN mkdir -p /run/mbus && chown mbus:mbus /run/mbus

USER mbus

# Checks the heartbeat file, not the process: the publisher can be running
# while no meter answers.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "healthcheck.py"]

CMD ["python", "-u", "mbus_publisher.py"]
