"""senec module: SENEC.Home battery storage (local lala.cgi API) -> MQTT.

Asks the unit for every section of its local API and publishes every value as
a topic of its own:

    <prefix>/<device>/<SECTION>/<FIELD>[/<index>]

In the stack <prefix> is "historian/senec". For the ingester that makes
"senec/<device>" the device_key and "<SECTION>.<FIELD>[.<index>]" the
native_key. Section and field are the unit's own names. A field that holds an
array (phases, PV strings, battery modules, cells) becomes one topic per
element, numbered from 0 as in the unit's answer.

The payload is the value as the unit sends it, decoded from its typed hex
notation and nothing more: no scaling, no renaming, no derived values. All
values of one pass share one time, the moment the answer arrived, sent as the
MQTT 5 user property "timestamp".

The module only ever reads. In lala.cgi a field sent with a value is a write;
every request here sends empty sections ({}) or an empty string.
"""

from __future__ import annotations

import logging
import math
import re
import signal
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterator

import paho.mqtt.client as mqtt
import requests
import urllib3
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger("senec_publisher")

# Connect, then the answer. The unit answers within half a second; whatever
# takes this long is not coming.
REQUEST_TIMEOUT = (5, 20)

# What a section or field name has to look like to become a topic segment.
NAME = re.compile(r"[A-Za-z0-9_]+")

# While the broker is away paho keeps every message in memory, without a limit
# unless given one, and a queued message costs it several kilobytes. A pass is
# about a thousand messages; this holds five passes (some 30 MB), enough for a
# restart of the broker, and drops what comes after. What is queued is sent,
# with its own time, once the broker is back.
MAX_QUEUED_MESSAGES = 5_000


