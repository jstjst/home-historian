# Architecture

This document explains the decisions behind home-historian. It starts with the
data flow, goes through the individual decisions, and ends with a close look at
the data model for metrics.

## Guiding idea

home-historian is a historian, not a controller. It reads devices, changes
nothing about them and keeps every raw value. Three requirements follow from
that, and everything else is built around them:

1. **A fine time grid.** Values arrive every few seconds, not as
   quarter-hourly averages. Cycling and control behaviour should be visible.
2. **Lose nothing, alter nothing.** A value is stored the way the device
   delivered it, even if nobody knows yet what it means.
3. **Comparable across devices.** The flow temperature reported by the heat
   pump and the one reported by a heat meter must sit side by side in one
   query, although the two devices name them in completely different ways.

## Data flow

```
module ──publish──► Mosquitto ──► ingest ──► TimescaleDB ──► Grafana
                    │  stamps the time      │ resolves device + metric
                    │  buffers on outage    │ acknowledges after the commit
```

1. A **module** reads a data source and publishes every value as an MQTT topic
   of its own, `<prefix>/<device…>/<quantity…>`, with a scalar payload.
2. The **broker** stamps every message with the time it received it.
3. The **ingester** derives device and metric from the topic, writes the value
   to the database and only then acknowledges the message.
4. **Grafana** reads through a view that joins measurements with the master
   data of device and metric.

## Decisions

### MQTT as the bus between modules and core

Modules and core do not know each other; all they share is a topic scheme. A
module can be written in any language, restart on its own and fail without
touching the rest. Ready-made tools such as open3e speak MQTT anyway, in which
case the module is nothing but configuration. The price is one more service,
but the broker also takes over the buffering (see below).

### The broker stamps the time, not the ingester

Every message carries the MQTT 5 user property `timestamp`. Mosquitto's
message-timestamp plugin sets it on receipt. The ingester uses that time and
nothing else, and **drops messages without it**.

The reason: the time at which the ingester writes is the wrong one. After an
outage the broker delivers values that are hours old; stamped with the write
time they would all land on the same instant. The time of receipt in the
broker survives buffering.

A module that knows the time of measurement better than the broker sends
`timestamp` itself, and its value takes precedence. The vicare module does so
because the cloud API reports when each value came into being, while the poll
happens minutes later. The broker's own stamp has a resolution of one second.

### The broker is the buffer; acknowledge after the commit

If the database fails, or the connection to it when it runs on another host,
no values should be lost. Rather than building a queue of its own, the
ingester uses the broker's:

- It holds a **persistent session** (fixed client id, expiring after 7 days).
  Whatever the broker cannot deliver, it queues.
- It **acknowledges only after the commit** (`manual_ack`). To the broker a
  message counts as delivered only once its row is in the database.
- When the database is unreachable it **disconnects from the broker**, writes
  what it has already received as soon as the database is back, and reconnects.
- The broker keeps the queue in RAM and saves it to disk every 30 minutes and
  on shutdown. That is gentle on SD cards; a power cut during a database
  outage loses at most what was queued since the last save.

**Duplicates.** "At least once" delivery means that if the connection breaks
between commit and acknowledgement, the message comes again. MQTT marks such
repeats with the DUP flag. The ingester writes exactly those messages with
`INSERT … WHERE NOT EXISTS` and all others with a plain `INSERT`. The normal
path stays free of checks, and the table free of a unique constraint. The
comparison includes the value, because broker time only resolves to a second.

That check covers the transport only. A measurement that a module publishes
twice, in two separate messages, is written twice. Avoiding that is the
module's job (see [modules.md](modules.md)).

**Why not QoS 2?** In MQTT, "exactly once" covers the hand-over to the client,
not "is in the database". The client would have to persist its QoS 2 state
together with the commit; the library used here (paho-mqtt) keeps it in memory
only. After a restart it no longer knows a message that was already announced
and cannot deliver it. A possible duplicate would turn into a possible loss.

**Modules publishing at QoS 0.** open3e publishes at QoS 0, which a broker
normally does not keep for a disconnected client. Two settings in
[mosquitto.conf](../mosquitto/config/mosquitto.conf) make up for it:

- `queue_qos0_messages true` buffers QoS 0 as well.
- `max_inflight_messages 0` works around a bug in Mosquitto (checked: 2.0.22):
  every buffered QoS 0 message permanently uses up one slot of the client's
  inflight quota; once it is exhausted, all QoS 1 messages are stuck until the
  client reconnects.

