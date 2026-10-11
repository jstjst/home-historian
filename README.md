# home-historian

A **process historian for the energy devices in a single-family home**: heat
pump, PV inverter, battery storage, heat and electricity meters. It reads the
devices every few seconds, keeps every raw value for good in a time-series
database, and makes it all queryable with SQL and Grafana.

The name says what it is. In industrial control systems (SCADA), the archive
that records every process value of a plant without gaps is called a
*historian*. This is one of those, scaled down to a house.

```
 Modules (data sources)           Core                                Analysis
 ──────────────────────           ────                                ────────
 open3e  (CAN, E3 devices) ─┐
 vicare  (cloud API)       ─┼─► Mosquitto ─► ingest ─► TimescaleDB ─► Grafana / SQL
 mbus    (M-Bus meters)    ─┤   (MQTT 5,     (Python)   (PostgreSQL)
 senec   (local API)       ─┤   buffers)
 … your own module         ─┘
```

## What it is for, and what it is not

| | home-historian | Home Assistant | Energy management (evcc, OpenEMS, …) |
|---|---|---|---|
| Purpose | **observe and analyse** | automate the home | **control** energy flows |
| Acts on devices | no, read-only | yes | yes |
| Time resolution | seconds, as fast as the device delivers | event-driven | control cycle |
| Raw values | kept permanently | detailed history for a limited time, then condensed statistics | mostly key figures |
| Analysis | SQL on a normalised schema, Grafana | UI, statistics cards | UI |
| Depth per device | internal diagnostics too (refrigerant circuit, sensor states, fault memory) | whatever the integration exposes | whatever control needs |

The question behind this project is not "what should the house do next?" but
**"what exactly did the system do, and why?"** How a heat pump cycles, when it
defrosts, what the flow temperatures looked like over a whole winter, how much
a buffer tank loses. Answering that takes a fine time grid, untouched raw
values, and a database in which devices and measured quantities can be joined
properly.

home-historian does not replace the other systems; it runs alongside them. It
controls nothing and needs no write access to any device.

## Quick start with the simulator

No hardware required: a simulator plays a heat pump (Vitocal) and a battery
storage unit (Vitocharge) on a virtual CAN bus.

You need Linux (for the virtual CAN bus; on macOS or Windows use a Linux VM)
and Docker with the Compose plugin.

```bash
git clone <repo-url> home-historian && cd home-historian
cp .env.example .env                 # set the passwords
modules/open3e/sim/setup-vcan.sh     # create the virtual CAN bus vcan0
docker compose up -d --build
```

After about a minute:

| What | Where |
|---|---|
| Grafana, dashboards "home-historian - Overview" and "- Stubs" | <http://localhost:3000> (login from `.env`) |
| MQTT explorer (broker host there: `mosquitto`) | <http://localhost:4000> |
| pgAdmin | <http://localhost:5050> |

An MQTT client outside the stack, such as the MQTT Explorer desktop app, can
also connect over WebSockets: protocol `ws://`, host `localhost`, port `9001`,
no user, no TLS. The broker accepts any base path, so the app's default (`ws`)
can stay.

If nothing shows up: `docker compose ps` and
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
  modules.md           the contract for writing your own module
  clear-data.md        discard measurements and uncurated metrics
