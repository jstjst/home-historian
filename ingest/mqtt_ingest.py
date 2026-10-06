"""
home-historian ingester: MQTT -> TimescaleDB.

What happens to one message:

    topic:   <prefix>/<device_key...>/<native_key, with / instead of .>
    payload: one scalar value (number / bool / text)
    props:   MQTT 5 user property "timestamp" (ISO 8601): the time of
             measurement if the module sent one, otherwise the time the broker
             received the message (message-timestamp plugin)

    Example: historian/open3e/vitocal/268_FlowTemperatureSensor/SensorStatus/ID
             -> device_key 'open3e/vitocal',
                native_key '268_FlowTemperatureSensor.SensorStatus.ID'

    1. Read the time from the timestamp property; drop the message if absent.
    2. Find the device by longest-prefix match on the topic.
    3. Whatever follows the device_key becomes the native_key ('/' -> '.').
    4. Resolve (device type, native_key) to a metric; unknown keys are created
       on the fly as text-typed stubs.
    5. Put the value into value_num / value_str / value_bool.
    6. Write the row in a batch.
    7. Acknowledge only AFTER the commit (QoS 1, manual_ack).

Delivery guarantee: the broker is the buffer. When the database is unreachable
the ingester disconnects from the broker, which keeps collecting in the
persistent session (QoS 1 by itself, QoS 0 only with queue_qos0_messages true;
open3e publishes at QoS 0). Redelivered messages (DUP flag) are written only if
their row is still missing.

Writing stays lean on purpose (metric_id + device_id + value). Resolving a
metric is cached, so it costs a database round trip only once per key.
"""

from __future__ import annotations

import logging
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg
import pygtrie
from cachetools import TTLCache
from dateutil import parser as datetime_parser
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.subscribeoptions import SubscribeOptions
from psycopg_pool import ConnectionPool
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger("mqtt_ingest")


# =====================================================================
# Configuration (environment variables / .env)
# =====================================================================
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="INGEST_")

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_filter: str = "historian/#"
    mqtt_topic_prefix: str = "historian"  # stripped before the device lookup
    mqtt_client_id: str = "home-historian-ingest"  # keep stable: it anchors the persistent session

    # How long the broker keeps undelivered messages for this client. This is
    # the longest outage (database, network, ingester) that loses no data.
    # Buffered messages keep their original timestamp property.
    mqtt_session_expiry_seconds: int = 604_800  # 7 days

    # Name of the MQTT 5 user property carrying the time of measurement
    # (ISO 8601). The Mosquitto message-timestamp plugin sets it on receipt; a
    # module that knows the time better sends its own. Messages without it are
    # dropped: there is no trustworthy time to store them under.
    mqtt_timestamp_property: str = "timestamp"

    database_url: str = "postgresql://localhost/historian"

    # Batching of the write path
    batch_max_rows: int = 500
    batch_max_seconds: float = 2.0

    # Pause between attempts while the database or the broker is unreachable.
    retry_seconds: float = 30.0

    # Lifetime of a cached metric. Once it expires the metric is looked up
    # again, which is how a manually changed value_type takes effect (restart
    # the ingester to apply it immediately). Every expiry costs one database
    # round trip and a new row version in metrics, because the upsert always
    # writes. Keep it well above the interval between values, or the cache
    # never hits: ViCare features only arrive on change, minutes to hours apart.
    metric_cache_seconds: float = 3600


# =====================================================================
# Value objects
# =====================================================================
@dataclass(frozen=True)
class ResolvedDevice:
    device_id: int
    device_type: str


@dataclass(frozen=True)
class ResolvedMetric:
    metric_id: int
    value_type: str  # 'num' | 'str' | 'bool'


@dataclass(frozen=True)
class Pending:
    """A message that has been received but not written yet."""

    time: datetime
    device: ResolvedDevice
    native_key: str
    raw_value: str
    mid: int   # mid + qos are needed to acknowledge after the commit
    qos: int
    dup: bool  # redelivered by the broker, so its row may already exist


