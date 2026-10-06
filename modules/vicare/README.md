# vicare module

Fetches the values of a Viessmann device through the ViCare cloud API (using
[PyViCare](https://github.com/openviess/PyViCare)) and publishes them to MQTT.
Meant for installations without access to the CAN bus, or as a second source
next to [open3e](../open3e/).

| | |
|---|---|
| Profile | `vicare` |
| Topics | `<prefix>/vicare/<gatewayId>/<deviceId>/<feature>/<property>[/<subkey>]` |
| Device (`device_key`) | `vicare/<gatewayId>/<deviceId>` |
| Timestamp | from the module: the time the API reports for the value |
| MQTT QoS | 1 |
| Status | flattening of the API response and token handling tested offline |

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

The **API quota is tight**: 300 s amounts to 288 requests a day. Only go lower
if your own quota allows it.

## Registering the device

Gateway and device id depend on the installation, so there is no `seed.sql`.
On its first pass the module logs which row is needed:

```
Topics under historian/vicare/<gatewayId>/<deviceId>/ -- device_key for the devices table: that path without the stack prefix (e.g. 'vicare/<gatewayId>/<deviceId>')
```

```sql
INSERT INTO devices (type, device_key, name)
VALUES ('vitocal', 'vicare/<gatewayId>/<deviceId>', 'Heat pump (ViCare)');
```

The ingester reads the list of devices at startup, so follow up with
`docker compose restart ingest`.

## Behaviour

- **Exactly one device**, chosen by device type. The cloud's device id can
  change; going by type keeps the assignment stable.
- **Only new values are sent.** Per feature, the module remembers the API
  timestamp it sent last. The comparison is per feature rather than against a
  shared watermark: the cloud updates features independently of each other,
  and a watermark would discard values that arrive late.
- **Login and token** are handled by PyViCare; the session is stored on the
  volume `vicare-token`. The module deletes an unreadable token file (left
  behind by an interrupted write, say) before logging in, because PyViCare
  would otherwise fail on it for good.
- **Healthcheck** ([healthcheck.py](healthcheck.py)): the service only counts
  as healthy if a complete pass succeeded recently and the broker was connected.
