# vicare module

Fetches the values of a Viessmann device through the ViCare cloud API, using
[PyViCare](https://github.com/openviess/PyViCare), and publishes them to
MQTT. It serves installations without access to the CAN bus, or runs as a
second source next to [open3e](../open3e/).

| | |
|---|---|
| Profile | `vicare` |
| Topics | `<prefix>/vicare/<gatewayId>/<deviceId>/<feature>/<property>[/<subkey>]` |
| Devices (`device_key`) | `vicare/<gatewayId>/<deviceId>`, one device |
| Timestamp | from the module: the time the API reports for the value |
| MQTT QoS | 1 |
| Status | in use with a Vitocal 250-A |

## Prerequisites

- A ViCare account that the installation is linked to in the app.
- A client ID from the [Viessmann developer portal](https://app.developer.viessmann.com/).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `VICARE_CLIENT_ID` | – | client ID from the developer portal |
| `VICARE_EMAIL`, `VICARE_PASSWORD` | – | credentials of the ViCare app |
| `VICARE_DEVICE_TYPE` | `heating` | which device: `heating` = heat pump, `electricityStorage` = battery storage |
| `VICARE_POLL_INTERVAL_SECONDS` | `300` | polling interval, minimum 60 |

The API quota is limited: 300 s amounts to 288 requests a day. Only go lower
if your quota allows it.

## Devices and metrics

[devices.sql](devices.sql) adds the device type `vitocal250a` and no device,
because gateway and device id belong to the installation. On its first pass
the module logs the key of the row that is needed:

```
Topics under historian/vicare/<gatewayId>/<deviceId>/ -- device_key for the devices table: that path without the stack prefix (e.g. 'vicare/<gatewayId>/<deviceId>')
```

```sql
INSERT INTO devices (type, device_key, name)
VALUES ('vitocal250a', 'vicare/<gatewayId>/<deviceId>', 'Heat pump (ViCare)');
```

The ingester reads the list of devices at startup: after adding one,
`docker compose restart ingest`.

[metrics.de.sql](metrics.de.sql) defines the features the API delivers for a
Vitocal 250-A with three heating circuits, a buffer cylinder and a DHW
cylinder, with German display names. There are no English names. Other
features are stored as stubs
([docs/architecture.md](../../docs/architecture.md)).

Both files are loaded when the database starts for the first time. On an
existing database:

```bash
for f in devices.sql metrics.de.sql; do
  docker compose exec -T timescaledb sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/vicare/$f
done
```

## Values

The key of a value is the API's feature name followed by the property:
`heating.circuits.1.sensors.temperature.supply.value`,
`heating.circuits.1.heating.curve.slope`. The payload depends on the
property's type in the API:

| API type | Published as |
|---|---|
| `number`, `boolean`, `string` | the value |
| `object` | one topic per entry, with the entry's name as subkey |
| `array` with a unit (consumption per period) | the first element only, which is the current period |
| `array` of scalars | the elements as text, separated by commas |
| `array` of objects, `Schedule` | the value as a JSON string |

Features without properties are not published.

## Behaviour

- The module publishes one device, chosen by `VICARE_DEVICE_TYPE`. The
  cloud's device id can change; selecting by type keeps the assignment
  stable.
- Only new values are sent. For each feature the module remembers the API
  timestamp it sent last. The comparison is per feature and not against one
  shared timestamp, because the cloud updates features independently of each
  other and a shared timestamp would discard values that arrive late.
- A restart sends every feature once more. The remembered timestamps are
  kept in memory only, so that the first pass after a restart also refills a
  database that had to be rebuilt. This produces duplicates: the ingester
  only checks messages the broker redelivers, so every feature that has not
  changed since the last pass before the restart is stored a second time,
  with the same time and value.
- Login and token are handled by PyViCare; the session is stored on the
  volume `vicare-token`. The module deletes an unreadable token file (left by
  an interrupted write, for example) before logging in, because PyViCare
  would otherwise fail on it at every start.
- A network outage produces one warning line per pass; the next pass tries
  again. A pass that hangs for more than five minutes ends the process, and
  the restart policy starts it again. PyViCare sends the login and the token
  renewal without a timeout, so a peer that does not answer would otherwise
  block the module permanently.
- Healthcheck ([healthcheck.py](healthcheck.py)): the service counts as
  healthy only if a complete pass succeeded recently and the broker was
  connected.

## Commissioning

1. Set `VICARE_CLIENT_ID`, `VICARE_EMAIL` and `VICARE_PASSWORD` and add
   `vicare` to `COMPOSE_PROFILES`.
2. On a database that already exists, apply `devices.sql` and
   `metrics.de.sql` ([Devices and metrics](#devices-and-metrics)).
3. `docker compose up -d vicare`, then `docker compose logs vicare`: the
   first pass logs the `device_key`.
4. Insert the device row, then `docker compose restart ingest vicare`. The
   values of the passes before that were dropped, and the module only sends
   a feature again when it changes; after its restart it sends every feature
   once.