# =====================================================================
# Device registry: topic prefix -> device, by longest-prefix match
#   Device keys go into a trie as path segments, so a key may span several
#   segments ('open3e/vitocal') and the most specific one wins.
# =====================================================================
class DeviceRegistry:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._trie: pygtrie.StringTrie = pygtrie.StringTrie(separator="/")

    def reload(self) -> None:
        trie: pygtrie.StringTrie = pygtrie.StringTrie(separator="/")
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT device_key, device_id, type FROM devices"
            ).fetchall()
        for device_key, device_id, device_type in rows:
            trie[device_key] = ResolvedDevice(device_id=device_id, device_type=device_type)
        self._trie = trie
        logger.info("Device registry loaded: %d devices", len(rows))

    def resolve(self, topic_without_prefix: str) -> tuple[ResolvedDevice, str] | None:
        """Return (device, rest of the topic after its key), or None if no device matches."""
        step = self._trie.longest_prefix(topic_without_prefix)
        if not step:
            return None
        device: ResolvedDevice = step.value
        matched_key: str = step.key
        remainder = topic_without_prefix[len(matched_key):].lstrip("/")
        return device, remainder


# =====================================================================
# Metric resolution: (device type, native_key) -> metric_id, value_type
#   A TTL cache in front of an upsert that ALWAYS returns a metric_id and
#   creates unknown keys as stubs (value_type = 'str').
# =====================================================================
class MetricResolver:
    def __init__(self, pool: ConnectionPool, cache_seconds: float) -> None:
        self._pool = pool
        self._cache: TTLCache[tuple[str, str], ResolvedMetric] = TTLCache(
            maxsize=10_000, ttl=cache_seconds
        )
        self._lock = threading.Lock()

    def resolve(self, device_type: str, native_key: str) -> ResolvedMetric:
        cache_key = (device_type, native_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        with self._lock:
            # Double-checked locking: another thread may have resolved the key
            # while this one was waiting for the lock.
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            resolved = self._resolve_in_database(device_type, native_key)
            self._cache[cache_key] = resolved
            return resolved

    def _resolve_in_database(self, device_type: str, native_key: str) -> ResolvedMetric:
        # An unknown key becomes a stub: display_name = native_key and
        # value_type = 'str', so nothing is lost until someone curates it. A
        # known key returns the value_type it was given. DO UPDATE is a no-op
        # whose only job is to make RETURNING fire for existing rows.
        upsert = """
            INSERT INTO metrics (type, native_key, display_name, value_type)
            VALUES (%s, %s, %s, 'str')
            ON CONFLICT (type, native_key) DO UPDATE
                SET native_key = EXCLUDED.native_key
            RETURNING metric_id, value_type
        """
        with self._pool.connection() as connection:
            row = connection.execute(
                upsert, (device_type, native_key, native_key)
            ).fetchone()
        metric_id, value_type = row
        return ResolvedMetric(metric_id=metric_id, value_type=value_type)


# =====================================================================
# Rest of the topic after the device_key -> native_key
#   Modules publish flattened leaves, e.g. open3e:
#     .../268_FlowTemperatureSensor/Actual
#     .../268_FlowTemperatureSensor/SensorStatus/ID
#   The rest of the topic is taken as it is, with '/' turned into '.'. It is
#   not split any further: the native_key is the complete lookup key, and
#   analysis runs on display_name + display_subname, not on raw segments.
# =====================================================================
def build_native_key(remainder: str) -> str | None:
    segments = [segment for segment in remainder.split("/") if segment]
    if not segments:
        return None
    return ".".join(segments)


# =====================================================================
# ISO 8601 timestamp from the user property -> datetime
#   A value that cannot be parsed counts as "no time" (None), and the
#   message is dropped.
# =====================================================================
def parse_iso_timestamp(value: str) -> datetime | None:
    try:
        text = value.strip()
        if text.endswith("Z"):  # dateutil would read a bare 'Z' as local time
            text = text[:-1] + "+00:00"
        parsed = datetime_parser.parse(text)
    except (ValueError, OverflowError, TypeError):
        logger.warning("Unreadable timestamp property, dropping message: %r", value)
        return None
    if parsed.tzinfo is None:  # treat a naive time as UTC
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# =====================================================================
# Raw value -> the column of its type
#   value_type picks the target column. A value that does not fit is NOT
#   dropped; it falls back to value_str, so no data is lost.
# =====================================================================
def decode_value(raw_value: str, value_type: str) -> tuple[float | None, str | None, bool | None]:
    if value_type == "num":
        try:
            return float(raw_value), None, None
        except ValueError:
            logger.warning("Not a number, storing as text instead: %r", raw_value)
            return None, raw_value, None

    if value_type == "bool":
        parsed = _parse_bool(raw_value)
        if parsed is None:
            logger.warning("Not a boolean, storing as text instead: %r", raw_value)
            return None, raw_value, None
        return None, None, parsed

    # value_type == 'str' (also what a stub starts with)
    return None, raw_value, None


def _parse_bool(text: str) -> bool | None:
    lowered = text.strip().lower()
    if lowered in {"true", "1", "on", "yes"}:
        return True
    if lowered in {"false", "0", "off", "no"}:
        return False
    return None


# =====================================================================
# Write statements
#   Normal case: a plain INSERT. A redelivered message (DUP flag) is written
#   only if its row is missing. measurements has no unique constraint on
#   purpose; the check uses idx_measurements_lookup and only ever runs for the
#   rare redelivery.
#   The value is part of the comparison because broker time has a resolution
#   of one second: two different values of one metric within the same second
#   are not duplicates.
# =====================================================================
INSERT_MEASUREMENT = """
    INSERT INTO measurements (time, metric_id, device_id, value_num, value_str, value_bool)
    VALUES (%(time)s, %(metric_id)s, %(device_id)s,
            %(value_num)s, %(value_str)s, %(value_bool)s)
"""

INSERT_MEASUREMENT_IF_ABSENT = """
    INSERT INTO measurements (time, metric_id, device_id, value_num, value_str, value_bool)
    SELECT %(time)s::timestamptz, %(metric_id)s::integer, %(device_id)s::integer,
           %(value_num)s::double precision, %(value_str)s::text, %(value_bool)s::boolean
    WHERE NOT EXISTS (
        SELECT 1 FROM measurements
        WHERE device_id = %(device_id)s::integer
          AND metric_id = %(metric_id)s::integer
          AND time = %(time)s::timestamptz
          AND value_num IS NOT DISTINCT FROM %(value_num)s::double precision
          AND value_str IS NOT DISTINCT FROM %(value_str)s::text
          AND value_bool IS NOT DISTINCT FROM %(value_bool)s::boolean
    )
"""


# =====================================================================
# Ingester: MQTT callback -> queue -> writer thread -> acknowledgement
#   The MQTT callback only does work that needs no database (time, device,
#   native_key). Everything that touches the database (metric lookup, INSERT)
#   runs in the writer thread, so an outage is handled in ONE place and never
#   blocks the MQTT loop.
# =====================================================================
class Ingester:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool = ConnectionPool(settings.database_url, min_size=1, max_size=4, open=True)
        self._devices = DeviceRegistry(self._pool)
        self._metrics = MetricResolver(self._pool, settings.metric_cache_seconds)
        self._queue: "queue.Queue[Pending | None]" = queue.Queue(maxsize=50_000)
        self._stop_requested = threading.Event()
        self._writer = threading.Thread(target=self._run_writer, name="batch-writer", daemon=True)
        self._writer_failed = False
        self._client = self._build_client()

    def _build_client(self) -> mqtt.Client:
        # MQTT 5 is required for user properties (timestamp) and session
        # expiry. clean_start=False with a stable client id lets the broker
        # recognise the session and deliver what was missed.
        # manual_ack: messages are acknowledged after the commit (see _store).
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._settings.mqtt_client_id,
            protocol=mqtt.MQTTv5,
            manual_ack=True,
        )
        if self._settings.mqtt_username:
            client.username_pw_set(self._settings.mqtt_username, self._settings.mqtt_password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        return client

    def _connect_properties(self) -> Properties:
        properties = Properties(PacketTypes.CONNECT)
        properties.SessionExpiryInterval = self._settings.mqtt_session_expiry_seconds
        # Deliberately NO ReceiveMaximum: the broker runs with
        # max_inflight_messages 0 (mosquitto.conf explains why), and a value
        # here would switch its flow control back on. Back-pressure comes from
        # the bounded queue instead: put() blocks the MQTT loop.
        return properties

    def _connect_mqtt(self) -> None:
        while not self._stop_requested.is_set():
            try:
                # clean_start=False: resume the persistent session of this client id.
                self._client.connect(
                    self._settings.mqtt_host,
                    self._settings.mqtt_port,
                    clean_start=False,
                    properties=self._connect_properties(),
                )
            except OSError as error:
                logger.warning(
                    "Broker unreachable, retrying in %.0f s: %s",
                    self._settings.retry_seconds, error,
                )
                self._stop_requested.wait(self._settings.retry_seconds)
            else:
                self._client.loop_start()
                return

    # -- MQTT callbacks -------------------------------------------------
    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            logger.error("MQTT connection failed: %s", reason_code)
            return
        # QoS 1 so the persistent session delivers what was missed.
        # Retained messages only for a NEW subscription: otherwise the same
        # old value (e.g. the open3e last will) would arrive on every
        # reconnect and end up in the database twice.
        client.subscribe(
            self._settings.mqtt_topic_filter,
            options=SubscribeOptions(qos=1, retainHandling=SubscribeOptions.RETAIN_SEND_IF_NEW_SUB),
        )
        logger.info(
            "Connected (session_present=%s), subscribed to %s",
            flags.session_present,
            self._settings.mqtt_topic_filter,
        )

    def _on_message(self, client: mqtt.Client, userdata, message: mqtt.MQTTMessage) -> None:
        try:
            pending = self._parse(message)
        except Exception:  # no callback may take the client down
            logger.exception("Message dropped (topic %s)", message.topic)
            pending = None
        if pending is None:
            # Dropped on purpose. Acknowledge anyway, or the broker keeps it.
            client.ack(message.mid, message.qos)
        else:
            self._queue.put(pending)

    # -- Core logic -----------------------------------------------------
    def _parse(self, message: mqtt.MQTTMessage) -> Pending | None:
        measured_at = self._read_timestamp(message)
        if measured_at is None:
            # No trustworthy time, so the message is dropped.
            logger.debug("No timestamp property, dropped: %s", message.topic)
            return None

        topic = self._strip_prefix(message.topic)
        if topic is None:
            return None

        resolution = self._devices.resolve(topic)
        if resolution is None:
            logger.debug("No device for topic: %s", message.topic)
            return None
        device, remainder = resolution

        native_key = build_native_key(remainder)
        if native_key is None:
            logger.debug("No native_key in topic: %s", message.topic)
            return None

        return Pending(
            time=measured_at,
            device=device,
            native_key=native_key,
            raw_value=message.payload.decode("utf-8", errors="replace").strip(),
            mid=message.mid,
            qos=message.qos,
            dup=message.dup,
        )

    def _strip_prefix(self, topic: str) -> str | None:
        prefix = self._settings.mqtt_topic_prefix.rstrip("/")
        if not prefix:
            return topic
        if topic == prefix:
            return None
        if topic.startswith(prefix + "/"):
            return topic[len(prefix) + 1:]
        return None  # topic outside the expected prefix

    def _read_timestamp(self, message: mqtt.MQTTMessage) -> datetime | None:
        """Read the timestamp user property (ISO 8601). None if it is missing."""
        properties = getattr(message, "properties", None)
        user_properties = getattr(properties, "UserProperty", None)
        if not user_properties:
            return None
        wanted = self._settings.mqtt_timestamp_property
        for key, value in user_properties:
            if key == wanted:
                return parse_iso_timestamp(value)
        return None

    # -- Writer thread --------------------------------------------------
    def _run_writer(self) -> None:
        try:
            self._write_loop()
        except Exception:
            # A dead writer behind a living MQTT loop would be a zombie that
            # accepts messages and never writes them. End the process instead.
            logger.exception("Writer thread crashed, shutting down")
            self._writer_failed = True
        finally:
            self._stop_requested.set()

    def _write_loop(self) -> None:
        # A batch closes at batch_max_rows or batch_max_seconds after its first
        # message, whichever comes first, so nothing is left waiting.
        while not self._stop_requested.is_set():
            first = self._queue.get()
            if first is None:  # sentinel from run_forever
                break
            batch = [first]
            deadline = time.monotonic() + self._settings.batch_max_seconds
            while len(batch) < self._settings.batch_max_rows:
                try:
                    item = self._queue.get(timeout=max(deadline - time.monotonic(), 0))
                except queue.Empty:
                    break
                if item is None:
                    break
                batch.append(item)
            self._store(batch)

    def _store(self, batch: list[Pending]) -> None:
        try:
            self._write(batch)
        except psycopg.OperationalError as error:
            logger.warning("Database unreachable: %s", error)
            self._ride_out_outage(batch)
            return
        # Only now, after the commit, does the broker get to consider the
        # message delivered.
        for pending in batch:
            self._client.ack(pending.mid, pending.qos)
        logger.debug("%d rows written", len(batch))

    def _ride_out_outage(self, batch: list[Pending]) -> None:
        # Disconnect from the broker: from here on IT collects in the
        # persistent session, not the ingester in its memory. What has already
        # been received stays here and is written once the database is back.
        # It is no longer acknowledged, so the broker redelivers the QoS 1
        # messages with the DUP flag and INSERT_MEASUREMENT_IF_ABSENT skips them.
        logger.warning("Disconnecting from the broker until the database is back (the broker buffers)")
        self._client.disconnect()
        self._client.loop_stop()

        def write_received() -> None:
            batch.extend(self._drain_queue())
            self._write(batch)

        if self._until_database_is_back(write_received):
            logger.info("Database is back, wrote %d messages received earlier", len(batch))
            self._connect_mqtt()
        else:
            logger.warning(
                "Stopped during a database outage: %d received messages not written "
                "(the broker redelivers QoS 1, QoS 0 is lost)", len(batch),
            )

    def _drain_queue(self) -> list[Pending]:
        drained: list[Pending] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return drained
            if item is not None:
                drained.append(item)

    def _until_database_is_back(self, action) -> bool:
        """Repeat action until the database accepts it. False if stopped first."""
        while not self._stop_requested.is_set():
            try:
                action()
                return True
            except psycopg.OperationalError as error:
                logger.warning(
                    "Database unreachable, retrying in %.0f s: %s",
                    self._settings.retry_seconds, error,
                )
                self._stop_requested.wait(self._settings.retry_seconds)
        return False

    def _write(self, batch: list[Pending]) -> None:
        fresh: list[dict] = []
        redelivered: list[dict] = []
        for pending in batch:
            (redelivered if pending.dup else fresh).append(self._to_row(pending))
        try:
            self._insert(fresh, redelivered)
        except psycopg.OperationalError:
            raise  # database unreachable: the caller waits and retries
        except psycopg.Error:
            # The database rejects the content of the batch (e.g. a NUL byte in
            # a text). Retrying cannot help and would hold up everything behind
            # it, so write row by row and drop only the broken one. IF_ABSENT,
            # so that an interruption halfway through duplicates nothing on retry.
            logger.exception("Batch rejected, writing %d rows one by one", len(batch))
            for row in fresh + redelivered:
                try:
                    self._insert([], [row])
                except psycopg.OperationalError:
                    raise
                except psycopg.Error:
                    logger.exception("Row dropped: %r", row)

    def _to_row(self, pending: Pending) -> dict:
        metric = self._metrics.resolve(pending.device.device_type, pending.native_key)
        value_num, value_str, value_bool = decode_value(pending.raw_value, metric.value_type)
        return {
            "time": pending.time,
            "metric_id": metric.metric_id,
            "device_id": pending.device.device_id,
            "value_num": value_num,
            "value_str": value_str,
            "value_bool": value_bool,
        }

    def _insert(self, fresh: list[dict], redelivered: list[dict]) -> None:
        # One batch is one transaction: all of it is committed, or none.
        with self._pool.connection() as connection, connection.cursor() as cursor:
            if fresh:
                cursor.executemany(INSERT_MEASUREMENT, fresh)
            if redelivered:
                cursor.executemany(INSERT_MEASUREMENT_IF_ABSENT, redelivered)

    # -- Lifecycle ------------------------------------------------------
    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, lambda *_: self._stop_requested.set())
        signal.signal(signal.SIGTERM, lambda *_: self._stop_requested.set())

        # Connect to the broker only once the database is there; until then
        # the broker buffers.
        if not self._until_database_is_back(self._devices.reload):
            return
        self._writer.start()
        self._connect_mqtt()
        logger.info("Ingester running. Press Ctrl+C to stop.")

        self._stop_requested.wait()

        logger.info("Shutting down ...")
        # Stop the inflow first, then write what is left. Its acknowledgements
        # can no longer be sent; the broker redelivers those messages with the
        # DUP flag on the next start and INSERT_MEASUREMENT_IF_ABSENT skips them.
        self._client.disconnect()
        self._client.loop_stop()
        self._queue.put(None)  # sentinel wakes a blocking get()
        self._writer.join(timeout=10)
        self._pool.close()
        if self._writer_failed:
            sys.exit(1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    Ingester(Settings()).run_forever()


if __name__ == "__main__":
    main()