One gap remains: QoS 0 values the ingester has received but not yet written
(one batch at most, about two seconds) are lost if it stops at that moment.
New modules should therefore publish at QoS 1.

Measured in testing: a backlog of 71,000 messages was drained 6 seconds after
reconnecting; a buffered message takes roughly 480 bytes of RAM in the broker.

### TimescaleDB rather than InfluxDB or ClickHouse

- **Mixed value types.** Devices deliver numbers, texts and booleans all mixed
  together. A relational schema separates them into typed columns; a database
  with one fixed type per field needs workarounds.
- **Master data and time series in one database.** Devices, metric
  definitions and measurements can be combined with ordinary joins. That is
  precisely what makes analysis across devices possible.
- **Many small writes.** The load is a few values per second, not billions of
  rows in large batches.
- **It is still PostgreSQL:** SQL, roles, backups and tooling are familiar,
  and Grafana speaks it natively.

Household data stays far below the sizes at which specialised analytics
databases show their strengths.

### A narrow measurements table with typed value columns

`measurements` has one row per value: time, metric, device, and exactly one
filled value column (`value_num`, `value_str` or `value_bool`). A wide table
with one column per quantity would be almost empty with hundreds of data
points that vary by device and firmware, and it would need altering for every
new data point.

### No foreign key, no unique constraint on `measurements`

The frequent insert should not be slowed down by a constraint check, and a new
data point must never fail on the schema. The metric id always exists anyway,
because the ingester creates it before writing. Duplicates are avoided at the
source (see above), not rejected by the database.

### Condensing with real-time continuous aggregates

For long time ranges there are two precomputed views: `measurements_1h`
(mean/min/max per hour for instantaneous values) and `consumption_1d` (daily
consumption from meter readings). Both run with `materialized_only = false`:
a query fills in the most recent, not yet computed edge from the raw data.
Without that setting the last few hours would silently be missing from a
dashboard. The raw data stays untouched alongside.

### Modules as separate containers, each with a profile

