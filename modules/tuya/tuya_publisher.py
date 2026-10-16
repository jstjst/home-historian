"""tuya module: Tuya devices (Smart Life cloud) -> MQTT.

Logs in to the Smart Life cloud with tuya-device-sharing-sdk, the SDK of the
Home Assistant integration, and listens to the values Tuya's cloud pushes for
the devices of the account. Every value becomes a topic of its own:

    <prefix>/<device id>/<code>

In the stack <prefix> is "historian/tuya". For the ingester that makes
"tuya/<device id>" the device_key and Tuya's data point code (va_temperature,
va_humidity, battery_percentage, ...) the native_key. The device id is the
one Tuya knows the device by; its name belongs in the devices table. Every
device of the account is published, or those listed in TUYA_DEVICES.

Tuya sends integers together with a scale that differs between products
(va_humidity 618 with scale 1 on one sensor, 54 with scale 0 on another).
The module applies the scale from the device's specification; nothing else
is converted. The time of measurement comes with the push (per value, in
milliseconds) and is sent as the MQTT 5 user property "timestamp".

Only pushed values are published. The status the cloud hands out at startup
carries no time of measurement and would be stored with the wrong one.

The SDK has no setting for the app it logs in as: it needs the client id and
schema Tuya issued for one (TUYA_CLIENT_ID, TUYA_SCHEMA), see the README. The
login itself (QR code, Smart Life app) is done once with tuya_login.py. The
module only ever reads; it sends no commands.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion, MQTTProtocolVersion
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from tuya_sharing import CustomerDevice, Manager, SharingDeviceListener, SharingTokenListener

logger = logging.getLogger("tuya_publisher")

# What a device id or a data point code has to look like to become a topic
# segment.
NAME = re.compile(r"[A-Za-z0-9_-]+")

# How often the connections are checked and the heartbeat is touched.
HEARTBEAT_SECONDS = 30


class Settings(BaseSettings):
    """Configuration from the environment."""

    model_config = SettingsConfigDict(env_prefix="tuya_")

    # The app the SDK logs in as. No default: which app to present to Tuya is
    # the operator's decision (README, "Prerequisites").
    client_id: str = Field(min_length=1)
    # "schema" would shadow a pydantic attribute, hence the other field name.
    app_schema: str = Field(min_length=1, validation_alias="tuya_schema")

    # Device ids to publish, comma separated. Empty or "all": every device of
    # the account.
    devices: Annotated[list[str], NoDecode] = []

    # Login and tokens (tuya_login.py writes it, the module renews it). MUST
    # be on a volume: without it every recreated container needs a new login.
    token_file: Path = Path("token.json")

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "historian/tuya"
    mqtt_client_id: str = "home-historian-tuya"

    # Touched while both connections are up. It only exists so the
    # healthcheck has something real to look at.
    heartbeat_file: Path = Path("heartbeat")

    @field_validator("devices", mode="before")
    @classmethod
    def parse_devices(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        devices = [part.strip() for part in value.split(",") if part.strip()]
        if [device.lower() for device in devices] == ["all"]:
            return []
        for device in devices:
            if not NAME.fullmatch(device):
                raise ValueError(f"not a device id: {device!r}")
        return devices


@dataclass(frozen=True, slots=True)
class Measurement:
    """One value, ready to publish."""

    topic: str
    payload: str
    # None: the push carried no time; the broker stamps the message.
    timestamp: str | None


# ---------------------------------------------------------------------
# Login and tokens
# ---------------------------------------------------------------------

@dataclass(slots=True)
class Login:
    """What tuya_login.py stores: the account and its current tokens."""

    user_code: str
    terminal_id: str
    endpoint: str
    token_info: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> Login | None:
        """The stored login, or None if there is none (yet)."""
        try:
            data = json.loads(path.read_text())
            return cls(data["user_code"], data["terminal_id"], data["endpoint"], data["token_info"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.error("Cannot read %s (%s) -- log in again with tuya_login.py", path, error)
            return None

    def save(self, path: Path) -> None:
        """Write the file in one step: a half-written token file loses the login."""
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({
            "user_code": self.user_code,
            "terminal_id": self.terminal_id,
            "endpoint": self.endpoint,
            "token_info": self.token_info,
        }))
        temporary.chmod(0o600)
        os.replace(temporary, path)


class TokenSaver(SharingTokenListener):
    """Stores the tokens the SDK renews, so that a restart needs no new login."""

    def __init__(self, login: Login, path: Path) -> None:
        self._login = login
        self._path = path

    def update_token(self, token_info: dict[str, Any]) -> None:
        self._login.token_info = {
            key: token_info[key] for key in ("t", "uid", "expire_time", "access_token", "refresh_token")
        }
        try:
            self._login.save(self._path)
        except OSError:
            logger.error("Could not store the renewed tokens in %s", self._path, exc_info=True)


def make_manager(settings: Settings, login: Login) -> Manager:
    return Manager(
        settings.client_id,
        login.user_code,
        login.terminal_id,
        login.endpoint,
        login.token_info,
        TokenSaver(login, settings.token_file),
    )


# ---------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------

def integer_scale(device: CustomerDevice, code: str) -> int:
    """Decimal places of an Integer data point, from the device's specification."""
    status_range = device.status_range.get(code)
    if status_range is None or status_range.type != "Integer":
        return 0
    values = status_range.values
    try:
        if isinstance(values, str):
            values = json.loads(values)
        return int(values.get("scale", 0))
    except (ValueError, TypeError, AttributeError):
        return 0


