# CLAUDE.md

Instructions for Claude Code in this repository. Overview:
[README.md](README.md). Decisions and data model:
[docs/architecture.md](docs/architecture.md). Module contract:
[docs/modules.md](docs/modules.md).

---

## What this project is

**home-historian**: a process historian for the energy devices in a
single-family home (heat pump, PV, battery, meters). Modules read data sources
and publish to MQTT (Mosquitto); the ingester writes to TimescaleDB; Grafana
does the analysis. Observe and analyse, never control.

### Services (`compose.yml`)

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
| `mqtt-explorer`, `pgadmin` | tools | `tools` |

`COMPOSE_PROFILES` in `.env` decides what runs.

---

## How to work here (non-negotiable)

- **Evidence before design.** Schema constraints, API behaviour and library
  internals are verified at the source (source code, real response files,
  actual broker and database tests), not assumed from documentation. Do not
  guess where the answer can be checked. Read the relevant source or real data
  before making an architectural decision.
- **Separate verified from assumed.** Say clearly what was checked and what is
  inference. Name assumptions as such and back them up where possible.
- **Explain trade-offs before building.** For any non-trivial change, lay out
  the options with their costs and benefits first. No silent reworking of
  design decisions.
- **Concise, no over-engineering.** The simplest solution that meets the
  requirement. Deliberately rejected so far: a separate `device_types` table,
  `circuit`/`instance`/`axis` fields, `native_subkey`. New fields, tables or
  abstractions need a reason.
- **Test end to end in the Docker Compose stack** before calling anything done.

## Language

- **Code, comments, log messages and documentation are in English.**
- Write for meaning, not word for word: domain terms use their established
  English form (flow temperature, heating circuit, domestic hot water, grid
  connection point).
- Database identifiers are English too: `measurements`, `v_measurements`,
  `measurements_1h`, `consumption_1d`, `metrics`, `devices`, `native_key`,
  `display_name`, `display_subname`, `category`, `aggregation`. Categories:
  `hc1`–`hc3` (heating circuits), `dhw`, `pv`, `battery`, `grid`, ….

## Python style

- `paho-mqtt`, `psycopg[pool]`, `pygtrie`, `cachetools`, `pydantic-settings`.
  Configuration through `pydantic-settings`, not scattered `os.getenv` calls.
- Lean, readable implementations; no speculative abstractions.
- Honest error handling: no liveness or health checks that report a broken
  state as healthy.

## Core and modules

- **The core (broker, ingester, schema) knows nothing about individual
  devices.** Anything device-specific belongs in a module under
  `modules/<name>/`.
- **Topic contract:** `<prefix>/<module>/<device>/<quantity…>`, scalar
  payload, one value per topic. `<module>/<device>` is the `device_key`, the
  rest is the `native_key` (`/` → `.`). One device = one physical unit; no
  device name inside the `native_key`.
- **Modules ship their master data as `seed.sql`** (idempotent). The schema
  itself creates no devices and no metrics.
- Every module has its own Compose profile and its own variable prefix
  (`OPEN3E_`, `VICARE_`, `MBUS_`).

## Design constraints that are easy to break

- **`measurements` deliberately has no foreign key and no unique constraint.**
  The high-frequency insert must not be slowed down by constraint checks.
  Before adding one: clear it with the user.
- **The broker is the buffer; acknowledge only after the commit.** The
  ingester uses `manual_ack`. When the database is gone it disconnects from
  the broker, which keeps collecting in the persistent session. Redelivered
  messages (DUP flag) are written with `INSERT … WHERE NOT EXISTS`; that
  stands in for the missing unique constraint in exactly this case.
- **`native_key` is the only raw column and exists for the ingester's lookup
  alone.** It can be rebuilt from the MQTT topic. Do not send it as an MQTT
  user property. The only user property is `timestamp`.
- **Time comes from the `timestamp` user property, never from the ingester.**
  The broker sets it on receipt (message-timestamp plugin); if a module sends
  it, the module's value wins. Messages without it are dropped. **Without the
  plugin the ingester writes nothing.**
- **`category` comes from the metric OR the device, via COALESCE.** Data
  points specific to one circuit carry it in `metrics`. Shared meter registers
  do not know their circuit, so it sits on the `device`. Do not unify the two.
- **`value_type` picks the column; a value that does not fit falls back to
  `value_str`**, so a single type anomaly never loses data. A stub is typed
  `str`: its values are stored as text even when they are numbers, and no
  type is guessed (`00000000` parses as a number and is not one). Typing a
  metric `num` or `bool` afterwards needs `db/backfill_value_types.sql` for
  the values already stored; it also refreshes the aggregates, whose policies
  only look back 3 and 7 days.
- **Unknown quantities** are created by the ingester as text-typed stubs
  (`display_name = native_key`, `category = NULL`, `value_type = 'str'`).
  Curating them is manual; existing values pick up name and category
  retroactively.
- **A manually changed `value_type` only takes effect once the metric cache
  expires** (`INGEST_METRIC_CACHE_SECONDS`, 1 h by default), or at once with
  `docker compose restart ingest` (lossless, the broker buffers). Values
  already stored never move by themselves (backfill with an `UPDATE`).
- **`measurements` is compressed after 7 days**, segmented by `device_id,
  metric_id` (lossless; inserts into compressed chunks still work). An
  `UPDATE` there has to carry the `metric_id` as a literal, or it unpacks
  every compressed row and hits TimescaleDB's limit of 100 000 per
  statement; `db/backfill_value_types.sql` shows the pattern. An existing
  database gets compression with `db/enable_compression.sql`.
- **Unknown devices are dropped.** The ingester loads `devices` at startup;
  after adding one, `docker compose restart ingest`.
