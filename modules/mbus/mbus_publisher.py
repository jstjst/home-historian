"""mbus module: wired M-Bus meters -> MQTT (decoding by pyMeterBus).

Asks every configured meter for its data telegram (REQ_UD2 to its primary
address), decodes the answer and publishes every data record as a topic of
its own:

    <prefix>/<meter>/<DIF>_<VIF>

In the stack <prefix> is "historian/mbus". For the ingester that makes
"mbus/<meter>" the device_key and "<DIF>_<VIF>" the native_key.

The key is the record's own header, in hex, exactly as the meter sends it:
the DIF (with its extensions) says which value of a quantity this is (current,
maximum, stored at the target date), the VIF (with its extensions) says which
quantity. Together they are unique within a telegram and the same for every
meter of a type, including the manufacturer-specific records a generic name
could not tell apart.

The payload is the value as pyMeterBus scales it (Wh, m^3, W, degrees
Celsius, seconds), not the meter's display unit. All records of one telegram
share one time, the moment of the reading, sent as the MQTT 5 user property
"timestamp".
"""

from __future__ import annotations

import logging
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterator

import meterbus
import paho.mqtt.client as mqtt
import serial
from meterbus.exceptions import MBusError
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger("mbus_publisher")

# The longest frame is 261 bytes of 11 bits each (8E1 with its start bit).
MAX_FRAME_BITS = 261 * 11

# Answers to this address come from every meter at once: usable with exactly
# one meter on the bus, a collision with more.
BROADCAST_ADDRESS = 254