def format_value(device: CustomerDevice, code: str, value: Any) -> str | None:
    """One value as Tuya sends it -> payload. None if there is nothing to publish."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        scale = integer_scale(device, code)
        # Decimal keeps 185 with scale 1 at exactly 18.5.
        return f"{Decimal(value).scaleb(-scale):f}" if scale > 0 else str(value)
    if isinstance(value, (float, str)):
        return str(value)
    # A Json data point: kept whole, stored as text.
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def format_time(milliseconds: Any) -> str | None:
    """Tuya's time of a value (Unix milliseconds) -> ISO 8601 in UTC."""
    if not isinstance(milliseconds, (int, float)) or milliseconds <= 0:
        return None
    moment = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------

class MeasurementPublisher:
    """Publishes measurements, with their time as an MQTT 5 user property."""

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
        properties = None
        if measurement.timestamp is not None:
            # Only the timestamp travels along; unit, type and name are
            # master data and live in the metrics table.
            properties = Properties(PacketTypes.PUBLISH)
            properties.UserProperty = [("timestamp", measurement.timestamp)]

        # retain=False: a retained value would be delivered again on every new
        # subscription and show up in the time series as a duplicate. The few
        # values an hour need no queue limit while the broker is away.
        self._client.publish(
            measurement.topic,
            payload=measurement.payload,
            qos=1,
            retain=False,
            properties=properties,
        )


# ---------------------------------------------------------------------
# Tuya -> MQTT
# ---------------------------------------------------------------------

class DeviceListener(SharingDeviceListener):
    """Called by the SDK, from its MQTT thread, for every push."""

    def __init__(self, settings: Settings, publisher: MeasurementPublisher) -> None:
        self._selected = set(settings.devices)
        self._prefix = settings.mqtt_topic_prefix
        self._publisher = publisher
        # Codes that cannot be a topic segment, reported once.
        self._unusable: set[str] = set()

    def update_device(
        self,
        device: CustomerDevice,
        updated_status_properties: list[str] | None = None,
        dp_timestamps: dict[str, int] | None = None,
    ) -> None:
        if not selected(device.id, self._selected) or not NAME.fullmatch(device.id):
            return
        topic_prefix = f"{self._prefix}/{device.id}"

        # Without a list of codes the push was an online/offline event (or a
        # renamed device). The time is the broker's: Tuya sends none for it.
        if updated_status_properties is None:
            self._publisher.publish(Measurement(
                f"{topic_prefix}/online", "true" if device.online else "false", None,
            ))
            return

        timestamps = dp_timestamps or {}
        for code in updated_status_properties:
            if not NAME.fullmatch(code):
                if code not in self._unusable:
                    self._unusable.add(code)
                    logger.warning("%s: code %r cannot be a topic -- skipped (reported once)", device.id, code)
                continue
            payload = format_value(device, code, device.status.get(code))
            if payload is None:
                continue
            self._publisher.publish(Measurement(
                f"{topic_prefix}/{code}", payload, format_time(timestamps.get(code)),
            ))

    def add_device(self, device: CustomerDevice) -> None:
        logger.info("Device added to the account: %s (%s, %s)", device.id, device.name, device.category)

    def remove_device(self, device_id: str) -> None:
        logger.warning("Device removed from the account: %s", device_id)