- **Grafana reads as the role `grafana` (`SELECT` only)**, never as the owner:
  Grafana passes SQL to the database unchecked.
- **Per-feature dedup in the vicare module, no global watermark.** Compare the
  timestamp per feature, or late values of other features are silently
  dropped.
- **The mbus module must not poll faster than every 120 s.** Kamstrup rates
  the MULTICAL 303's battery for that, and the battery cannot be replaced
  without breaking the verification seal. The minimum is enforced in code.
- **Real-time continuous aggregates need `materialized_only = false`**, or the
  current (not yet materialised) edge silently goes missing from dashboards.
- **Ports are bound locally by default** (`BIND_ADDRESS`), because broker and
  tools have no authentication. The open3e module runs without its command
  channel. Do not quietly open either.

## Known pitfalls (verified)

- **`paho-mqtt`'s `publish()` buffers silently when the broker is down.** A
  running process is not a healthcheck; check real broker connectivity
  (`is_connected`).
- **open3e publishes at QoS 0** (not configurable). The broker only buffers
  that for a disconnected client with `queue_qos0_messages true`. Remaining
  gap: QoS 0 values still in the ingester's memory when it stops are gone.
- **Mosquitto: `queue_qos0_messages` needs `max_inflight_messages 0`.** Every
  buffered QoS 0 message permanently uses one inflight slot
  (`db__message_dequeue_first`, 2.0.22); after that all QoS 1 messages are
  stuck until the client reconnects. For the same reason the ingester must not
  send a `ReceiveMaximum`.
- **The broker's timestamp only resolves to a second**, so the duplicate
  check includes the value.
- **PyViCare fails permanently on an empty token file** (`EOFError` is not
  caught). The vicare module deletes unreadable token files before logging in.
- **PyViCare sends the login and the token renewal without a timeout** (the
  fetch has 31 s; still so in 2.63.0). A peer that accepts the connection and
  stays silent blocks the pass for good, and Docker does not restart an
  unhealthy container. The vicare module therefore exits on a hung pass.
- **A restart of the vicare module duplicates unchanged values.** Its
  per-feature timestamps are in memory on purpose (the first pass refills a
  rebuilt database). The ingester only checks DUP redeliveries, so every
  feature unchanged since the last pass is stored a second time.
- **Importing `meterbus` configures the root logger** (`basicConfig` in its
  `serial.py`). The mbus module therefore calls `basicConfig(force=True)`.
- **pyMeterBus 0.8.5 ships without its command-line tools** (the entry points
  import `meterbus.tools`, which is not in the package).
- **Kamstrup's E8/E9 records carry no scale; their resolution follows the
  volume's** (CCC code). The mbus module scales them from the volume VIF of
  the same telegram, for manufacturer `KAM` only. No setting for B or CCC:
  the telegram's VIFs already carry both.
- **M-Bus needs 8E1, and the Raspberry Pi's mini UART (`/dev/ttyS0`) has no
  parity bit.** Nothing fails; the meter just does not answer.
- **Compose cannot leave a `devices` entry out.** For the M-Bus simulator
  (TCP) the mbus service gets `MBUS_SERIAL_DEVICE=/dev/null`.
- **A Compose `tmpfs` belongs to root** unless given `uid=`/`gid=`; a module
  running as a user cannot write its heartbeat there otherwise.
- **Grafana + `includeAll` quotes values twice** → `LIKE '${category:csv}'`
  instead of `LIKE '$category'`.
- **Grouping by `display_name` alone** collapses actual/min/max/average into
  one series → concatenate with `display_subname`.
- **Grafana's PostgreSQL query builder is broken for browsing tables**
  (upstream bug) → write the SQL by hand.
- **`vcan` needs Linux and `linux-modules-extra`** (not preinstalled in slim
  cloud images, and needed again after a kernel update). The simulator only
  runs with the `vcan` module loaded.
- **open3e does not set the CAN bit rate** (python-can's SocketCAN backend
  ignores `bitrate`). The host configures the interface, 250 kbit/s:
  `modules/open3e/setup-can.sh` or systemd-networkd.
- **A CAN link under systemd-networkd never counts as online** (checked with
  `vcan`); its `.network` file needs `RequiredForOnline=no`, or
  `network-online.target` (Docker waits for it) hangs until the timeout.
- **open3e's `-a` (all data points) reads once and exits**; `-t` only
  repeats a `-r` list, and waits that long *after* each pass. For
  `OPEN3E_DIDS=all` the service's command therefore starts `open3e -a` in
  a loop. Without the data point lists of a scan, `-a` asks for all 1564
  data points open3e knows and publishes an error topic for each miss;
  the command refuses to start then.
- **open3e resolves `dpList` from its working directory**, not from where
  `devices.json` lies → `/config/Open3Edatapoints_<address>.py`.
- **`docker compose run -v` does not replace a mount the service already has
  at the same path**; the service's (read-only) one stays.
- **Reloading a dashboard with an unchanged `version` is unreliable** → after
  editing the JSON, `docker compose restart grafana`.
- **Schema init ONLY runs on an empty volume** → apply schema changes by hand,
  or `docker compose down -v` (deletes the measurements).
- **Compose evaluates `${VAR:?}` even for services of inactive profiles.**
  The passwords from `.env.example` therefore always have to be set.
- **`docker compose --profile X` on the command line replaces
  `COMPOSE_PROFILES` from `.env`** instead of adding to it. A `down` with
  `--profile` leaves the other profiles' containers running.

## Repo conventions

- Deployment is Docker Compose throughout (**one** `compose.yml`, profiles
  rather than several files).
- Secrets live in `.env` only (git-ignored). No secrets in tracked files.
- `local/` is git-ignored: private deployment variants and notes that are not
  published.
