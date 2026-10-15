# CLAUDE.md

Instructions for Claude Code in this repository. Overview:
[README.md](README.md). Decisions and data model:
[docs/architecture.md](docs/architecture.md). What a module has to provide:
[docs/modules.md](docs/modules.md).

## What this project is

**home-historian**: a process historian for the technical systems of a home
(heating, PV, battery storage, meters). Modules read data sources
and publish to MQTT (Mosquitto); the ingester writes to TimescaleDB; Grafana
does the analysis. The stack reads and never controls.

| Service | Role | Profile |
|---|---|---|
| `mosquitto` | MQTT broker, message-timestamp plugin, buffers for the ingester | – (core) |
| `ingest` | Python ingester, MQTT → TimescaleDB | – (core) |
| `timescaledb` | storage (hypertable + continuous aggregates) | `db` |
| `grafana` | dashboards | `db` |
| `open3e` | module: reads DIDs from Viessmann E3 devices over CAN | `open3e` |
| `open3e-sim` | virtualE3 simulates Vitocal (`0x680`) + Vitocharge (`0x6a1`) on `vcan0` | `open3e-sim` |
| `vicare` | module: polls the ViCare cloud API | `vicare` |
| `mbus` | module: reads wired M-Bus meters (Kamstrup heat meters) over a serial port | `mbus` |
| `mbus-sim` | simulates M-Bus meters behind a TCP port (`socket://mbus-sim:2400`) | `mbus-sim` |
| `senec` | module: reads a SENEC.Home battery storage over its local API (`lala.cgi`) | `senec` |
| `mqtt-explorer`, `pgadmin` | tools | `tools` |

`COMPOSE_PROFILES` in `.env` decides what runs.

## How to work here

- **Evidence before design.** Schema constraints, API behaviour and library
  internals are verified at the source (source code, real response files,
  broker and database tests), not assumed from documentation. Read the
  relevant source or real data before making an architectural decision.
- **Separate verified from assumed.** Say what was checked and what is
  inference. Name assumptions as such.
- **Explain trade-offs before building.** For any non-trivial change, lay out
  the options with their costs and benefits first. No silent reworking of
  design decisions.
- **No over-engineering.** The simplest solution that meets the requirement.
  Rejected so far: a separate `device_types` table,
  `circuit`/`instance`/`axis` fields, `native_subkey`. New fields, tables or
  abstractions need a reason.
- **Test end to end in the Docker Compose stack** before calling anything
  done.

## Conventions

- Code, comments, log messages and documentation are in English. Domain terms
  use their established English form (flow temperature, heating circuit,
  domestic hot water, grid connection point).
- Documentation is factual: statements, numbers and what was checked. No
  filler and no slogans.
- Database identifiers are English: `measurements`, `v_measurements`,
  `measurements_1h`, `consumption_1d`, `metrics`, `devices`, `native_key`,
  `display_name`, `display_subname`, `category`, `aggregation`. Categories:
  `hkDG`, `hkOG`, `hkEG` (heating circuits, named after the floor), `dhw`,
  `pv`, `battery`, `grid`, ….
- The exception: `display_name` and `display_subname` in the modules'
  `metrics.de.sql` are German (HK DG Vorlauftemperatur; Ist/Soll/Status/Min/Max).
  `metrics.en.sql` replaces them with English names where a module has one;
  it is not maintained further.
- Python: `paho-mqtt`, `psycopg[pool]`, `pygtrie`, `cachetools`,
  `pydantic-settings`. Configuration through `pydantic-settings`, not
  scattered `os.getenv` calls. No speculative abstractions.
- Healthchecks must not report a broken state as healthy.
- Deployment is Docker Compose with one `compose.yml` and profiles, not
  several files.
- Secrets are in `.env` only (git-ignored). No secrets, serial numbers or
  addresses of a real installation in tracked files.
- `local/` is git-ignored: private deployment variants and notes.

