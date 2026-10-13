# Modules

A module connects one data source: it reads a device or an interface and
publishes the values to MQTT. Timestamps, buffering, storage and analysis are
done by the core.

A module can be written in any language, or consist only of the
configuration of an existing tool (like [open3e](../modules/open3e/)).

## Interface to the core

### Topic

```
<prefix>/<device key>/<native key>
```

| Part | Meaning |
|---|---|
| `<prefix>` | the stack's `MQTT_TOPIC_PREFIX` (default `historian`) |
| `<device key>` | identifies the device. One or more segments, the first one the module's name. It is the `device_key` in the `devices` table. |
| `<native key>` | names the value. One or more segments. With `/` turned into `.` it is the metric's `native_key`. |

The topic does not mark where the device key ends. The ingester compares the
topic with the `device_key` of every row in `devices` and takes the longest
one that matches; what follows is the native key. A module therefore
publishes the complete device key in every topic, and the row in `devices`
has to contain that path.

| Module | Topic after the prefix | `device_key` | `native_key` |
|---|---|---|---|
| open3e | `open3e/vitocal/268_FlowTemperatureSensor/SensorStatus/ID` | `open3e/vitocal` | `268_FlowTemperatureSensor.SensorStatus.ID` |
| mbus | `mbus/hc1/04_06` | `mbus/hc1` | `04_06` |
| vicare | `vicare/<gatewayId>/<deviceId>/heating.circuits.1.heating.curve/slope` | `vicare/<gatewayId>/<deviceId>` | `heating.circuits.1.heating.curve.slope` |

The vicare module's device key has three segments, because the API
identifies a device by gateway and device id together.

- One device is one physical unit. If a module reads several devices, each
  gets a device key of its own.
- The native key contains no device name. It has to be the same for every
  device of a type, so that they share their metric definitions.
- Topic names have to stay stable. To the database, a renamed topic is a new
  metric.

### What to publish

Every value the source delivers, under the source's own name for it. A module
does not select, rename or convert values; name, unit and category are
assigned in the database (`metrics`), also for values whose meaning is not
known yet. The reasons are in
[architecture.md](architecture.md#modules-publish-everything-meaning-is-assigned-in-the-database).
A setting that limits what is read is useful where a source delivers a large
number of values.

### Payload

A single scalar value as text: `21.5`, `true`, `no_error`. The module splits
compound values into separate topics. A JSON object with several fields
cannot be stored as time series by the core.

Which column a value goes to is decided by the metric's `value_type`; an
unknown key is stored as text at first.

### Timestamp

The broker stamps every message on receipt, to the second. A module normally
sends no time.

If the module knows the time of measurement better, for example because it
fetches values with a delay, it sends it as the MQTT 5 user property
`timestamp` in ISO 8601 with a time zone (`2026-01-31T12:00:00.000Z`). That
value takes precedence.

`timestamp` is the only user property. Unit, type and name are stored in
`metrics`; sending them with the message would create a second source for
the same information.

### Delivery

- QoS 1, so that the broker holds the values while the ingester is down.
  QoS 0 is buffered as well but has a loss window of about two seconds, and
  the broker's queue limit does not count it, see
  [architecture.md](architecture.md).
- Not retained. An old value delivered again on subscribing would be a
  duplicate in the time series.
- Send only what changed or was newly measured. Repeating the same value with
  the same time of measurement produces duplicate rows: the ingester only
  checks messages the broker redelivers, not what a module sends twice. For
  this reason the vicare module remembers the last timestamp it sent per
  quantity. It keeps that in memory, so its restart repeats unchanged values
  once (see its [README](../modules/vicare/README.md)).

### Devices and metrics

The ingester drops messages of devices it does not know, and it stores a key
without a definition as a text-typed stub. A module therefore brings two SQL
files, which [compose.yml](../compose.yml) loads when the database starts for
the first time:

| File | Content |
|---|---|
| `devices.sql` | the module's device type (`ALTER TYPE device_type ADD VALUE IF NOT EXISTS '<type>';`) and its rows in `devices`, where the keys are fixed. If the keys depend on the installation (serial numbers, addresses), the module's README gives the row to insert. |
| `metrics.de.sql` | one `INSERT INTO metrics` with a row per key the module knows: name, unit, category, value type, aggregation. Optional; every other key becomes a stub. |

Both files have to be repeatable. `devices.sql` uses
`ON CONFLICT … DO NOTHING`; `metrics.de.sql` overwrites stubs only, so that
definitions changed by hand stay. [modules/mbus/](../modules/mbus/) is the
shortest example of both.

The display names in `metrics.de.sql` (`display_name`, `display_subname`) are
German, as in the installation they come from. A `metrics.en.sql` next to it
replaces them with English names. It is applied by hand and does not cover
every row in every module.

## Adding a module

1. A directory `modules/<name>/` with a `Dockerfile`, the program,
   `devices.sql`, `metrics.de.sql` and a `README.md` with the sections of
   the existing modules (prerequisites, configuration, devices and metrics,
   values, behaviour, commissioning).
2. A service in [compose.yml](../compose.yml), in the "Modules" section:
   - its own profile, `profiles: ["<name>"]`,
   - broker at `mosquitto:1883` (on the host network: `localhost:1883`),
   - topic prefix from `${MQTT_TOPIC_PREFIX:-historian}/<name>`,
   - its own variables prefixed `<NAME>_`, credentials through `.env` only,
   - `logging: *logging`,
   - the two SQL files mounted into the `timescaledb` service, `devices.sql`
     before `metrics.de.sql`.
3. Add its variables, with an explanation, to [.env.example](../.env.example).
4. Test it in the stack: the values have to arrive in `measurements` with
   the right device and a plausible time.

## Further requirements

- A healthcheck that tests delivery. A running process is not sufficient: a
  module can run for hours without delivering values. Check that a complete
  pass succeeded recently and that the broker is connected. MQTT clients
  often buffer without an error while the broker is away.
- Recovery without intervention. The broker or the source may not be
  available at startup; the module retries and does not exit.
- Few writes. The stack is meant to run on a Raspberry Pi, where every write
  wears the SD card; for the same reason the broker saves its queue only
  every 30 minutes and the mbus and senec modules keep their heartbeat file
  on a tmpfs. So no verbose logging in normal operation. State files belong
  on a volume: a file elsewhere in the container is lost when the container
  is recreated.
- Read access only. A module does not act on devices.
