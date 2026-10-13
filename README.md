# home-historian

A process historian for the technical systems of a home, such as heating, PV,
battery storage and meters. It reads the devices at intervals of seconds to
minutes, stores every raw value in a time-series database and makes the data
available to SQL and Grafana.

A historian is the part of an industrial control system (SCADA) that records
every process value of a plant. This project does the same for a home. It
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

The project is meant for questions about what a system did: how a heat pump
cycles, when it defrosts, what the flow temperatures were over a winter, how
much heat a buffer tank loses. That needs a fine time grid, unmodified raw
values and a database in which devices and measured quantities can be joined.

It was written for a single-family house and works the same way for a flat.
Several flats or houses can be recorded in one installation within limits:
the ingester tells devices apart by their topic path, and any number of
devices of one type share their metric definitions. The database and Grafana
do not separate them (see [Limitations](#limitations)).

home-historian can run alongside home automation and energy management
systems. It needs no write access to any device. The table shows how the
purposes differ:

| | home-historian | Home Assistant | Energy management (evcc, OpenEMS, …) |
|---|---|---|---|
| Purpose | record and analyse | automate and operate the home | control energy flows |
| Acts on devices | no | yes | yes |
| Stored data | every raw value, permanently | recent history in detail, statistics for the long term | what control and its UI need |
| Access to the data | SQL, Grafana | its own UI | its own UI |

## Try it out with the simulated devices

The stack runs without hardware. With the profiles of `.env.example`,
[virtualE3](https://github.com/lgraf/vituale3) answers on a virtual CAN
bus as two Viessmann E3 devices, a Vitocal heat pump and a Vitocharge battery
storage, with fixed values from open3e's integration test. The open3e module
reads three data points from them every 60 s, and the values pass through the
broker and the ingester into the database.

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
| Grafana, dashboards "home-historian - Overview", "home-historian - Metrics" and "home-historian - Stubs" | <http://localhost:3000> (login from `.env`) |
| MQTT explorer (broker host there: `mosquitto`) | <http://localhost:4000> |
| pgAdmin | <http://localhost:5050> |

If no values appear: `docker compose ps` and
`docker compose logs open3e ingest`.

Further options:

- Other E3 devices can be simulated from a capture of a real system
  ([modules/open3e](modules/open3e/README.md#simulator)).
- A second simulator answers as M-Bus heat meters behind a TCP port
  ([modules/mbus](modules/mbus/README.md#simulator)).
- The vicare and senec modules have no simulator.

## Modules

The core is the broker ([Eclipse Mosquitto](https://mosquitto.org) with its
[message-timestamp plugin](https://github.com/eclipse-mosquitto/mosquitto/tree/v2.0.20/plugins/message-timestamp))
and the ingester. Neither contains anything specific to a device. A module is
an independent program that reads one data source and publishes to MQTT.
Supporting a new device means writing a module; the core stays unchanged.
[docs/modules.md](docs/modules.md) describes what a module has to provide.

| Module | Source | Uses | Profile | Status |
|---|---|---|---|---|
| [open3e](modules/open3e/) | Viessmann E3 devices over the CAN bus (heat pump, battery) | [open3e](https://github.com/open3e/open3e) | `open3e` | in use on a Vitocal 250-A |
| open3e simulator | virtual CAN bus with two E3 devices | [virtualE3](https://github.com/lgraf/vituale3) | `open3e-sim` | – |
| [vicare](modules/vicare/) | Viessmann ViCare cloud API | [PyViCare](https://github.com/openviess/PyViCare) | `vicare` | in use with a Vitocal 250-A |
| [mbus](modules/mbus/) | wired M-Bus meters over a serial port (Kamstrup MULTICAL 303 heat meters) | [pyMeterBus](https://github.com/ganehag/pyMeterBus) | `mbus` | in use with four MULTICAL 303 |
| mbus simulator | M-Bus meters behind a TCP port | a telegram from the tests of [wmbusmeters](https://github.com/wmbusmeters/wmbusmeters) | `mbus-sim` | – |
| [senec](modules/senec/) | SENEC.Home battery storage over its local API (`lala.cgi`) | the API as described by [ha-senec-v3](https://github.com/marq24/ha-senec-v3) | `senec` | in use with a SENEC.Home V3 hybrid duo |

Every module's README has the same sections: prerequisites, configuration,
devices and metrics, values, behaviour, commissioning.

Storage and analysis are [TimescaleDB](https://github.com/timescale/timescaledb)
and [Grafana](https://grafana.com) (profile `db`). The profile `tools` adds
[MQTT Explorer](https://github.com/Smeagolworms4/MQTT-Explorer) as a web
application and [pgAdmin](https://www.pgadmin.org).

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
dashboard "home-historian - Stubs" lists these. "home-historian - Metrics"
lists every metric, charts a selection, and edits the name, unit, category
and note of a metric.

The display names in `metrics.de.sql` are German. `metrics.en.sql`, where a
module has one, replaces them with English names and is applied by hand.

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

- No control. Alerts can be set up with Grafana's alerting; the repository
  contains no alert rules.
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
- One installation per database. There is no column for a tenant or a
  building, the categories (heating circuits 1 to 3, domestic hot water, PV,
  battery, grid, …) are an enum that describes one installation, and the
  Grafana role reads every row.

## Documentation

- [docs/architecture.md](docs/architecture.md): the decisions and their
  reasons, the data model.
- [docs/modules.md](docs/modules.md): what a module has to provide.
- [docs/clear-data.md](docs/clear-data.md): discarding measurements and
  uncurated metrics.

## Licence

GPL-3.0, see [LICENSE](LICENSE). All trademarks are the property of their
owners. This project is not affiliated with the manufacturers of the devices
it reads.