def load_devices(manager: Manager, settings: Settings, stop: threading.Event) -> None:
    """Fetch the account's devices, retrying until it works or the module stops."""
    delay = 10
    while not stop.is_set():
        try:
            manager.update_device_cache()
            return
        except Exception as error:
            # The SDK raises ApiRequestException for a refused request (an
            # expired login among them) and AttributeError for an HTTP error.
            logger.warning(
                "Loading the devices failed (%s: %s) -- retrying in %d s. "
                "If the login has expired, run tuya_login.py again.",
                type(error).__name__, error, delay,
            )
            stop.wait(delay)
            delay = min(delay * 2, 300)


def selected(device_id: str, devices: set[str]) -> bool:
    """Whether a device is published: all of them unless TUYA_DEVICES lists some."""
    return not devices or device_id in devices


def report_devices(manager: Manager, settings: Settings) -> int:
    """Log the account's devices and which are published. Returns that number."""
    module = settings.mqtt_topic_prefix.rsplit("/", 1)[-1]
    devices = set(settings.devices)
    for device_id in devices - manager.device_map.keys():
        logger.error("Device id %s from TUYA_DEVICES is not in the account", device_id)
    published = 0
    for device in manager.device_map.values():
        if not NAME.fullmatch(device.id):
            logger.error("Device id %r cannot be a topic -- not published", device.id)
            continue
        if selected(device.id, devices):
            published += 1
            logger.info(
                "Publishing %s (%s, %s), device_key '%s/%s', online %s",
                device.name, device.category, device.product_name, module, device.id, device.online,
            )
        else:
            logger.info("Not in TUYA_DEVICES: %s (%s, %s)", device.id, device.name, device.category)
    return published


def tuya_connected(manager: Manager) -> bool:
    mq = manager.mq
    return mq is not None and mq.client is not None and mq.client.is_connected()


def wait_for_login(settings: Settings, stop: threading.Event) -> Login | None:
    """The stored login. Without one, wait: tuya_login.py can run beside the service."""
    reported = False
    while not stop.is_set():
        login = Login.load(settings.token_file)
        if login is not None:
            return login
        if not reported:
            logger.error(
                "No login in %s -- run: docker compose run --rm tuya python tuya_login.py",
                settings.token_file,
            )
            reported = True
        stop.wait(30)
    return None


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # The SDK brings a handler of its own; without removing it every line of
    # it would be logged twice.
    logging.getLogger("tuya_sharing").handlers.clear()
    settings = Settings()

    # An event rather than a flag: stop.wait() lets a signal cut any wait
    # short immediately.
    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        logger.info("Signal %s -- stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    publisher = MeasurementPublisher(settings)
    # When the stack starts up the broker may not be there yet. After the
    # first connect paho handles reconnecting on its own.
    while not stop.is_set():
        try:
            publisher.connect()
            break
        except OSError as error:
            logger.warning("MQTT unreachable (%s) -- retrying in 5 s", error)
            stop.wait(5)

    manager: Manager | None = None
    try:
        login = wait_for_login(settings, stop)
        if login is None:
            return 0
        manager = make_manager(settings, login)
        manager.add_device_listener(DeviceListener(settings, publisher))
        load_devices(manager, settings, stop)
        if stop.is_set():
            return 0
        found = report_devices(manager, settings)

        # The SDK subscribes only to devices whose "set_up" is true. Tuya
        # reports false for some (a WiFi T&H sensor among them), and their
        # pushes would never arrive. Its meaning is not documented.
        devices = set(settings.devices)
        for device in manager.device_map.values():
            if selected(device.id, devices):
                device.set_up = True

        # From here on the SDK's thread receives the pushes, renews the
        # connection every two hours and reconnects on its own.
        manager.refresh_mq()

        healthy = None
        while not stop.wait(HEARTBEAT_SECONDS):
            # Healthy means: a device to publish exists AND both connections
            # are up. publish() queues in silence while the broker is gone,
            # and the SDK reconnects to Tuya without telling anyone.
            problems = [
                problem for problem, failed in (
                    ("no device to publish", not found),
                    ("no MQTT connection", not publisher.is_connected),
                    ("no connection to Tuya's cloud", not tuya_connected(manager)),
                ) if failed
            ]
            if not problems:
                try:
                    settings.heartbeat_file.touch()
                except OSError:
                    logger.warning("Could not write the heartbeat", exc_info=True)
            # Log changes only, not every 30 seconds.
            if healthy is None or healthy != (not problems):
                if problems:
                    logger.warning("%s -- heartbeat skipped", ", ".join(problems))
                else:
                    logger.info("Connected to Tuya's cloud, waiting for values")
            healthy = not problems
    finally:
        if manager is not None and manager.mq is not None:
            manager.mq.stop()
        publisher.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
