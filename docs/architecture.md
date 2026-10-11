# Architecture

This document describes the decisions behind home-historian: the data flow,
the individual decisions with their reasons, and the data model.

## Guiding idea

home-historian reads devices, does not change them and keeps every raw value.
Three requirements follow from that:

1. A fine time grid. Values arrive every few seconds, not as quarter-hourly
   averages, so that cycling and control behaviour are visible.
2. No loss and no alteration. A value is stored as the device delivered it,
   also when its meaning is not known yet.
3. Comparability across devices. The flow temperature reported by the heat
   pump and the one reported by a heat meter have to be usable in one query,
   although the two devices name them differently.

## Data flow

```
module ──publish──► Mosquitto ──► ingest ──► TimescaleDB ──► Grafana
                    │  stamps the time      │ resolves device + metric
                    │  buffers on outage    │ acknowledges after the commit
```

1. A module reads a data source and publishes every value as an MQTT topic
   of its own, `<prefix>/<device key>/<native key>`, with a scalar payload.
2. The broker stamps every message with the time it received it.
3. The ingester derives device and metric from the topic, writes the value
   to the database and only then acknowledges the message.
4. Grafana reads through a view that joins measurements with the rows of
   their device and metric.

## Decisions

### MQTT as the bus between modules and core

Modules and core share only a topic scheme. A module can be written in any
language, restart independently and fail without affecting the rest.
Existing tools such as open3e already publish to MQTT; such a module consists
of configuration only. The cost is one more service. The broker also does
the buffering (see below).

### Modules publish everything; meaning is assigned in the database

A module publishes every value its source delivers, under the name the source
uses for it. It does not select, rename or convert. What a key means (name,
unit, part of the installation, value type) is a row in `metrics` and is
assigned in the database, at any later time.

The alternative is a module that knows its quantities: it reads a chosen list
and publishes them under curated names and units. That keeps the database
small and every stored value documented, but it stores only what was
understood when the module was written.

Reasons for the first approach:

- Values are recorded before their meaning is known. A data point that
  nobody can interpret yet is stored from the first pass as a stub. When it
  turns out to matter, its history is already there. With devices whose
  interfaces are not documented (the SENEC API, most of the E3 data points)
  this is the normal case: of the 963 values a SENEC unit delivered per
  pass, 835 had no checked definition when its module was written.
- Definitions apply retroactively. Name, unit and category are joined at
  query time (`v_measurements`), so a definition added later covers every
  value stored before it.
- A wrong definition is corrected in one row. The module and the stored
  values stay unchanged.
- Modules stay small. They contain a protocol and no list of quantities.
  A firmware update that adds a field needs no change in the module.

Costs:

- Volume: many values never change and are stored in every pass.
  Compression reduces this (see below), and a module can limit what it reads
  where the volume matters (`OPEN3E_DIDS`, `SENEC_SECTIONS`).
- Curation is a separate, manual step, and a stub's values are stored as
  text until its metric is typed. Values stored before that need
  [db/backfill_value_types.sql](../db/backfill_value_types.sql).

A module still decodes the source's encoding: the senec module turns
`fl_4404AEB9` into a number, and the mbus module scales two Kamstrup
registers whose telegram carries no scale. Neither assigns a meaning.

### The broker stamps the time, not the ingester

Every message carries the MQTT 5 user property `timestamp`. Mosquitto's
message-timestamp plugin sets it on receipt. The ingester uses that time and
no other, and drops messages without it.

The time at which the ingester writes cannot be used. After an outage the
broker delivers values that are hours old; with the write time they would
all be stored at the same instant. The time of receipt in the broker is not
affected by buffering.

A module that knows the time of measurement better than the broker sends
`timestamp` itself, and its value takes precedence. The vicare module does so
because the cloud API reports when each value came into being, while the poll
happens minutes later. The broker's own stamp has a resolution of one second.

### The broker is the buffer; acknowledge after the commit

If the database fails, or the connection to it when it runs on another host,
no values should be lost. The ingester has no queue of its own and uses the
broker's:

- It holds a persistent session (fixed client id, expiring after 7 days).
  The broker queues what it cannot deliver.
- It acknowledges only after the commit (`manual_ack`). To the broker a
  message counts as delivered once its row is in the database.
- When the database is unreachable it disconnects from the broker, writes
  what it has already received as soon as the database is back, and
  reconnects.
- The broker keeps the queue in RAM and saves it to disk every 30 minutes and
  on shutdown. This keeps the number of writes low (SD cards); a power cut
  during a database outage loses at most what was queued since the last save.

Duplicates: "at least once" delivery means that if the connection breaks
between commit and acknowledgement, the message comes again. MQTT marks such
repeats with the DUP flag. The ingester writes exactly those messages with
`INSERT … WHERE NOT EXISTS` and all others with a plain `INSERT`. The normal
path stays free of checks, and the table free of a unique constraint. The
comparison includes the value, because broker time only resolves to a second.