```

The **core** is the broker and the ingester; neither knows anything about a
particular device. **Modules** are independent programs that read one data
source and publish to MQTT. Supporting a new device means writing a module,
not changing the core. The contract is in [docs/modules.md](docs/modules.md).

| Module | Source | Profile | Status |
|---|---|---|---|
| [open3e](modules/open3e/) | Viessmann E3 devices over the CAN bus (heat pump, battery) | `open3e` | tested against the simulator |
| open3e simulator | virtual CAN bus with two E3 devices | `open3e-sim` | – |
| [vicare](modules/vicare/) | Viessmann ViCare cloud API | `vicare` | flattening and token handling tested offline |
| [mbus](modules/mbus/) | wired M-Bus meters over a serial port (Kamstrup MULTICAL 303 heat meters) | `mbus` | tested against the simulator |
| mbus simulator | M-Bus meters behind a TCP port | `mbus-sim` | – |
| [senec](modules/senec/) | SENEC.Home battery storage over its local API (`lala.cgi`) | `senec` | tested on a SENEC.Home V3 hybrid duo |

## Configuration

Everything lives in `.env` (template: [.env.example](.env.example)). The
switches that matter most:

| Variable | Meaning |
|---|---|
| `COMPOSE_PROFILES` | what runs: `db`, `open3e`, `open3e-sim`, `vicare`, `mbus`, `mbus-sim`, `senec`, `tools` |
| `BIND_ADDRESS` | `127.0.0.1` (default) = local only, `0.0.0.0` = reachable from the LAN |
| `MQTT_TOPIC_PREFIX` | top-level topic segment of all modules |
| `INGEST_DATABASE_URL` | a database on another host (then drop the `db` profile) |

**Devices have to be registered.** The ingester only stores values from
devices listed in the `devices` table. Modules ship their master data as a
`seed.de.sql`, loaded automatically on first start. Where the devices depend on
the installation (vicare, mbus), the seed only brings the metrics and the
module's README names the rows to insert. The display names in the seeds are
German; a module's `seed.en.sql`, where there is one, puts English names over
them.

**The schema is only applied to an empty volume.** The files under
`db/initdb/` run once, when the database starts for the first time. Later
changes are applied by hand, or you discard volume and measurements with
`docker compose down -v`. A database from before the schema compressed its
raw data gets that with
[db/enable_compression.sql](db/enable_compression.sql). To discard only the
measurements and the uncurated metrics, see
[docs/clear-data.md](docs/clear-data.md).

## Security

- The broker runs **without authentication**, and so do the MQTT explorer and
  pgAdmin. That is why every port is bound to `127.0.0.1` by default.
- Before setting `BIND_ADDRESS=0.0.0.0`, give the broker a user and password
  ([mosquitto.conf](mosquitto/config/mosquitto.conf)) and set `MQTT_USERNAME` /
  `MQTT_PASSWORD`.
- Grafana queries the database through a role that can only read.
- The open3e module starts without open3e's command channel, through which
  data points on the device could be **written**.

## Limitations

- **Not a controller** and not an alarm system.
- **Outages:** when the database is unreachable the broker buffers for up to
  7 days and delivers afterwards. Values a module sends at MQTT QoS 0 (open3e)
  can be lost within a window of about two seconds if the ingester stops at
  exactly that moment. Details in [docs/architecture.md](docs/architecture.md).
- **Raw data is kept for good.** Chunks older than 7 days are compressed
  (lossless); nothing is ever deleted, there is no retention.
- **Curating metrics is manual work.** Unknown quantities are stored, but they
  only get a name, unit and category through an entry in `metrics`. The
  dashboard "home-historian - Stubs" lists what is waiting. A metric typed
  `num` or `bool` afterwards also needs `db/backfill_value_types.sql`.
- Device types and categories are enums in the schema and so far cover the
  installations this project grew out of.

## Licence and credits

GPL-3.0, see [LICENSE](LICENSE).

This project stands on the shoulders of
[open3e](https://github.com/open3e/open3e) (access to Viessmann E3 devices;
the simulation data comes from its integration test),
[virtualE3](https://github.com/philippoo66/vitualE3) (simulator),
[PyViCare](https://github.com/openviess/PyViCare),
[pyMeterBus](https://github.com/ganehag/pyMeterBus) (M-Bus decoding; the
simulated telegram comes from the tests of
[wmbusmeters](https://github.com/wmbusmeters/wmbusmeters)),
[Eclipse Mosquitto](https://mosquitto.org),
[TimescaleDB](https://www.timescale.com) and [Grafana](https://grafana.com).
Viessmann, Vitocal, Vitocharge, ViCare, Kamstrup and MULTICAL are trademarks
of their owners; this project is not affiliated with the manufacturers.
