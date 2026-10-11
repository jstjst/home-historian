# home-historian

A process historian for the energy devices in a single-family home: heat
pump, PV inverter, battery storage, heat and electricity meters. It reads the
devices at intervals of seconds to minutes, stores every raw value in a
time-series database and makes the data available to SQL and Grafana.

A historian is the part of an industrial control system (SCADA) that records
every process value of a plant. This project does the same for a house. It
reads only and does not control any device.

```
 Modules (data sources)           Core                                Analysis
 ──────────────────────           ────                                ────────
 open3e  (CAN, E3 devices) ─┐
 vicare  (cloud API)       ─┼─► Mosquitto ─► ingest ─► TimescaleDB ─► Grafana / SQL
 mbus    (M-Bus meters)    ─┤   (MQTT 5,     (Python)   (PostgreSQL)
 senec   (local API)       ─┤   buffers)
 … your own module         ─┘
```

## Scope

| | home-historian | Home Assistant | Energy management (evcc, OpenEMS, …) |
|---|---|---|---|
| Purpose | observe and analyse | automate the home | control energy flows |
| Acts on devices | no, read-only | yes | yes |
| Time resolution | as fast as the device delivers | event-driven | control cycle |
| Raw values | kept permanently | detailed history for a limited time, then condensed statistics | mostly key figures |
| Analysis | SQL on a normalised schema, Grafana | UI, statistics cards | UI |
| Depth per device | internal diagnostics too (refrigerant circuit, sensor states, fault memory) | whatever the integration exposes | whatever control needs |

The project is meant for questions about what a system did: how a heat pump
cycles, when it defrosts, what the flow temperatures were over a winter, how
much heat a buffer tank loses. That needs a fine time grid, unmodified raw
values and a database in which devices and measured quantities can be joined.

home-historian runs alongside the other systems and does not replace them.
It needs no write access to any device.

## Quick start with the simulator

No hardware is required: a simulator answers as a heat pump (Vitocal) and a
battery storage unit (Vitocharge) on a virtual CAN bus.

Requirements: Linux (for the virtual CAN bus; on macOS or Windows use a Linux
VM) and Docker with the Compose plugin.

```bash
git clone https://github.com/jstjst/home-historian.git && cd home-historian
cp .env.example .env                 # set the passwords
modules/open3e/sim/setup-vcan.sh     # create the virtual CAN bus vcan0
docker compose up -d --build
```

After about a minute:

| What | Where |
|---|---|
| Grafana, dashboards "home-historian - Overview" and "home-historian - Stubs" | <http://localhost:3000> (login from `.env`) |
| MQTT explorer (broker host there: `mosquitto`) | <http://localhost:4000> |
| pgAdmin | <http://localhost:5050> |

An MQTT client outside the stack, such as the MQTT Explorer desktop app, can
connect over WebSockets: protocol `ws://`, host `localhost`, port `9001`, no
user, no TLS. The broker accepts any base path, so the app's default (`ws`)
can stay.

If no values appear: `docker compose ps` and
`docker compose logs open3e ingest`.

## Layout

```
compose.yml            one stack; profiles in .env decide what runs
mosquitto/             MQTT broker with the timestamp plugin
ingest/                MQTT -> database
db/initdb/             schema and read-only role
db/backfill_value_types.sql  move stored values after typing a metric
db/enable_compression.sql    compression for a database created without it
grafana/               data source and dashboards (provisioned)
modules/
  open3e/              Viessmann E3 devices over CAN (+ simulator in sim/)
  vicare/              Viessmann ViCare cloud API
  mbus/                wired M-Bus meters, e.g. heat meters (+ simulator in sim/)
  senec/               SENEC.Home battery storage, local API
docs/
  architecture.md      decisions and their reasons, data model
  modules.md           the contract for writing a module
  clear-data.md        discard measurements and uncurated metrics
```

The core is the broker and the ingester. Neither contains anything specific
to a device. A module is an independent program that reads one data source
and publishes to MQTT. Supporting a new device means writing a module; the
core stays unchanged. The contract is in [docs/modules.md](docs/modules.md).