That check covers the transport only. A measurement that a module publishes
twice, in two separate messages, is written twice. Avoiding that is the
module's job (see [modules.md](modules.md)).

QoS 2 is not used. In MQTT, "exactly once" covers the hand-over to the client,
not "is in the database". The client would have to persist its QoS 2 state
together with the commit; the library used here (paho-mqtt) keeps it in memory
only. After a restart it no longer knows a message that was already announced
and cannot deliver it. A possible duplicate would turn into a possible loss.

Modules publishing at QoS 0: open3e publishes at QoS 0, which a broker
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

### TimescaleDB as the database

TimescaleDB is an extension of PostgreSQL. The project uses these of its
properties:

- A hypertable next to ordinary tables. `measurements` is a hypertable,
  partitioned by time into chunks of 7 days; `devices` and `metrics` are
  ordinary tables in the same database. The view `v_measurements` joins them,
  which is how a definition applies to values stored before it and how
  values of different devices are compared.
- Compression per chunk, segmented by device and metric. A module stores
  every value in every pass, so most of the volume is repetition (see
  [Old raw data is compressed](#old-raw-data-is-compressed)).
- Continuous aggregates that include the not yet materialised edge
  (`measurements_1h`, `consumption_1d`).
- Typed columns. Devices deliver numbers, texts and booleans, and each has a
  column of its type.
- PostgreSQL's roles and its data source in Grafana: Grafana reads through a
  role that may only `SELECT`.

### One row per value, one column per value type

`measurements` has one row per value: time, metric, device, and exactly one
filled value column (`value_num`, `value_str` or `value_bool`). TigerData's
documentation compares
[wide, narrow and medium table layouts](https://www.tigerdata.com/docs/learn/data-model/wide-narrow-medium-tables);
in its terms this is the medium layout, with one column per data type and
the metric as a reference.

A wide table with one column per quantity would be almost empty with
hundreds of data points that vary by device and firmware, and it would need
an `ALTER TABLE` for every new data point. With the medium layout a new data
point is a new row in `metrics`. Its drawback is that something has to record
which column a metric's values are in; that is `metrics.value_type`.

### No foreign key, no unique constraint on `measurements`

The frequent insert should not be slowed down by a constraint check, and a new
data point must never fail on the schema. The metric id always exists,
because the ingester creates it before writing. Duplicates are avoided at the
source (see above), not rejected by the database.

### Condensing with real-time continuous aggregates

For long time ranges there are two precomputed views: `measurements_1h`
(mean/min/max per hour for instantaneous values) and `consumption_1d` (daily
consumption from meter readings). Both run with `materialized_only = false`:
a query fills in the most recent, not yet computed edge from the raw data.
Without that setting the last few hours would be missing from a dashboard
without an error. The raw data is not changed by the aggregates.

### Modules as separate containers, each with a profile

Every data source runs as a service of its own in the single `compose.yml`
and is switched on or off through a Compose profile. Modules have their own
dependencies (open3e needs the host's CAN bus, vicare a cloud login) and
their own failure modes; separate containers keep these apart. One file with
profiles rather than many Compose files, because the whole stack then starts
with one command and can be read in one place.

### Database and Grafana can be split off

The database and Grafana have a profile too. On a small computer with an SD
card they can be left out, with the ingester pointed at a database on
another host through `INGEST_DATABASE_URL`. Because the broker buffers, the
link to that database may fail without loss.

### Safe defaults

The broker and the tools have no authentication, so every port is bound
locally by default. Grafana reads through a database role that may only
`SELECT`, because it passes SQL on unchecked. The open3e module starts without
the command channel through which open3e could write data points; the stack
does not need it.

## The data model: devices, metrics, measurements

```
devices                 metrics                     measurements (hypertable)
───────                 ───────                     ────────────
type ─────────────────► type                        time
device_key              native_key                  value_num
name                    display_name                value_str
category                display_subname             value_bool
                        unit, category
                        value_type, aggregation
                        metric_id  ◄─────────────── metric_id
device_id  ◄─────────────────────────────────────── device_id
```

None of the three arrows is a foreign key. `devices.type` and `metrics.type`
use the same enum (`device_type`): the ingester takes the type of the device
a message belongs to and looks the metric up by `(type, native_key)`.
`measurements` references metric and device by id; the view `v_measurements`
joins them.

### A metric belongs to the device type, not to the device

`metrics` describes what a quantity means for a type of device. The
individual device is referenced by the measurement only.

The reason is identical devices. Three heat meters of the same model expose
the same records. If the definition were attached to the device, "record
04_06 = heat energy, cumulative" would have to be curated three times and kept
in sync three times. This way it exists once:

```sql
-- one definition for every meter of this type
INSERT INTO metrics (type, native_key, display_name, unit, aggregation)
VALUES ('multical303', '04_06', 'Heat energy', 'wh', 'counter');

-- three devices sharing it
INSERT INTO devices (type, device_key, name, category) VALUES
  ('multical303', 'mbus/hc1', 'Heat meter HC1', 'hc1'),
  ('multical303', 'mbus/hc2', 'Heat meter HC2', 'hc2'),
  ('multical303', 'mbus/hc3', 'Heat meter HC3', 'hc3');
```

A "device" here is the physical unit, not the module: the open3e module reads
heat pump and battery storage over the same bus and registers two devices of
different types for them.

### `native_key`: the one raw key

`native_key` is the name of the quantity as the source delivers it: the rest
of the MQTT topic after the device, with dots instead of slashes
(`268_FlowTemperatureSensor.SensorStatus.ID`). Together with the type it is
unique. It is the only raw column and is used for the ingester's lookup only;
it does not appear in analysis.

### `metric_id`: a surrogate key for lean rows

`measurements` stores an integer, not the text key. A `native_key` can be long
(API paths run to dozens of characters); with millions of rows it would
otherwise dictate the size of table and index. The ingester resolves
`(type, native_key)` once and keeps the result in a cache.

### Old raw data is compressed

Every pass of a module stores every value again, changed or not, and a
row costs well over 100 bytes, half of it index. Chunks of `measurements`
older than 7 days are therefore compressed: stored column by column, one
segment per device and metric, ordered by time. That is the shape of the
queries, and it puts similar values next to each other. In tests a
compressed chunk took a twentieth to a thirtieth of the space and returned
exactly the rows that went in.

- The recent chunks are not compressed. A chunk spans 7 days and is
  compressed once it lies 7 days back, so up to 14 days are uncompressed.
  The ingester's inserts and its duplicate check normally only touch
  those.
- Compressed chunks can still be written to, more slowly: a late
  value is stored next to the compressed data, and the policy compresses
  it on its next run. Both aggregates read compressed chunks like any
  other.
- An `UPDATE` decompresses what it might touch. With the `metric_id`
  written into the statement that is one metric's segments; with a join
  it is every compressed row, and TimescaleDB refuses more than 100 000
  per statement by default.
  [db/backfill_value_types.sql](../db/backfill_value_types.sql) therefore
  goes metric by metric.

### `display_name` and `display_subname`: the unified view

Every source names the same physical quantity differently. For analysis each
metric therefore gets two curated fields:

- `display_name` is the sensor: "HC1 flow temperature"
- `display_subname` is the role of the value: `actual`, `min`, `max`,
  `average`, `setpoint`, `state`

Many devices report several values for one sensor. As separate metrics with a
shared `display_name` they can be shown together (an actual line with a
min/max band) or filtered (`WHERE display_subname = 'actual'`), in the same
way for every device, while the `native_key` differs for each source.
Grouping by `display_name` alone merges actual, min and max into one series,
so queries group by both fields.

### `category`: the part of the installation, from metric or device

The category assigns a value to a part of the installation (`hc1`, `dhw`,
`pv`, `battery`, `grid`, …). It can live in two places:

- on the metric, when the quantity itself determines the part. On a heat
  pump, "flow HC1" is a different data point from "flow HC2".
- on the device, when the metric is shared. The "energy" register of a
  heat meter does not tell which heating circuit the meter is installed in.

The view `v_measurements` merges the two with `COALESCE(metric, device)`. A
single condition `category = 'hc1'` then finds the heat pump's flow
temperature and the heat meter of that circuit. A single place would model
one of the two kinds of device wrongly.

### `aggregation` and `unit` rather than a category "energy"

Whether a value is instantaneous (`gauge`) or a running meter reading
(`counter`) decides how to condense it correctly: a mean in one case, a
difference in the other. That is a property of how the value behaves over
time, not of its place in the installation, so it has a field of its own.

### `value_type` is a hint, not a constraint

`value_type` tells the ingester which column a value belongs in. If a single
value does not fit (a text where a number is expected) it goes to `value_str`
instead of being dropped. A type anomaly never costs data.

### Unknown quantities are stored, not rejected

If the ingester does not know a `native_key`, it creates the metric itself: a
"stub" with `display_name = native_key`, no category, typed as text. The value
is stored from the first message; curation (name, unit, category, type)
follows by hand once somebody needs the quantity. A firmware update that adds
data points loses nothing.

- Name, unit and category apply retroactively, because the view fetches
  them at query time.
- A changed `value_type` only applies to new values: once the metric cache
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

- Retention for old raw data. It is compressed, but never deleted.
- Device types and categories are enums that grow with
  `ALTER TYPE … ADD VALUE`. Every module adds its device type in its
  `devices.sql`; the categories are part of the schema and cover the
  installation this project was written for.
- Meter overflows and replacements are not detected in `consumption_1d`.
- Switching open3e to QoS 1 would close the remaining loss window, but open3e
  does not offer that setting.