Every data source runs as a service of its own in the single `compose.yml`
and is switched on or off through a Compose profile. Modules have their own
dependencies (open3e needs the host's CAN bus, vicare a cloud login) and
their own failure modes; separate containers keep these apart. One file with
profiles rather than many Compose files, because the whole stack then starts
with one command and can be read in one place.

### Database and Grafana can be split off

The database and Grafana hang on a profile too. On a small computer with an SD
card you can leave them out and point the ingester at a database on another
host with `INGEST_DATABASE_URL`. Buffering in the broker is what makes this
split sensible: the link to the database is allowed to fail.

### Safe defaults

The broker and the tools have no authentication, so every port is bound
locally by default. Grafana reads through a database role that may only
`SELECT`, because it passes SQL on unchecked. The open3e module starts without
the command channel through which open3e could write data points; a historian
has no use for it.

## The data model: devices, metrics, measurements

```
devices                 metrics                        measurements (hypertable)
───────                 ───────                        ────────────
device_id   ◄─────┐     metric_id   ◄───────────┐      time
type ────────────┐│     type                    └───── metric_id
device_key       │└──────────────────────────────────── device_id
name             │      native_key                     value_num
category         └────► display_name                   value_str
                        display_subname                value_bool
                        unit, category
                        value_type, aggregation
```

### A metric belongs to the device type, not to the device

This is the central decision: `metrics` describes what a quantity means **for
a type of device**. The individual device only comes in with the measurement.

The reason is identical devices. Three heat meters of the same model expose
the same registers. If the definition were attached to the device, "register 1
= energy in kWh, cumulative" would have to be curated three times and kept in
sync three times. This way it exists once:

```sql
-- one definition for every meter of this type
INSERT INTO metrics (type, native_key, display_name, unit, aggregation)
VALUES ('kamstrup', '1.Actual', 'Energy total', 'kwh', 'counter');

-- three devices sharing it
INSERT INTO devices (type, device_key, name, category) VALUES
  ('kamstrup', 'heatmeter/hc1', 'Heat meter HC1', 'hc1'),
  ('kamstrup', 'heatmeter/hc2', 'Heat meter HC2', 'hc2'),
  ('kamstrup', 'heatmeter/hc3', 'Heat meter HC3', 'hc3');
```

A "device" here is the physical unit, not the module: the open3e module reads
heat pump and battery storage over the same bus and registers two devices of
different types for them.

### `native_key`: the one raw key

`native_key` is the name of the quantity as the source delivers it: the rest
of the MQTT topic after the device, with dots instead of slashes
(`268_FlowTemperatureSensor.SensorStatus.ID`). Together with the type it is
unique. It is the **only raw column** and serves the ingester's lookup alone;
it does not appear in analysis.

### `metric_id`: a surrogate key for lean rows

`measurements` stores an integer, not the text key. A `native_key` can be long
(API paths run to dozens of characters); with millions of rows it would
otherwise dictate the size of table and index. The ingester resolves
`(type, native_key)` once and keeps the result in a cache.

### `display_name` and `display_subname`: the unified view

Every source names the same physical quantity differently. For analysis each
metric therefore gets two curated fields:

- `display_name` is the **sensor**: "HC1 flow temperature"
- `display_subname` is the **role** of the value: `actual`, `min`, `max`,
  `average`, `setpoint`, `state`

Many devices report several values for one sensor. As separate metrics with a
shared `display_name` they can be shown together (an actual line with a
min/max band) or filtered precisely (`WHERE display_subname = 'actual'`), and
in the same way for every device, while the `native_key` looks different for
each source. Grouping by `display_name` alone throws actual, min and max into
one series, so queries group by both fields.

### `category`: the part of the installation, from metric or device

The category assigns a value to a part of the installation (`hc1`, `dhw`,
`pv`, `battery`, `grid`, …). It can live in two places:

- on the **metric**, when the quantity itself determines the part. On a heat
  pump, "flow HC1" is a different data point from "flow HC2".
- on the **device**, when the metric is shared. The "energy" register of a
  heat meter does not know which heating circuit the meter sits in.

The view `v_measurements` merges the two with `COALESCE(metric, device)`. A
single condition `category = 'hc1'` then finds the heat pump's flow
temperature **and** the heat meter of that circuit. Folding the two places
into one would model one of the two kinds of device wrongly.

### `aggregation` and `unit` rather than a category "energy"

Whether a value is instantaneous (`gauge`) or a running meter reading
(`counter`) decides how to condense it correctly: a mean in one case, a
difference in the other. That is a property of how the value behaves over
time, not of where it sits in the installation, so it has a field of its own.

### `value_type` is a hint, not a constraint

`value_type` tells the ingester which column a value belongs in. If a single
value does not fit (a text where a number is expected) it goes to `value_str`
instead of being dropped. A type anomaly never costs data.

### Unknown quantities are stored, not rejected

If the ingester does not know a `native_key`, it creates the metric itself: a
"stub" with `display_name = native_key`, no category, typed as text. The value
is stored from the first second; curation (name, unit, category, type)
follows by hand once somebody needs the quantity. A firmware update that adds
data points loses nothing.

- Name, unit and category apply **retroactively**, because the view fetches
  them at query time.
- A changed `value_type` only applies **to new values**: once the metric cache
  expires (one hour), or immediately after `docker compose restart ingest`.
  Values already stored stay in their column. A stub's numbers therefore sit
  in `value_str` as text and are missing from both aggregates until
  [db/backfill_value_types.sql](../db/backfill_value_types.sql) moves them. The
  script finds the affected metrics itself and refreshes the aggregates over
  their whole history, which the refresh policies (3 and 7 days) would not.

Which metrics are still stubs, with their latest value, a chart and the list
of values they reported: the Grafana dashboard "home-historian - Stubs". Or in
SQL:

```sql
SELECT metric_id, type, native_key FROM metrics WHERE display_name = native_key;
```

### Devices are resolved by longest prefix

`devices.device_key` is the start of the topic after the stack prefix. The
ingester picks the device with the longest matching key; everything after it
is the `native_key`. Keys may have several segments (`open3e/vitocal`,
`vicare/<gateway>/<device>`), which gives every module a namespace of its own.
Topics without a matching device are dropped. Registering devices is manual on
purpose, because the type cannot be derived from the topic.

## Open points

- Retention and compression for old raw data.
- Device types and categories are enums that grow with
  `ALTER TYPE … ADD VALUE`; so far they cover the installations this project
  grew out of.
- Meter overflows and replacements are not detected in `consumption_1d`.
- Switching open3e to QoS 1 would close the remaining loss window, but open3e
  does not offer that setting.