## Core and modules

- The core (broker, ingester, schema) contains nothing specific to a device.
  Device-specific code and data belong in a module under `modules/<name>/`.
- Topic format: `<prefix>/<device key>/<native key>`, scalar payload, one
  value per topic. The device key has one or more segments, the first one
  the module's name, and is the `device_key`; the rest is the `native_key`
  (`/` → `.`). The ingester finds the device by longest-prefix match. One
  device is one physical unit; no device name inside the `native_key`.
- A module publishes every value its source delivers under the source's own
  name. Name, unit, category and type are assigned in `metrics`, also later
  ([docs/architecture.md](docs/architecture.md)).
- A module ships two SQL files, both repeatable: `devices.sql` (its device
  type and the devices with fixed keys) and `metrics.de.sql` (one
  `INSERT INTO metrics`, nothing else). The schema creates no device types,
  devices or metrics.
- Every module has its own Compose profile and variable prefix (`OPEN3E_`,
  `VICARE_`, `MBUS_`, `SENEC_`), and a README with the same sections:
  prerequisites, configuration, devices and metrics, values, behaviour,
  commissioning.

## Design constraints that are easy to break

- **`measurements` has no foreign key and no unique constraint.** The
  high-frequency insert must not be slowed down by constraint checks. Before
  adding one: clear it with the user.
- **The broker is the buffer; acknowledge only after the commit.** The
  ingester uses `manual_ack`. When the database is gone it disconnects from
  the broker, which keeps collecting in the persistent session. Redelivered
  messages (DUP flag) are written with `INSERT … WHERE NOT EXISTS`; that
  replaces the missing unique constraint in this case only.
- **`native_key` is the only raw column and exists for the ingester's
  lookup.** It can be rebuilt from the MQTT topic. Do not send it as an MQTT
  user property. The only user property is `timestamp`.
- **Time comes from the `timestamp` user property, never from the ingester.**
  The broker sets it on receipt (message-timestamp plugin); if a module sends
  it, the module's value wins. Messages without it are dropped. Without the
  plugin the ingester writes nothing.
- **`category` comes from the metric or the device, via COALESCE.** Data
  points specific to one circuit carry it in `metrics`. Shared meter registers
  do not know their circuit, so it sits on the `device`. Do not unify the two.
- **`value_type` picks the column; a value that does not fit falls back to
  `value_str`**, so a type anomaly never loses data. A stub is typed `str`:
  its values are stored as text even when they are numbers, and no type is
  guessed (`00000000` parses as a number and is not one). Typing a metric
  `num` or `bool` afterwards needs `db/backfill_value_types.sql` for the
  values already stored; it also refreshes the aggregates, whose policies
  only look back 3 and 7 days.
- **Unknown quantities** are created by the ingester as text-typed stubs
  (`display_name = native_key`, `category = NULL`, `value_type = 'str'`).
  Curating them is manual; existing values get name and category
  retroactively.
- **A changed `value_type` takes effect once the metric cache expires**
  (`INGEST_METRIC_CACHE_SECONDS`, 1 h by default), or at once with
  `docker compose restart ingest` (lossless, the broker buffers).
- **`measurements` has chunks of 1 day and is compressed after 1 day**,
  segmented by `device_id, metric_id` (lossless; inserts into compressed
  chunks still work). Shorter chunks compress much worse (measured: 5.0
  bytes per row at 1 day, 8.0 at 6 hours, 25.7 at 1 hour). An `UPDATE` there
  has to carry the `metric_id` as a literal, or it unpacks every compressed
  row and hits TimescaleDB's limit of 100 000 per statement;
  `db/backfill_value_types.sql` shows the pattern. An existing database
  gets both settings with `db/enable_compression.sql`.
- **Unknown devices are dropped.** The ingester loads `devices` at startup;
  after adding one, `docker compose restart ingest`.