| Module | Source | Profile | Status |
|---|---|---|---|
| [open3e](modules/open3e/) | Viessmann E3 devices over the CAN bus (heat pump, battery) | `open3e` | in use on a Vitocal 250-A |
| open3e simulator | virtual CAN bus with two E3 devices | `open3e-sim` | – |
| [vicare](modules/vicare/) | Viessmann ViCare cloud API | `vicare` | in use with a Vitocal 250-A |
| [mbus](modules/mbus/) | wired M-Bus meters over a serial port (Kamstrup MULTICAL 303 heat meters) | `mbus` | in use with four MULTICAL 303 |
| mbus simulator | M-Bus meters behind a TCP port | `mbus-sim` | – |
| [senec](modules/senec/) | SENEC.Home battery storage over its local API (`lala.cgi`) | `senec` | in use with a SENEC.Home V3 hybrid duo |

Every module's README has the same sections: prerequisites, configuration,
devices and metrics, values, behaviour, commissioning.

## Configuration

All settings are in `.env` (template: [.env.example](.env.example)). The
general ones:

| Variable | Meaning |
|---|---|
| `COMPOSE_PROFILES` | what runs: `db`, `open3e`, `open3e-sim`, `vicare`, `mbus`, `mbus-sim`, `senec`, `tools` |
| `BIND_ADDRESS` | `127.0.0.1` (default) = local only, `0.0.0.0` = reachable from the LAN |
| `MQTT_TOPIC_PREFIX` | top-level topic segment of all modules |
| `INGEST_DATABASE_URL` | a database on another host (then drop the `db` profile) |

### Devices and metrics

The ingester only stores values of devices listed in the `devices` table. A
module brings two SQL files, which are loaded when the database starts for
the first time:

| File | Content |
|---|---|
| `modules/<name>/devices.sql` | the module's device type, and its devices where their keys are fixed (open3e simulator, senec) |
| `modules/<name>/metrics.de.sql` | metric definitions: name, unit, category and type for each key the module publishes |

Where the device keys depend on the installation (vicare, mbus, a scanned
open3e system), the module's README gives the rows to insert. A key without
a definition is stored as a text-typed stub until it is curated; the
dashboard "home-historian - Stubs" lists these.

The display names in `metrics.de.sql` are German. `metrics.en.sql`, where a
module has one, replaces them with English names and is applied by hand. The
dashboard "home-historian - Overview" filters one panel by a German name.

### Schema changes

The files under `db/initdb/` and the modules' SQL files run once, when the
database starts on an empty volume. Later changes are applied by hand, or
the volume is discarded together with the measurements
(`docker compose down -v`).

- A database created before compression was added to the schema:
  [db/enable_compression.sql](db/enable_compression.sql).
- To discard only the measurements and the uncurated metrics:
  [docs/clear-data.md](docs/clear-data.md).

## Security

- The broker, the MQTT explorer and pgAdmin run without authentication. For
  that reason every port is bound to `127.0.0.1` by default.
- Before setting `BIND_ADDRESS=0.0.0.0`, give the broker a user and password
  ([mosquitto.conf](mosquitto/config/mosquitto.conf)) and set `MQTT_USERNAME`
  and `MQTT_PASSWORD`.
- Grafana queries the database through a role that can only read.
- The open3e module starts without open3e's command channel, through which
  data points on the device could be written.

## Limitations

- No control and no alarms.
- Outages: when the database is unreachable the broker buffers for up to
  7 days and delivers afterwards. Values a module sends at MQTT QoS 0
  (open3e) can be lost within a window of about two seconds if the ingester
  stops at that moment. Details in
  [docs/architecture.md](docs/architecture.md).
- No retention: raw data is never deleted. Chunks older than 7 days are
  compressed without loss.
- Curating metrics is manual. Unknown quantities are stored, but they get a
  name, unit and category only through an entry in `metrics`. A metric typed
  `num` or `bool` after values were stored also needs
  [db/backfill_value_types.sql](db/backfill_value_types.sql).
- Categories are an enum in the schema and cover the installation this
  project was written for.

## Licence and credits

GPL-3.0, see [LICENSE](LICENSE).

The project uses
[open3e](https://github.com/open3e/open3e) (access to Viessmann E3 devices;
the simulation data comes from its integration test),
[virtualE3](https://github.com/philippoo66/vitualE3) (simulator),
[PyViCare](https://github.com/openviess/PyViCare),
[pyMeterBus](https://github.com/ganehag/pyMeterBus) (M-Bus decoding; the
simulated telegram comes from the tests of
[wmbusmeters](https://github.com/wmbusmeters/wmbusmeters)),
[Eclipse Mosquitto](https://mosquitto.org),
[TimescaleDB](https://www.timescale.com) and [Grafana](https://grafana.com).
Viessmann, Vitocal, Vitocharge, ViCare, Kamstrup, MULTICAL and SENEC are
trademarks of their owners. This project is not affiliated with the
manufacturers.
