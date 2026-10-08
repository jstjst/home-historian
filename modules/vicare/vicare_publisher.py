"""vicare module: ViCare cloud API -> MQTT (via PyViCare).

Logs in to the Viessmann cloud through PyViCare, picks ONE device (by its
device type, see below), flattens its properties and publishes every value as
a topic of its own:

    <prefix>/<gatewayId>/<deviceId>/<feature>/<property>[/<subkey>]

In the stack <prefix> is "historian/vicare". For the ingester that makes
"vicare/<gatewayId>/<deviceId>" the device_key and "<feature>.<property>" the
native_key.

The payload is the raw scalar. The time travels as the MQTT 5 user property
"timestamp". It is the feature timestamp reported by the API (when the value
came into being), not the time of the poll.

Why only ONE device: the metric key (native_key) does not depend on the device
(feature.property). Two devices would partly share the same metrics, and a
device id changed by the cloud would leave duplicate entries behind. The
choice therefore goes by device type, not by the volatile index or device id.

PyViCare takes care of persisting the token, renewing it when it expires and
raising typed errors (rate limit etc.).
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from PyViCare.PyViCare import PyViCare
from PyViCare.PyViCareUtils import (
    PyViCareDeviceCommunicationError,
    PyViCareInternalServerError,
    PyViCareRateLimitError,
)

logger = logging.getLogger("vicare_publisher")

# A pass that is still running after this long is stuck, not slow: a healthy
# one takes seconds, and one that fails on a dead network gives up after two
# to three minutes at the latest.
PASS_TIMEOUT_SECONDS = 300


class Settings(BaseSettings):
    """Configuration from the environment / .env."""

    model_config = SettingsConfigDict(env_prefix="vicare_", env_file=".env")

    # -- Credentials: environment/.env only, NO defaults -----------------
    # Secrets do not belong in tracked code. If one is missing pydantic
    # raises at startup, which beats running silently with wrong values.
    client_id: str
    email: str
    password: str

    # Which device to publish, identified by its type because that is stable.
    # 'heating' is the heat pump. Deliberately NOT the device id: if the cloud
    # changes it, the metrics would be left behind twice.
    device_type: str = "heating"

    # PyViCare pickles the OAuth session here (not just the refresh token).
    # Must point to a volume, otherwise the session is gone after every
    # restart and a needless fresh login follows.
    token_file: Path = Path("token.pickle")

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "historian/vicare"
    mqtt_client_id: str = "home-historian-vicare"

    poll_interval_seconds: int = Field(default=300, ge=60)

    # Touched after every successful pass. It only exists so the healthcheck
    # has something real to look at: the process can be alive and still not
    # have fetched anything for hours.
    heartbeat_file: Path = Path("heartbeat")


@dataclass(frozen=True, slots=True)
class Measurement:
    """One flattened property, ready to publish."""

    topic: str
    payload: str
    native_key: str
    unit: str | None
    value_type: str  # num | str | bool
    timestamp: str


# ---------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------

def normalize_unit(prop: dict[str, Any]) -> str | None:
    """An empty unit ('') means dimensionless, not 'missing' -> None."""
    unit = prop.get("unit")
    return unit if unit else None


def format_scalar(value: Any) -> tuple[str, str]:
    """Raw value -> (payload, value_type). Check bool before int!"""
    if isinstance(value, bool):
        return ("true" if value else "false"), "bool"
    if isinstance(value, (int, float)):
        return repr(value), "num"
    return str(value), "str"


def flatten_property(
    feature: str,
    name: str,
    prop: dict[str, Any],
    timestamp: str,
    prefix: str,
) -> Iterator[Measurement]:
    """Turn one API property into zero to n measurements.

    The API's 'type' is not enough to tell the cases apart: 'array' covers
    three disjoint shapes that need separate handling.
    """
    api_type = prop.get("type")
    value = prop.get("value")

    def build(payload: str, unit: str | None, value_type: str, subkey: str | None = None) -> Measurement:
        tail = f"{name}/{subkey}" if subkey else name
        key = f"{feature}.{name}" + (f".{subkey}" if subkey else "")
        return Measurement(
            topic=f"{prefix}/{feature}/{tail}",
            payload=payload,
            native_key=key,
            unit=unit,
            value_type=value_type,
            timestamp=timestamp,
        )

    if api_type == "number":
        yield build(repr(value), normalize_unit(prop), "num")

    elif api_type == "boolean":
        yield build("true" if value else "false", None, "bool")

    elif api_type == "string":
        yield build(str(value), None, "str")

    elif api_type == "array":
        unit = normalize_unit(prop)
        if unit is not None:
            # Look-back window (kilowattHour): index 0 is the current period,
            # higher indices shift as time passes. The history builds up in
            # the database, not here.
            if value:
                yield build(repr(value[0]), unit, "num")
        elif value and isinstance(value[0], dict):
            # Lists of objects (error messages, product matrix) cannot be
            # flattened; pass them through as a JSON string.
            yield build(json.dumps(value, separators=(",", ":")), None, "str")
        else:
            # Sets of scalars. The empty list is a statement too.
            # Must come last: [] has no value[0] to inspect.
            yield build(",".join(str(item) for item in value), None, "str")

    elif api_type == "object":
        for subkey, subvalue in (value or {}).items():
            payload, value_type = format_scalar(subvalue)
            yield build(payload, None, value_type, subkey)

    elif api_type == "Schedule":
        yield build(json.dumps(value, separators=(",", ":")), None, "str")

    else:
        logger.warning("unknown type %r in %s.%s -- skipped", api_type, feature, name)


def flatten_response(
    document: dict[str, Any],
    topic_prefix: str,
    seen_timestamps: dict[str, str] | None = None,
) -> Iterator[Measurement]:
    """Flatten a whole API response.

    Features without properties (everything with isEnabled=false, plus pure
    command features) carry no values and drop out silently.

    If 'seen_timestamps' is given, only features whose timestamp has changed
    since the last pass are returned, and the new state is recorded in it. The
    comparison is per feature, not against one global watermark: the cloud
    updates features independently of each other, so a watermark would
    silently discard values that arrive late.
    """
    for feature in document.get("data", []):
        properties = feature.get("properties")
        if not properties:
            continue

        name = feature["feature"]
        timestamp = feature["timestamp"]
        if seen_timestamps is not None:
            if seen_timestamps.get(name) == timestamp:
                continue
            seen_timestamps[name] = timestamp

        prefix = f"{topic_prefix}/{feature['gatewayId']}/{feature['deviceId']}"
        for property_name, prop in properties.items():
            yield from flatten_property(
                feature=name,
                name=property_name,
                prop=prop,
                timestamp=timestamp,
                prefix=prefix,
            )


# ---------------------------------------------------------------------
# API access (PyViCare)
# ---------------------------------------------------------------------

def _discard_unreadable_token(token_file: Path) -> None:
    """Delete a token file that cannot be loaded.

    PyViCare writes the file with open('wb'), which empties it at once, and on
    loading only catches UnpicklingError. If the write is interrupted an empty
    file stays behind, and its EOFError then makes EVERY login fail, for good,
    until someone deletes the file by hand. Without a file PyViCare simply
    logs in again.
    """
    if not token_file.is_file():
        return
    try:
        with token_file.open("rb") as handle:
            pickle.load(handle)
    except Exception as error:  # whatever the reason, the file is worthless
        logger.warning(
            "Token file %s is unreadable (%r) -- deleted, a fresh login follows",
            token_file, error,
        )
        token_file.unlink()


class ViCareClient:
    """Logs in through PyViCare and returns the feature document of one device.

    The login is deliberately lazy (on the first fetch): if the cloud is
    briefly down at startup the container does not die straight away but
    tries again on the next pass. PyViCare keeps the OAuth session in
    token_file (pickle), renews it when it expires and reports rate limits as
    PyViCareRateLimitError.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._device: Any = None

    def _select_device(self, devices: list[Any]) -> Any:
        """The (one) device of the wanted type -- stable, no index."""
        wanted = self._settings.device_type
        matches = [d for d in devices if d.device_type == wanted]
        if not matches:
            available = [(d.getModel(), d.device_type) for d in devices]
            raise RuntimeError(
                f"no device with device_type={wanted!r} found -- available: {available}"
            )
        if len(matches) > 1:
            logger.warning(
                "several devices with device_type=%r -- using the first (device_id=%s)",
                wanted, matches[0].getId(),
            )
        return matches[0]

    def _ensure_connected(self) -> None:
        if self._device is not None:
            return
        _discard_unreadable_token(self._settings.token_file)
        vicare = PyViCare()
        vicare.initWithCredentials(
            self._settings.email,
            self._settings.password,
            self._settings.client_id,
            str(self._settings.token_file),
        )
        for device in vicare.devices:
            logger.info(
                "Device found: model=%s type=%s device_id=%s",
                device.getModel(), device.device_type, device.getId(),
            )
        self._device = self._select_device(vicare.devices)
        logger.info(
            "Publishing device: model=%s type=%s device_id=%s",
            self._device.getModel(), self._device.device_type, self._device.getId(),
        )

    def fetch_features(self) -> dict[str, Any]:
        """Raw feature document of the selected device ({"data": [...]})."""
        self._ensure_connected()
        return self._device.get_raw_json()


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
        # Only the timestamp travels along. The native_key can be rebuilt from
        # the topic ('/' -> '.'); value type and unit are master data and live
        # in the metrics table. Sending them here as well would create a
        # second, competing source of truth.
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