- **Grafana reads as the role `grafana` (`SELECT` only)**, never as the
  owner: Grafana passes SQL to the database unchecked. It may write what the
  dashboard "home-historian - Metrics" edits: the table `metric_notes`, and
  the columns `display_name`, `display_subname`, `unit`, `category` and
  `disabled` of `metrics`. Do not grant it more without asking.
- **Definitions edited in Grafana exist in the database only.** A module's
  `metrics.de.sql` overwrites stubs and leaves a named metric alone, so it
  does not undo an edit and does not contain it either.
- **Real-time continuous aggregates need `materialized_only = false`**, or
  the current, not yet materialised edge is missing from dashboards.
- **Ports are bound locally by default** (`BIND_ADDRESS`), because broker and
  tools have no authentication. The open3e module runs without its command
  channel. Do not open either without asking.
- **Per-feature dedup in the vicare module, no global watermark.** Compare
  the timestamp per feature, or late values of other features are dropped.
- **The mbus module must not poll faster than every 120 s.** Kamstrup rates
  the MULTICAL 303's battery for that, and the battery cannot be replaced
  without breaking the verification seal. The minimum is enforced in code.

## Known pitfalls (verified)

### Broker and ingester

- `paho-mqtt`'s `publish()` buffers without an error when the broker is down.
  A running process is not a healthcheck; check broker connectivity
  (`is_connected`).
- paho keeps QoS 1 messages in memory without a limit while the broker is
  away, about 5 kB each. The senec module publishes close to a thousand per
  pass and therefore sets `max_queued_messages`.
- open3e publishes at QoS 0 (not configurable). The broker only buffers that
  for a disconnected client with `queue_qos0_messages true`. Remaining gap:
  QoS 0 values still in the ingester's memory when it stops are lost.
- Mosquitto's `max_queued_messages` counts QoS 1 messages only (2.0.22).
  QoS 0 is queued uncounted; once the limit is reached, new messages of
  both are dropped and the queued ones stay. The buffer lasts for the limit
  divided by the QoS 1 rate, not for the 1 day of the session expiry
  (`persistent_client_expiration`, which caps the ingester's 7 days too), and
  takes about 500 bytes of RAM per queued message, QoS 0 included.
- The limit is per client. Every subscriber with a persistent session that
  goes away fills a queue of its own (about 80 bytes more per message and
  session, the message is stored once). Four forgotten sessions with random
  client ids grew `mosquitto.db` to 2 GB on a 4 GB Raspberry Pi: the broker
  took two minutes to load it, left 27 MB of RAM free, and the machine
  restarted every six minutes.
- Mosquitto: `queue_qos0_messages` needs `max_inflight_messages 0`. Every
  buffered QoS 0 message permanently uses one inflight slot
  (`db__message_dequeue_first`, 2.0.22); after that all QoS 1 messages are
  stuck until the client reconnects. For the same reason the ingester must
  not send a `ReceiveMaximum`.
- The broker's timestamp resolves to a second, so the duplicate check
  includes the value.

### Database and Grafana

- Schema init only runs on an empty volume. Apply schema changes by hand, or
  `docker compose down -v` (deletes the measurements).
- `ALTER TYPE … ADD VALUE` has to be committed before the value is used. The
  modules' `devices.sql` therefore run outside a transaction.
- One `INSERT … ON CONFLICT DO UPDATE` cannot contain the same key twice. A
  `metrics.de.sql` with a duplicate key fails as a whole.
- Grafana + `includeAll` quotes values twice → `LIKE '${category:csv}'`
  instead of `LIKE '$category'`.
- Grouping by `display_name` alone merges actual/min/max/average into one
  series → concatenate with `display_subname`.
- Grafana's PostgreSQL query builder is broken for browsing tables (upstream
  bug) → write the SQL by hand.
- Reloading a dashboard with an unchanged `version` is unreliable → after
  editing the JSON, `docker compose restart grafana`.

### Compose and host