class Settings(BaseSettings):
    """Configuration from the environment."""

    model_config = SettingsConfigDict(env_prefix="senec_")

    # Scheme and address of the unit, e.g. https://192.168.1.50. No default:
    # the address belongs to the installation.
    url: str

    # Topic segment of the unit, and with it the device_key "senec/<device>".
    device: str = Field(default="home", pattern=r"^[A-Za-z0-9_-]+$")

    # Sections to read, comma separated. Empty or "all": every section the
    # unit lists (asked anew on every pass, so a firmware update that adds
    # one needs no restart).
    sections: Annotated[list[str], NoDecode] = []

    # No limit is documented. The unit refreshes its values every second and
    # a pass takes about one; 5 s leaves its web server mostly idle.
    poll_interval_seconds: int = Field(default=60, ge=5)

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "historian/senec"
    mqtt_client_id: str = "home-historian-senec"

    # Touched after every complete pass. It only exists so the healthcheck
    # has something real to look at.
    heartbeat_file: Path = Path("heartbeat")

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        if not re.fullmatch(r"https?://[^/]+/?", value):
            raise ValueError(f"expected http(s)://<address>, got {value!r}")
        return value.rstrip("/")

    @field_validator("sections", mode="before")
    @classmethod
    def parse_sections(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        sections = [part.strip() for part in value.split(",") if part.strip()]
        if [section.lower() for section in sections] == ["all"]:
            return []
        for section in sections:
            if not NAME.fullmatch(section):
                raise ValueError(f"not a section name: {section!r}")
        return sections


@dataclass(frozen=True, slots=True)
class Measurement:
    """One value, ready to publish."""

    topic: str
    payload: str
    timestamp: str


class SenecError(Exception):
    """The unit did not answer, or not with what was asked for."""


# ---------------------------------------------------------------------
# lala.cgi
# ---------------------------------------------------------------------

class Senec:
    """The unit's local API: one POST of JSON, one JSON answer."""

    def __init__(self, settings: Settings) -> None:
        self._url = f"{settings.url}/lala.cgi"
        self._session: requests.Session | None = None

    def read(self, form: dict[str, Any]) -> dict[str, Any]:
        """Send one request and return the answer, undecoded."""
        if self._session is None:
            # One session for all passes: it keeps the connection open and
            # returns the cookie the unit sets anew with every answer. Without
            # it every request would look like a new visitor to the unit.
            self._session = requests.Session()
            # The unit's certificate cannot be verified: SENEC issues it for
            # the name "senecGuiSelf" from a root of its own.
            self._session.verify = False
        try:
            response = self._session.post(self._url, json=form, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            answer = response.json()
        except requests.RequestException as error:
            # Start over next time: a session the unit no longer knows, or a
            # connection it dropped, must not outlive the failure.
            self.close()
            if isinstance(error, requests.JSONDecodeError):
                # Also what an empty body is, which the unit sends with
                # status 200 when it could not read the request.
                raise SenecError(f"the answer is not JSON: {response.text!r:.100}") from error
            raise SenecError(f"{type(error).__name__}: {error}") from error
        if not isinstance(answer, dict):
            raise SenecError(f"unexpected answer: {answer!r:.200}")
        return answer

    def sections(self) -> list[str]:
        """Every section this unit has, as it lists them itself."""
        answer = self.read({"DEBUG": {"SECTIONS": ""}})
        try:
            sections = [decode_value(item) for item in answer["DEBUG"]["SECTIONS"]]
        except (KeyError, TypeError):
            sections = []
        if not sections or None in sections:
            raise SenecError(f"no list of sections in {answer!r:.200}")
        return sections

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


def format_float(raw: bytes) -> str | None:
    """Four bytes of IEEE 754 single precision -> payload.

    The shortest decimal text that reads back as the same single: 524.77, not
    524.77001953125. None for infinity and NaN, which are no measurements.
    """
    (value,) = struct.unpack(">f", raw)
    if not math.isfinite(value):
        return None
    value += 0.0  # the unit sends -0.0 for "nothing"
    # A single has six to nine significant digits.
    for digits in range(6, 10):
        text = f"{value:.{digits}g}"
        if struct.unpack(">f", struct.pack(">f", float(text)))[0] == value:
            break
    return text


def decode_value(raw: Any) -> str | None:
    """One value in the unit's notation -> payload. None if it is not one.

    The unit sends "<type>_<value>": fl (float), u8/u1/u3/u6 (unsigned, 8 to
    64 bits), i8/i1/i3/i6 (signed) and st (text). Numbers are hex, floats the
    hex of their four bytes. Anything else is not a value, in particular the
    unit's "VARIABLE_NOT_FOUND".
    """
    if not isinstance(raw, str):
        return None
    kind, separator, text = raw.partition("_")
    if not separator:
        return None
    if kind == "st":
        return text
    try:
        if kind == "fl":
            return format_float(bytes.fromhex(text))
        if kind in ("u8", "u1", "u3", "u6"):
            return str(int(text, 16))
        if kind in ("i8", "i1", "i3", "i6"):
            # Two's complement over the digits sent: i3_FFFFFFFF is -1.
            number = int(text, 16)
            bits = 4 * len(text)
            return str(number - (1 << bits) if number >> (bits - 1) else number)
    except (ValueError, struct.error):
        return None
    return None


def flatten(
    answer: dict[str, Any], topic_prefix: str, timestamp: str, unreadable: set[str],
) -> Iterator[Measurement]:
    """Turn one answer into one measurement per value.

    unreadable collects the keys already reported, so that a value the module
    cannot publish is logged once and not on every pass.
    """
    for section, fields in answer.items():
        if not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            key = f"{section}/{field}"
            # A name with a slash or a wildcard in it would make another topic.
            usable = NAME.fullmatch(section) and NAME.fullmatch(field)
            # A list is one value per element; nothing in the unit's answers
            # nests deeper than that.
            if isinstance(value, list):
                items = [(f"{key}/{index}", item) for index, item in enumerate(value)]
            else:
                items = [(key, value)]
            for key, raw in items:
                payload = decode_value(raw) if usable else None
                if payload is None:
                    if key not in unreadable:
                        unreadable.add(key)
                        logger.warning("%s = %r cannot be published -- skipped (reported once)", key, raw)
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
        self._client.max_queued_messages_set(MAX_QUEUED_MESSAGES)
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

    def publish(self, measurement: Measurement) -> bool:
        """Hand one measurement to the client. False if its queue is full."""
        # Only the timestamp travels along; unit, type and name are master
        # data and live in the metrics table.
        properties = Properties(PacketTypes.PUBLISH)
        properties.UserProperty = [("timestamp", measurement.timestamp)]

        # retain=False: a retained value would be delivered again on every new
        # subscription and show up in the time series as a duplicate.
        info = self._client.publish(
            measurement.topic,
            payload=measurement.payload,
            qos=1,
            retain=False,
            properties=properties,
        )
        return info.rc != mqtt.MQTT_ERR_QUEUE_SIZE


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------

def run_once(senec: Senec, publisher: MeasurementPublisher, settings: Settings, unreadable: set[str]) -> None:
    """Read the unit once and publish what it answered."""
    sections = settings.sections or senec.sections()
    answer = senec.read({section: {} for section in sections})
    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    measurements = list(flatten(
        answer,
        f"{settings.mqtt_topic_prefix}/{settings.device}",
        timestamp.replace("+00:00", "Z"),
        unreadable,
    ))
    if not measurements:
        raise SenecError("the answer holds no values")

    dropped = sum(not publisher.publish(measurement) for measurement in measurements)
    logger.info("%d values published from %d sections", len(measurements) - dropped, len(answer))

    # Healthy means: the unit answered AND the broker is there. The connection
    # is checked because publish() merely queues in silence when the broker is
    # gone instead of raising.
    if dropped:
        logger.warning("%d values dropped, the queue for the broker is full -- heartbeat skipped", dropped)
    elif not publisher.is_connected:
        logger.warning("no MQTT connection -- heartbeat skipped")
    else:
        try:
            settings.heartbeat_file.touch()
        except OSError:
            logger.warning("Could not write the heartbeat", exc_info=True)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    settings = Settings()

    # urllib3 warns about the unverified certificate on every single request.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # An event rather than a flag: stop.wait() lets a signal cut any wait
    # short immediately, with no polling loop and no leftover latency.
    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        logger.info("Signal %s -- stopping after the current pass", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    # Say which row the devices table needs. Without a matching device_key
    # the ingester drops everything.
    module = settings.mqtt_topic_prefix.rsplit("/", 1)[-1]
    logger.info(
        "Reading %s -- device_key for the devices table: '%s/%s'",
        settings.url, module, settings.device,
    )

    senec = Senec(settings)
    publisher = MeasurementPublisher(settings)
    unreadable: set[str] = set()

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
                run_once(senec, publisher, settings, unreadable)
            except SenecError as error:
                # Expected while the unit restarts or updates itself, so one
                # line and no traceback.
                logger.warning("Pass failed (%s) -- trying again next time", error)
            except Exception:
                logger.exception("Pass failed -- trying again next time")

            # Shorten the wait by the duration of the pass to keep a steady cadence.
            stop.wait(max(0.0, settings.poll_interval_seconds - (time.monotonic() - started_at)))
    finally:
        senec.close()
        publisher.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