class Settings(BaseSettings):
    """Configuration from the environment."""

    model_config = SettingsConfigDict(env_prefix="mbus_")

    # A serial device, or any pyserial URL (socket://host:port for the
    # simulator or a serial-to-TCP gateway).
    serial_port: str = "/dev/ttyUSB0"
    baudrate: int = 2400

    # "<name>:<primary address>", comma separated. The name becomes the topic
    # segment and with it the device_key "mbus/<name>". No default: addresses
    # belong to the installation, and guessing one reads the wrong meter.
    meters: Annotated[dict[str, int], NoDecode]

    # Battery-powered meters pay for every answer. Kamstrup states the battery
    # lifetime of the MULTICAL 303 for at most one reading every 120 s, and
    # its battery cannot be replaced without breaking the verification seal.
    poll_interval_seconds: int = Field(default=120, ge=120)

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "historian/mbus"
    mqtt_client_id: str = "home-historian-mbus"

    # Touched after every complete pass. It only exists so the healthcheck
    # has something real to look at.
    heartbeat_file: Path = Path("heartbeat")

    @field_validator("meters", mode="before")
    @classmethod
    def parse_meters(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        meters: dict[str, str] = {}
        for item in filter(None, (part.strip() for part in value.split(","))):
            name, separator, address = item.partition(":")
            if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError(f"expected <name>:<address>, got {item!r}")
            if name in meters:
                raise ValueError(f"meter {name!r} is listed twice")
            meters[name] = address
        return meters

    @field_validator("meters")
    @classmethod
    def check_addresses(cls, meters: dict[str, int]) -> dict[str, int]:
        if not meters:
            raise ValueError("no meters configured")
        for name, address in meters.items():
            if address == BROADCAST_ADDRESS and len(meters) == 1:
                continue
            if not 0 <= address <= 250:
                raise ValueError(
                    f"address {address} of meter {name!r}: primary addresses are 0-250 "
                    f"({BROADCAST_ADDRESS} only for a single meter on the bus)"
                )
        if len(set(meters.values())) < len(meters):
            raise ValueError("two meters share one address")
        return meters


@dataclass(frozen=True, slots=True)
class Measurement:
    """One data record, ready to publish."""

    topic: str
    payload: str
    timestamp: str


class MeterError(Exception):
    """A meter did not answer, or not with a usable telegram."""


# ---------------------------------------------------------------------
# M-Bus
# ---------------------------------------------------------------------

def request_frame(address: int) -> bytes:
    """REQ_UD2 as a short frame: start, C field, address, checksum, stop."""
    control = 0x5B
    return bytes([0x10, control, address, (control + address) & 0xFF, 0x16])


def read_telegram(port: serial.SerialBase, address: int) -> bytes:
    """Ask one meter for its data and return the raw long frame."""
    request = request_frame(address)
    port.reset_input_buffer()
    port.write(request)

    head = port.read(4)
    if head == request[:4]:
        # Half-duplex RS485: some adapters hand back what was just sent. An
        # answer never looks like this (it starts with 0x68, not 0x10).
        port.read(1)
        head = port.read(4)
    if not head:
        raise MeterError("no answer")
    # Long frame: 68 L L 68, then L bytes, checksum, 16.
    if len(head) < 4 or head[0] != 0x68 or head[3] != 0x68 or head[1] != head[2]:
        raise MeterError(f"not the start of a data telegram: {head.hex()}")
    rest = port.read(head[1] + 2)
    if len(rest) < head[1] + 2:
        raise MeterError(f"telegram cut short after {len(head) + len(rest)} bytes")
    return head + rest


def format_value(value: Any) -> str:
    """Record value -> payload. Dates and other texts pass through."""
    if isinstance(value, (int, str)):
        return str(value)
    # pyMeterBus scales in binary floating point and returns the result as a
    # Decimal, noise included (1.128 arrives as 1.12800000000000011...).
    # Twelve significant digits keep every digit a meter sends.
    return f"{float(value):.12g}"


def flatten_telegram(telegram: bytes, topic_prefix: str, timestamp: str) -> Iterator[Measurement]:
    """Turn one data telegram into one measurement per record."""
    try:
        frame = meterbus.load(telegram)
    except MBusError as error:  # the checksum is verified here too
        raise MeterError(f"undecodable telegram ({error!r}): {telegram.hex()}") from error
    if not isinstance(frame, meterbus.TelegramLong):
        raise MeterError(f"not a data telegram: {telegram.hex()}")

    seen: set[str] = set()
    for record in frame.records:
        key = f"{bytes(record.dib.parts).hex().upper()}_{bytes(record.vib.parts).hex().upper()}"
        if key in seen:
            # Two values on one topic at one time would be one series with
            # contradicting points. Not expected from a conforming meter.
            logger.warning("record %s appears twice in %s -- second one skipped", key, topic_prefix)
            continue
        seen.add(key)
        try:
            payload = format_value(record.value)
        except Exception as error:  # one odd record must not cost the others
            logger.warning("record %s in %s cannot be decoded (%r) -- skipped", key, topic_prefix, error)
            continue
        yield Measurement(topic=f"{topic_prefix}/{key}", payload=payload, timestamp=timestamp)


# ---------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------

class MeasurementPublisher:
    """Publishes measurements with their time as an MQTT 5 user property."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            protocol=MQTTProtocolVersion.MQTTv5,
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    @staticmethod
    def _on_connect(client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code.is_failure:
            logger.error("MQTT connection failed: %s", reason_code)
        else:
            logger.info("Connected to the MQTT broker")

    @staticmethod
    def _on_disconnect(client, userdata, flags, reason_code, properties=None) -> None:
        logger.warning("MQTT connection lost: %s", reason_code)

    def connect(self) -> None:
        self._client.connect(self._settings.mqtt_host, self._settings.mqtt_port)
        self._client.loop_start()

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, measurement: Measurement) -> None:
        # Only the timestamp travels along; unit, type and name are master
        # data and live in the metrics table.
        properties = Properties(PacketTypes.PUBLISH)
        properties.UserProperty = [("timestamp", measurement.timestamp)]

        # retain=False: a retained value would be delivered again on every new
        # subscription and show up in the time series as a duplicate.
        self._client.publish(
            measurement.topic,
            payload=measurement.payload,
            qos=1,
            retain=False,
            properties=properties,
        )


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------

def run_once(publisher: MeasurementPublisher, settings: Settings) -> None:
    """Read every meter once and publish what it answered."""
    count = 0
    failed: list[str] = []
    # A meter has to start answering within 330 bit times plus 50 ms; after
    # that the longest frame needs 1.2 s at 2400 baud and 9.6 s at 300.
    # Whatever is not there by then is not coming.
    read_timeout = 0.5 + MAX_FRAME_BITS / settings.baudrate
    # Opened per pass: a port that went away (an unplugged adapter, a gateway
    # that restarted) is simply found again the next time round.
    with serial.serial_for_url(
        settings.serial_port,
        baudrate=settings.baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,  # M-Bus is always 8E1
        stopbits=serial.STOPBITS_ONE,
        timeout=read_timeout,
    ) as port:
        for name, address in settings.meters.items():
            try:
                telegram = read_telegram(port, address)
                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                measurements = list(flatten_telegram(
                    telegram,
                    f"{settings.mqtt_topic_prefix}/{name}",
                    timestamp.replace("+00:00", "Z"),
                ))
            except MeterError as error:
                logger.warning("Meter %s (address %d): %s", name, address, error)
                failed.append(name)
                continue
            for measurement in measurements:
                publisher.publish(measurement)
            count += len(measurements)

    logger.info(
        "%d values published from %d of %d meters",
        count, len(settings.meters) - len(failed), len(settings.meters),
    )

    # Healthy means: every meter answered AND the broker is there. The
    # connection is checked because publish() merely buffers in silence when
    # the broker is gone instead of raising.
    if failed:
        logger.warning("no answer from %s -- heartbeat skipped", ", ".join(failed))
    elif not publisher.is_connected:
        logger.warning("no MQTT connection -- heartbeat skipped")
    else:
        try:
            settings.heartbeat_file.touch()
        except OSError:
            logger.warning("Could not write the heartbeat", exc_info=True)


def main() -> int:
    # force: importing meterbus already configures the root logger.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        force=True,
    )
    settings = Settings()

    # An event rather than a flag: stop.wait() lets a signal cut any wait
    # short immediately, with no polling loop and no leftover latency.
    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        logger.info("Signal %s -- stopping after the current pass", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    # Say which rows the devices table needs. Without a matching device_key
    # the ingester drops everything.
    module = settings.mqtt_topic_prefix.rsplit("/", 1)[-1]
    for name, address in settings.meters.items():
        logger.info(
            "Meter %s at address %d -- device_key for the devices table: '%s/%s'",
            name, address, module, name,
        )

    publisher = MeasurementPublisher(settings)

    # When the stack starts up the broker may not be there yet. connect()
    # raises in that case, and without a retry the container dies at once.
    # After that paho handles reconnecting on its own.
    while not stop.is_set():
        try:
            publisher.connect()
            break
        except OSError as error:
            logger.warning("MQTT unreachable (%s) -- retrying in 5 s", error)
            stop.wait(5)

    try:
        while not stop.is_set():
            started_at = time.monotonic()
            try:
                run_once(publisher, settings)
            except (serial.SerialException, OSError) as error:
                # The port is missing, busy or gone mid-pass. Expected while
                # an adapter is unplugged, so one line and no traceback.
                logger.warning("Pass failed (%s) -- trying again next time", error)
            except Exception:
                logger.exception("Pass failed -- trying again next time")

            # Shorten the wait by the duration of the pass to keep a steady cadence.
            stop.wait(max(0.0, settings.poll_interval_seconds - (time.monotonic() - started_at)))
    finally:
        publisher.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
