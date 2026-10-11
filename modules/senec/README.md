# senec module

Reads a SENEC.Home battery storage over the local API of its web interface
(`lala.cgi`) and publishes every value to MQTT: power flows, battery, grid
meter, PV inputs, battery modules down to the single cell, temperatures,
states and the unit's configuration. No cloud account is involved.

| | |
|---|---|
| Profile | `senec` |
| Topics | `<prefix>/senec/<device>/<SECTION>/<FIELD>[/<index>]` |
| Devices (`device_key`) | `senec/<device>`, the name from `SENEC_DEVICE` (default `senec/home`) |
| Timestamp | from the module: the time the answer arrived, shared by all values of a pass |
| MQTT QoS | 1 |
| Status | in use with a SENEC.Home V3 hybrid duo LFP, NPU image 2608 |

## Prerequisites

- A SENEC.Home whose web interface answers in the local network (open
  `https://<address>` in a browser). According to the Home Assistant
  integration named under [Sources](#sources), the V2.x and the V3 have it
  and the SENEC.Home 4 does not.
- The unit's address. Give it a fixed one in the router; the module has no
  discovery.

To check from the machine the stack runs on:

```bash
curl -sk --data '{"ENERGY":{"GUI_HOUSE_POW":""}}' https://<address>/lala.cgi
```

```
{"ENERGY":{"GUI_HOUSE_POW":"fl_4404AEB9"}}
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SENEC_URL` | – | scheme and address of the unit, e.g. `https://192.168.1.50` |
| `SENEC_DEVICE` | `home` | topic segment of the unit; the device is `senec/<name>` |
| `SENEC_SECTIONS` | `all` | sections to read: `all`, or a selection such as `ENERGY,PM1OBJ1,PV1,BMS,TEMPMEASURE` |
| `SENEC_POLL_INTERVAL_SECONDS` | `60` | polling interval, minimum 5 |

With `all`, the tested unit delivers 30 sections with 435 fields, which come
to 963 values per pass, or 1.4 million rows a day at the default interval.
More than half of them never change on a given installation (the wallbox and
socket sections without a wallbox, the grid code parameters, the
commissioning settings). `SENEC_SECTIONS` limits what is read. The unit lists
its sections with

```bash
curl -sk --data '{"DEBUG":{"SECTIONS":""}}' https://<address>/lala.cgi
```

The values include serial numbers: the unit's own, those of the battery
modules and inverters, and its MAC address (`FACTORY`, `BAT1`, `BMS`, `PV1`,
`PWR_UNIT`, `WALLBOX`, `WIZARD`). With `all` they are published and stored
like every other value.

HTTPS is used without certificate verification. Current firmware redirects
HTTP to HTTPS and presents a certificate that SENEC issues for the name
`senecGuiSelf` from a root of its own, so it cannot be verified. The
connection is encrypted, but the module cannot tell the unit from another
host answering at its address. `http://` is accepted for firmware that still
serves plain HTTP; that was not tested, because the tested unit redirects.

## Devices and metrics

[devices.sql](devices.sql) adds the device type `senec_v3` and the device
`senec/home`. With another `SENEC_DEVICE`, insert the row yourself and
`docker compose restart ingest`, because the ingester reads the list of
devices at startup:

```sql
INSERT INTO devices (type, device_key, name) VALUES
  ('senec_v3', 'senec/cellar', 'SENEC.Home V3');
```

[metrics.de.sql](metrics.de.sql) defines every value the tested unit
delivered with `SENEC_SECTIONS=all`, with German display names, in two
groups:

- 146 values whose meaning and unit were checked on the unit; they are listed
  under [Values](#values). The battery module values are defined for all four
  slots, fitted or not. [metrics.en.sql](metrics.en.sql) has English names
  for this group.
- 835 further values, for which only `value_type` is checked against stored
  values. Their names, units and categories are derived from the field names
  and are not checked.

A field that is not in the file is stored as a stub
([docs/architecture.md](../../docs/architecture.md)).

Both files are loaded when the database starts for the first time. On an
existing database:

```bash
for f in devices.sql metrics.de.sql; do
  docker compose exec -T timescaledb sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/senec/$f
done
docker compose restart ingest
```

## Values

The key of a value is the unit's own name for it: `<SECTION>.<FIELD>`. A
field that holds an array becomes one value per element, numbered from 0 as
in the unit's answer: `PM1OBJ1.U_AC.0` is the voltage on L1,
`BMS.CELL_VOLTAGES_MODULE_A.15` the sixteenth cell of the first module.

The unit sends every value with its type, as `<type>_<hex>`. The module
decodes that and changes nothing else:

| Type | Meaning | Example | Published as |
|---|---|---|---|
| `fl` | float, the four bytes of an IEEE 754 single | `fl_4404AEB9` | `530.73004` |
| `u8`, `u1`, `u3`, `u6` | unsigned integer of 8, 16, 32, 64 bits | `u1_0A30` | `2608` |
| `i8`, `i1`, `i3`, `i6` | signed integer, two's complement | `i3_FFFFFFFF` | `-1` |
| `st` | text | `st_V3 LV` | `V3 LV` |

A float is published with the fewest digits that read back as the same
single (`530.73004`). Infinity and NaN are skipped.

Values are not scaled. Most are in base units, some are not:
`BMS.SYSTEM_SOC` counts in tenths of a percent, `BMS.MAX_TEMP` and the
`AMPACE` temperatures in tenths of a degree, the cell voltages in millivolts.

The values whose meaning and unit were checked on the tested unit:

| Keys | Quantity | Unit |
|---|---|---|
| `ENERGY.GUI_HOUSE_POW`, `GUI_INVERTER_POWER` | house consumption, PV power | W |
| `ENERGY.GUI_GRID_POW` | grid power, positive = import, negative = export | W |
| `ENERGY.GUI_BAT_DATA_POWER`, `…_CURRENT` | battery, positive = charging, negative = discharging | W, A |
| `ENERGY.GUI_BAT_DATA_VOLTAGE`, `…_FUEL_CHARGE` | battery voltage, state of charge | V, % |
| `ENERGY.STAT_STATE`, `STAT_HOURS_OF_OPERATION` | system state (a number), operating time | –, h |
| `PM1OBJ1.FREQ`, `U_AC`, `I_AC`, `P_AC`, `P_TOTAL` | meter at the grid connection point, per phase and in total | Hz, V, A, W |
| `PV1.MPP_VOL`, `MPP_CUR`, `MPP_POWER` | the PV inputs | V, A, W |
| `BMS.VOLTAGE`, `CURRENT`, `SOC`, `SOH`, `CYCLES` | per battery module | V, A, %, %, – |
| `BMS.CELL_VOLTAGES_MODULE_A…D`, `MAX_`/`MIN_CELL_VOLTAGE` | per cell, and the extremes of a module | mV |
| `BMS.CELL_TEMPERATURES_MODULE_A…D`, `TEMP_MAX`, `TEMP_MIN` | per sensor, and the extremes of a module | °C |
| `TEMPMEASURE.BATTERY_TEMP`, `CASE_TEMP`, `MCU_TEMP` | the unit's own temperatures | °C |

The signs follow from the unit's own figures, which satisfy
house = PV + grid − battery in every pass. The PV inputs were read at night,
with voltage only; their units are those of the unit's web interface.

The unit has no energy counters on this firmware. The section `STATISTIC` is
empty; according to the Home Assistant integration it held the kWh totals
until SENEC removed them. Energy has to be integrated from the power values,
and the polling interval limits the accuracy for loads that switch within a
minute.

## Behaviour

- Every pass publishes every value, including those that never change. Each
  reading is a new measurement with a new time.
- A pass makes two requests: one for the list of sections, one for all of
  them. The list is requested every time, so a firmware update that adds a
  section needs no restart. With a selection in `SENEC_SECTIONS` a pass makes
  one request.
- One HTTP session serves all passes. The unit sets a new cookie with every
  answer; the module returns it and keeps the connection open. After a failed
  pass it starts a new session.
- What cannot be published is skipped and logged once: a value of a type the
  module does not know, the unit's `VARIABLE_NOT_FOUND`, a section from
  `SENEC_SECTIONS` the unit does not have.
- While the broker is away, up to 5000 messages wait in memory, which is
  about five passes with `all`. They are sent with their own time once the
  broker is back. Further values are dropped and logged. Without the limit
  the queue would grow by several megabytes a minute.
- The module only reads. In `lala.cgi` a field sent with a value is a write;
  the module sends empty sections and nothing else.
- Healthcheck ([healthcheck.py](healthcheck.py)): the service counts as
  healthy only if the unit answered recently, nothing was dropped and the
  broker was connected.

## Commissioning

1. Set `SENEC_URL` and add `senec` to `COMPOSE_PROFILES`.
2. On a database that already exists, apply `devices.sql` and
   `metrics.de.sql` ([Devices and metrics](#devices-and-metrics)).
3. `docker compose up -d senec`. The log should say
   `… values published from N sections` on every pass.

| Observation | Likely cause |
|---|---|
| `Pass failed (ConnectionError … No route to host)`, or a timeout | wrong address, or the unit is in a network the container cannot reach |
| `Pass failed (HTTPError: 404 … for url: https://…)` | `http://` against firmware that redirects to HTTPS: the redirect turns the request into a GET, which the unit does not answer. Use `https://` |
| `Pass failed (SSLError … WRONG_VERSION_NUMBER)` | `https://` against a host that only serves plain HTTP |
| `Pass failed (the answer is not JSON: '')` | the unit could not read the request; seen for an empty request body |
| `… = '' cannot be published`, for `<SECTION>/OBJECT_NOT_FOUND` | a section in `SENEC_SECTIONS` that the unit does not have |
| values published, none in the database | the device row is missing, or the ingester was not restarted after adding it |

## Sources

SENEC does not document the API. What is known about it comes from the
Home Assistant integration
[ha-senec-v3](https://github.com/marq24/ha-senec-v3) and a
[Postman collection](https://documenter.getpostman.com/view/10329335/UVCB9ihZ)
of example requests. The statements above were checked against the tested
unit. Two findings differ from those sources: signed integers are two's
complement and must not be read as unsigned, and a request for all sections
at once (377 bytes, answered with 19 kB in 0.4 s) does not have to be split
into smaller ones.
