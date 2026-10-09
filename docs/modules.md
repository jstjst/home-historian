# Modules

A module connects **one data source**: it reads a device or an interface and
publishes the values to MQTT. That is all it has to do. Timestamps, buffering,
storage and analysis are the core's job.

A module can be written in any language, or consist of nothing but the
configuration of an existing tool (like [open3e](../modules/open3e/)).

## The contract

### Topic

```
<prefix>/<module>/<device>/<quantity>[/<sub-value>…]
```

| Part | Meaning |
|---|---|
| `<prefix>` | the stack's `MQTT_TOPIC_PREFIX` (default `historian`) |
| `<module>/<device>` | becomes the `device_key` in the `devices` table; may have more than two segments |
| the rest | becomes the metric's `native_key`, with `/` turned into `.` |

Example: `historian/open3e/vitocal/268_FlowTemperatureSensor/SensorStatus/ID`
belongs to the device `open3e/vitocal` and the metric
`268_FlowTemperatureSensor.SensorStatus.ID`.

- **One device = one physical unit.** If a module reads several devices, each
  gets a segment of its own.
- **No device name inside the quantity.** The `native_key` should read the
  same for every device of a type, so that they share their metric definitions.
- **Stable names.** To the database, a renamed topic is a new metric.

### Payload

A **single scalar value as text**: `21.5`, `true`, `no_error`. The module
breaks compound values down into separate topics. No JSON object with several
fields; the core could not turn that into time series.

Which column a value goes to is decided by the metric's `value_type`; an
unknown key is stored as text at first.

### Timestamp

Usually nothing to do: the broker stamps every message on receipt (to the
second).

If the module knows the time of measurement better, for example because it
fetches values with a delay, it sends it as the MQTT 5 user property
`timestamp` in ISO 8601 with a time zone (`2026-01-31T12:00:00.000Z`). That
value takes precedence.

Nothing else belongs in the user properties. Unit, type and name are master
data and live in `metrics`; sending them along would create a second,
competing source of truth.

### Delivery

- **QoS 1**, so the broker reliably holds the values while the ingester is
  down. (QoS 0 is buffered as well but has a small loss window, see
  [architecture.md](architecture.md).)
- **Not retained.** An old value delivered again on subscribing would be a
  duplicate in the time series.
- **Only send what changed or was newly measured.** Repeating the same value
  with the same time of measurement produces duplicate rows: the ingester
  only checks messages the broker redelivers, not what a module sends twice.
  The vicare module remembers the last timestamp it sent per quantity for this
  reason. It keeps that in memory, so its restart repeats unchanged values
  once (see its [README](../modules/vicare/README.md)).

### Master data

The ingester drops messages from devices it does not know. Every module
therefore ships a `seed.sql` containing:

- its **devices** (`devices`: type, `device_key`, name),
- optionally **curated metrics** (`metrics`) for the quantities it knows.
  Everything else the ingester creates as stubs.

The file must be safe to run more than once (`ON CONFLICT … DO NOTHING`, or
overwrite stubs only); see [modules/open3e/seed.sql](../modules/open3e/seed.sql)
for a template. If the keys depend on the installation (serial numbers), the
module's README describes which row to insert instead.

A new **device type** is a new value in the `device_type` enum:

```sql
ALTER TYPE device_type ADD VALUE 'my_type';
```

## Adding a module

1. A directory `modules/<name>/` with a `Dockerfile`, the program, `seed.sql`
   and a short `README.md` (source, prerequisites, variables, status).
2. A service in [compose.yml](../compose.yml), in the "Modules" section:
   - its own profile, `profiles: ["<name>"]`,
   - broker at `mosquitto:1883` (on the host network: `localhost:1883`),
   - topic prefix from `${MQTT_TOPIC_PREFIX:-historian}/<name>`,
   - its own variables prefixed `<NAME>_`, credentials through `.env` only,
   - `logging: *logging`.
3. Add its variables, with an explanation, to [.env.example](../.env.example).
4. Test it in the stack: do the values arrive in `measurements`, with the
   right device and a plausible time?

## What a good module also does

- **An honest healthcheck.** "The process is running" is not enough: a module
  can run and deliver nothing for hours. Check that a complete pass succeeded
  recently *and* that the broker is connected; MQTT clients often buffer
  silently when the broker is gone instead of reporting an error.
- **Recover without help.** The broker or the source may not be there yet at
  startup; retry instead of dying.
- **Write little.** No verbose logging in normal operation, no state files
  outside a volume.
- **Read only.** A historian does not act on devices.