def run_once(
    client: ViCareClient,
    publisher: MeasurementPublisher,
    settings: Settings,
    seen_timestamps: dict[str, str],
) -> int:
    document = client.fetch_features()
    if not seen_timestamps:
        # First pass: say which row the devices table needs. Without a
        # matching device_key the ingester drops everything.
        for device in sorted({f"{f['gatewayId']}/{f['deviceId']}" for f in document.get("data", [])}):
            logger.info(
                "Topics under %s/%s/ -- device_key for the devices table: "
                "that path without the stack prefix (e.g. 'vicare/%s')",
                settings.mqtt_topic_prefix, device, device,
            )
    count = 0
    for measurement in flatten_response(document, settings.mqtt_topic_prefix, seen_timestamps):
        publisher.publish(measurement)
        count += 1
    logger.info("%d measurements published (%d features known)", count, len(seen_timestamps))

    # Only now is the pass really complete (fetch + publish). The connection
    # is checked as well because publish() merely buffers in silence when the
    # broker is dead instead of raising. A heartbeat without this check would
    # report "healthy" while nothing arrives.
    if publisher.is_connected:
        try:
            settings.heartbeat_file.touch()
        except OSError:
            logger.warning("Could not write the heartbeat", exc_info=True)
    else:
        logger.warning("no MQTT connection -- heartbeat skipped")

    return count