- Compose evaluates `${VAR:?}` even for services of inactive profiles. The
  passwords from `.env.example` therefore always have to be set.
- `docker compose --profile X` on the command line replaces
  `COMPOSE_PROFILES` from `.env`. A `down` with `--profile` leaves the other
  profiles' containers running.
- `docker compose run -v` does not replace a mount the service already has at
  the same path; the service's (read-only) one stays.
- Compose cannot leave a `devices` entry out. For the M-Bus simulator (TCP)
  the mbus service gets `MBUS_SERIAL_DEVICE=/dev/null`.
- A Compose `tmpfs` belongs to root unless given `uid=`/`gid=`; a module
  running as a user cannot write its heartbeat there otherwise.

### open3e

- `vcan` needs Linux and `linux-modules-extra` (not preinstalled in slim
  cloud images, and needed again after a kernel update). The simulator only
  runs with the `vcan` module loaded.
- open3e does not set the CAN bit rate (python-can's SocketCAN backend
  ignores `bitrate`). The host configures the interface, 250 kbit/s:
  `modules/open3e/setup-can.sh` or systemd-networkd.
- A CAN link under systemd-networkd never counts as online (checked with
  `vcan`); its `.network` file needs `RequiredForOnline=no`, or
  `network-online.target` (Docker waits for it) hangs until the timeout.
- open3e's `-a` (all data points) reads once and exits; `-t` only repeats a
  `-r` list and waits that long after each pass. For `OPEN3E_DIDS=all` the
  service's command therefore starts `open3e -a` in a loop. Without the data
  point lists of a scan, `-a` asks for all 1564 data points open3e knows and
  publishes an error topic for each miss; the command refuses to start then.
- open3e resolves `dpList` from its working directory, not from the location
  of `devices.json` → `/config/Open3Edatapoints_<address>.py`.

### vicare

- PyViCare fails permanently on an empty token file (`EOFError` is not
  caught). The module deletes unreadable token files before logging in.
- PyViCare sends the login and the token renewal without a timeout (the
  fetch has 31 s; still so in 2.63.0). A peer that accepts the connection and
  does not answer blocks the pass, and Docker does not restart an unhealthy
  container. The module therefore exits on a hung pass.
- A restart of the module duplicates unchanged values. Its per-feature
  timestamps are kept in memory (the first pass refills a rebuilt database).
  The ingester only checks DUP redeliveries, so every feature unchanged since
  the last pass is stored a second time.

### mbus

- Importing `meterbus` configures the root logger (`basicConfig` in its
  `serial.py`). The module therefore calls `basicConfig(force=True)`.
- pyMeterBus 0.8.5 ships without its command-line tools (the entry points
  import `meterbus.tools`, which is not in the package).
- Kamstrup's E8/E9 records carry no scale; their resolution follows the
  volume's (CCC code). The module scales them from the volume VIF of the same
  telegram, for manufacturer `KAM` only. No setting for B or CCC: the
  telegram's VIFs carry both.
- M-Bus needs 8E1, and the Raspberry Pi's mini UART (`/dev/ttyS0`) has no
  parity bit. No error is reported; the meter does not answer.

### senec

- `lala.cgi` sends signed integers as two's complement (`i3_FFFFFFFF` is -1);
  ha-senec-v3 reads them as unsigned. A field sent with a value is a write:
  the module only sends `{}` and `""`.
- `lala.cgi` reports misses with status 200: `VARIABLE_NOT_FOUND` as a
  field's value, `{"OBJECT_NOT_FOUND":""}` for a section, an empty body for a
  request it cannot read.
- `STATISTIC` is empty on current firmware (NPU image 2608): no energy
  counters locally, energy has to be integrated from power.
- A SENEC redirects HTTP to HTTPS with a certificate that cannot be verified
  (issued for `senecGuiSelf` by SENEC's own root), and the redirect turns the
  POST into a GET that answers 404.
