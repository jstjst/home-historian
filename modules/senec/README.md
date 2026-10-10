# senec module

Reads a SENEC.Home battery storage over the local API of its web interface
(`lala.cgi`) and publishes every value to MQTT: power flows, battery, grid
meter, PV inputs, battery modules down to the single cell, temperatures,
states and the unit's configuration. No cloud account is involved.

| | |
|---|---|
| Profile | `senec` |
| Topics | `<prefix>/senec/<device>/<SECTION>/<FIELD>[/<index>]` |
| Device (`device_key`) | `senec/home`, the name from `SENEC_DEVICE` |
| Timestamp | from the module: the moment the answer arrived, shared by all values of a pass |
| MQTT QoS | 1 |
| Status | tested on a SENEC.Home V3 hybrid duo LFP, NPU image 2608 |

## Prerequisites

- A SENEC.Home whose web interface answers in the local network (open
  `https://<address>` in a browser). According to the Home Assistant
  integration named under [Sources](#sources), the V2.x and the V3 have it
  and the SENEC.Home 4 does not.
- The unit's **address**. Give it a fixed one in the router; the module has
  no discovery.

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

**`all` is a lot.** The tested unit has 30 sections with 435 fields, which
come to 963 values per pass: 1.4 million rows a day at the default interval.
More than half of them never change on a given installation (the wallbox and
socket sections without a wallbox, the grid code parameters, the commissioning
settings). `SENEC_SECTIONS` narrows it down; the unit lists its sections with

```bash
curl -sk --data '{"DEBUG":{"SECTIONS":""}}' https://<address>/lala.cgi
```

**Serial numbers are among the values.** The unit reports its own, those of
the battery modules and inverters, and its MAC address (`FACTORY`, `BAT1`,
`BMS`, `PV1`, `PWR_UNIT`, `WALLBOX`, `WIZARD`). With `all` they are published
and stored like everything else.

**HTTPS without verification.** Current firmware redirects HTTP to HTTPS and
presents a certificate that SENEC issues for the name `senecGuiSelf` from a
root of its own, so there is nothing to verify it against. The connection is
encrypted, but the module cannot tell the unit from something else answering
at its address. `http://` is accepted for firmware that still serves plain
HTTP; that was not tested, the tested unit redirects.

## Master data

[seed.sql](seed.sql) adds the device type `senec_v3`, the device `senec/home`
and curated metrics. It is loaded automatically on first start; on a database
that already exists, apply it by hand (the command is in its header) and
follow up with `docker compose restart ingest`, because the ingester reads
the list of devices at startup.

With another `SENEC_DEVICE`, insert the row yourself:

```sql
INSERT INTO devices (type, device_key, name) VALUES
  ('senec_v3', 'senec/cellar', 'SENEC.Home V3');
```

## The values

The key of a value is the unit's own name for it: `<SECTION>.<FIELD>`. A
field that holds an array becomes one value per element, numbered **from 0**
as in the unit's answer: `PM1OBJ1.U_AC.0` is the voltage on L1,
`BMS.CELL_VOLTAGES_MODULE_A.15` the sixteenth cell of the first module.

The unit sends every value with its type, as `<type>_<hex>`, and the module
decodes that and nothing more:

| Type | Meaning | Example | Published as |
|---|---|---|---|
| `fl` | float, the four bytes of an IEEE 754 single | `fl_4404AEB9` | `530.73004` |
| `u8`, `u1`, `u3`, `u6` | unsigned integer of 8, 16, 32, 64 bits | `u1_0A30` | `2608` |
| `i8`, `i1`, `i3`, `i6` | signed integer, two's complement | `i3_FFFFFFFF` | `-1` |
| `st` | text | `st_V3 LV` | `V3 LV` |

A float is published with the fewest digits that read back as the same
single, so the noise of the unit's arithmetic stays visible (`530.73004`).
Infinity and NaN are skipped.

**No scaling.** Values arrive as the unit holds them. Most are in base units,
some are not: `BMS.SYSTEM_SOC` counts in tenths of a percent, `BMS.MAX_TEMP`
and the `AMPACE` temperatures in tenths of a degree, the cell voltages in
millivolts.

The curated metrics of [seed.sql](seed.sql) are those whose meaning and unit
were checked on the tested unit:

| Keys | Quantity | Unit |
|---|---|---|
| `ENERGY.GUI_HOUSE_POW`, `GUI_INVERTER_POWER` | house consumption, PV power | W |
| `ENERGY.GUI_GRID_POW` | grid power, **positive = import**, negative = export | W |
| `ENERGY.GUI_BAT_DATA_POWER`, `…_CURRENT` | battery, **positive = charging**, negative = discharging | W, A |
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

Everything else arrives as a **stub**: stored as text under its key, without
a name or unit, until someone curates it (see
[architecture.md](../../docs/architecture.md)). That is the larger part, 835
of the 963 values on the tested unit.

**No energy counters.** On this firmware the section `STATISTIC` is empty;
according to the Home Assistant integration it held the kWh totals until
SENEC removed them. Energy has to be integrated from the power values, and
the polling interval sets how well that works for loads that come and go
within a minute.

## Behaviour

- **Every pass publishes every value**, including those that never change.
  Each reading is a new measurement with a new time.
- **Two requests per pass**: one for the list of sections, one for all of
  them. The list is asked every time, so a firmware update that adds a
  section needs no restart. With a selection in `SENEC_SECTIONS` it is one.
- **One HTTP session for all passes.** The unit sets a new cookie with every
  answer; the module returns it and keeps the connection open, so that it
  does not look like a new visitor every minute. After a failed pass it
  starts over with a fresh session.
- **What cannot be published is skipped and logged once**: a value of a type
  the module does not know, the unit's `VARIABLE_NOT_FOUND`, a section from
  `SENEC_SECTIONS` the unit does not have.
- **A broker that is away costs the values after the fifth pass.** Up to
  5000 messages wait in memory and are sent, with their own time, once the
  broker is back; what comes after is dropped and logged. Without that limit
  the queue would grow by several megabytes a minute.
- **Healthcheck** ([healthcheck.py](healthcheck.py)): the service only counts
  as healthy if the unit answered recently, nothing was dropped and the
  broker was connected.
- **Read only.** In `lala.cgi` a field sent with a value is a write. The
  module sends empty sections and nothing else.

## Switching it on

1. Set `SENEC_URL` and add `senec` to `COMPOSE_PROFILES`.
2. On a database that already exists, apply [seed.sql](seed.sql) by hand and
   `docker compose restart ingest`.
3. `docker compose up -d senec`. The log should say
   `… values published from N sections` on every pass.

| Observation | Likely cause |
|---|---|
| `Pass failed (ConnectionError … No route to host)`, or a timeout | wrong address, or the unit is in a network the container cannot reach |
| `Pass failed (HTTPError: 404 … for url: https://…)` | `http://` against firmware that redirects to HTTPS: the redirect turns the request into a GET, which the unit does not answer. Use `https://` |
| `Pass failed (SSLError … WRONG_VERSION_NUMBER)` | `https://` against something that only speaks plain HTTP |
| `Pass failed (the answer is not JSON: '')` | the unit could not read the request; seen for an empty request body |
| `… = '' cannot be published`, for `<SECTION>/OBJECT_NOT_FOUND` | a section in `SENEC_SECTIONS` that the unit does not have |
| values published, none in the database | the device row is missing, or the ingester was not restarted after adding it |

## Sources

The API is not documented by SENEC. What is known about it comes from the
community: the Home Assistant integration
[ha-senec-v3](https://github.com/marq24/ha-senec-v3) and a
[Postman collection](https://documenter.getpostman.com/view/10329335/UVCB9ihZ)
of example requests. Everything stated above was checked against the tested
unit. Two things differ from what those sources suggest: signed integers are
two's complement and must not be read as unsigned, and a request for all
sections at once (377 bytes, answered with 19 kB in 0.4 s) needs no splitting
into smaller ones.