def abort_hung_pass() -> None:
    """Watchdog: end the process so that the restart policy starts it afresh.

    PyViCare sends the login and the token renewal without a timeout. If the
    other side accepts the connection and then stays silent, the main thread
    waits in that read for good. os._exit, because sys.exit in the timer
    thread would only end that thread.
    """
    logger.critical(
        "Pass still running after %d s -- exiting so the container restarts",
        PASS_TIMEOUT_SECONDS,
    )
    os._exit(1)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
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

    client = ViCareClient(settings)
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

    # In memory only: a restart deliberately publishes everything again. That
    # doubles as the safety net should the database ever be rebuilt.
    seen_timestamps: dict[str, str] = {}

    try:
        while not stop.is_set():
            started_at = time.monotonic()
            wait_seconds = float(settings.poll_interval_seconds)
            watchdog = threading.Timer(PASS_TIMEOUT_SECONDS, abort_hung_pass)
            watchdog.daemon = True
            watchdog.start()
            try:
                run_once(client, publisher, settings, seen_timestamps)
            except PyViCareRateLimitError as error:
                # Polling on blindly would only push the quota further into
                # the red; wait until the reported reset instead.
                # limitResetDate is naive UTC (PyViCare uses utcfromtimestamp).
                logger.error("%s", error)
                reset_at = error.limitResetDate.replace(tzinfo=timezone.utc)
                until_reset = (reset_at - datetime.now(timezone.utc)).total_seconds()
                wait_seconds = max(wait_seconds, until_reset + 5)
                logger.warning("waiting %.0f s for the quota to reset", wait_seconds)
            except (PyViCareInternalServerError, PyViCareDeviceCommunicationError) as error:
                # The cloud, the way to it or the gateway is down. PyViCare
                # wraps every network error of a fetch in the first one
                # (status code 0). Expected, so one line and no traceback.
                logger.warning("Pass failed (%s) -- trying again next time", error.message)
            except OSError as error:
                # Login and token renewal are NOT wrapped by PyViCare: their
                # network errors arrive as plain requests exceptions, which
                # are OSErrors. Their text quotes the login URL; the query
                # string is cut because it carries the client id.
                logger.warning(
                    "Pass failed (%s) -- trying again next time",
                    re.sub(r"\?\S+", "", str(error)),
                )
            except Exception:
                logger.exception("Pass failed -- trying again next time")
            finally:
                watchdog.cancel()

            # Shorten the wait by the duration of the pass to keep a steady cadence.
            stop.wait(max(0.0, wait_seconds - (time.monotonic() - started_at)))
    finally:
        publisher.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
