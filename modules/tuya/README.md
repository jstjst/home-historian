# tuya module

Listens to the values Tuya's Smart Life cloud pushes for the devices of a
Smart Life (or Tuya Smart) account and publishes them to MQTT. Written for
battery-powered WiFi temperature and humidity sensors (Tuya category
`wsdcg`), which only wake up to report to the cloud and cannot be read in the
local network. Other Tuya devices are published the same way, without metric
definitions.

| | |
|---|---|
| Profile | `tuya` |
| Topics | `<prefix>/tuya/<device id>/<code>` |
| Devices (`device_key`) | `tuya/<device id>`, Tuya's id of the device |
| Timestamp | from Tuya: the time of measurement that comes with each pushed value, to the millisecond |
| MQTT QoS | 1 |
| Status | logged in and connected with a real account; pushes of a WiFi T&H sensor (product `xflodz7oja0pndk3`) received through the SDK; the path to the database tested with simulated pushes |

## Prerequisites

- A Smart Life or Tuya Smart account with the device in it, and the app on a
  phone for the login.
- A client id and schema for the login (`TUYA_CLIENT_ID`, `TUYA_SCHEMA`).
  Read the next section before setting them.

### Which app the login presents itself as

The module uses [tuya-device-sharing-sdk](https://github.com/tuya/tuya-device-sharing-sdk),
Tuya's SDK for the Home Assistant integration. It needs no developer account:
the login is a QR code scanned with the app. But the SDK has no app of its
own. Every request names a client id that Tuya issued for one app, and the
login goes to endpoints made for Home Assistant
(`/v1.0/m/life/home-assistant/qrcode/tokens`, `/v1.0/m/life/ha/…`), as the
SDK's source (version 0.2.15) shows.

The only client id and schema known to work are Home Assistant's, in
[homeassistant/components/tuya/const.py](https://github.com/home-assistant/core/blob/dev/homeassistant/components/tuya/const.py)
(`TUYA_CLIENT_ID`, `TUYA_SCHEMA`). With them, the module logs in to Tuya as
Home Assistant. No statement by Tuya was found on whether other programs may
do that; Tuya can block it or change the endpoints at any time. This
repository therefore contains neither value: whoever runs the module decides
and puts them into `.env`.

The alternative without borrowed credentials is a cloud project of one's own
on the [Tuya Developer Platform](https://iot.tuya.com) with its own
access id and secret. That is a different API, and this module does not
support it. Its free tier has to be renewed every few months.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TUYA_CLIENT_ID` | – | client id of the app the login presents itself as, see above |
| `TUYA_SCHEMA` | – | schema that belongs to that client id |
| `TUYA_DEVICES` | all | device ids to publish, comma separated; empty or `all` = every device of the account |

The login itself is not configured in `.env`. `tuya_login.py` stores it,
with its tokens, in `/data/token.json` on the volume `tuya-data`, and the
module renews the tokens there. The file grants access to the account:
whoever can read the volume can read the devices.

## Devices and metrics

[devices.sql](devices.sql) adds the device type `tuya` and no devices: the
ids belong to the installation. Insert one row per published device, with
the name and location it should have in the database, and
`docker compose restart ingest`, because the ingester reads the list of
devices at startup. `tuya_login.py --list` prints the rows, with the name
from the app:

```sql
INSERT INTO devices (type, device_key, name, location) VALUES
  ('tuya', 'tuya/<device id>', 'Temperature and humidity sensor', 'bathroom');
```

The name can be changed in the table at any time; the device id in the
topic stays the same.

`category` stays NULL; a room belongs to no part of the installation in the
sense of the categories.

[metrics.de.sql](metrics.de.sql) defines the codes a WiFi temperature and
humidity sensor (category `wsdcg`, product "T&H Sensor") delivered, and
`online`, with German display names. Other sensors of the category have
codes of their own (`battery_state`, `temp_periodic_report`, … in the device
dumps of the Home Assistant integration's tests); they are stored as stubs
([docs/architecture.md](../../docs/architecture.md)).

Both files are loaded when the database starts for the first time. On an
existing database:

```bash
for f in devices.sql metrics.de.sql; do
  docker compose exec -T timescaledb sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/tuya/$f
done
docker compose restart ingest
```

## Values

The key of a value is Tuya's code for the data point. The tested sensor
delivers these (examples are its values):

| Code | Quantity | Unit | Example |
|---|---|---|---|
| `va_temperature` | temperature | °C | `21.9` |
| `va_humidity` | relative humidity | % | `54` |
| `battery_percentage` | battery | % | `92` |
| `temp_alarm`, `hum_alarm` | alarm: `loweralarm`, `upperalarm`, `cancel` | – | `cancel` |
| `maxtemp_set`, `minitemp_set` | alarm limits for the temperature | °C | `39.0` |
| `maxhum_set`, `minihum_set` | alarm limits for the humidity | % | `60` |
| `temp_sensitivity`, `hum_sensitivity` | change that makes the sensor report | K, % | `0.6` |
| `temp_unit_convert` | unit on the sensor's display: `c`, `f` | – | `c` |
| `online` | online/offline in Tuya's cloud (from the module, see below) | – | `true` |

Tuya sends integers together with a scale in the device's specification, and
the scale differs between products: `va_humidity` 618 with scale 1 on one
sensor in the dumps, scale 0 on the others; `va_temperature` with
scale 0 on one, scale 1 on the rest. A single metric definition per code
could not hold that, so the module applies the scale and publishes `61.8`.
It changes nothing else.

How often values arrive is up to the sensor, not the module. The tested
sensor reports after a change of 0.6 K or 6 % (`temp_sensitivity`,
`hum_sensitivity`, settable in the app). Some sensors also report at a fixed
interval (`temp_periodic_report`, 60 minutes on one in the dumps); the tested
one has no such code.

## Behaviour

- Only values Tuya pushes are published, each with the time of measurement
  that comes with it. The status the cloud hands out when the module starts
  carries no time and is not published, so the first values of a sensor
  arrive with its next report.
- Values pushed while the module is not running or not connected to Tuya
  are lost: Tuya's MQTT has no persistent session, and the SDK offers no
  history. The local broker buffers as usual once a value has been received.
- A pushed value without a time (devices for which the SDK does not convert
  data point ids) is published without a `timestamp`; the broker stamps it.
- Tuya's online/offline events become the value `online`, stamped by the
  broker. A renamed device produces the same event and repeats the value.
- Every device of the account is published, unless `TUYA_DEVICES` lists
  some; the others are ignored and logged at startup. Values of a device
  without a row in `devices` are dropped by the ingester, so publishing all
  of them stores only those with a row.
- A device added to the account later is published once the service has
  been restarted (it loads the device list at startup).
- The SDK subscribes only to devices whose `set_up` flag is true. The tested
  sensor has it false, and no push arrived for it until the module set the
  flag itself; it does so for every device it publishes. Tuya does not
  document the flag.
- A WiFi sensor goes online when it wakes up to report: a report comes as
  `online` = `true` followed by the values.
- The SDK renews the connection to Tuya every two hours and reconnects on its
  own. The tokens it renews are written to the token file, which is the only
  write the module makes apart from the heartbeat on its tmpfs.
- The module only reads. It sends no commands; the SDK's command function is
  not called.
- Healthcheck ([healthcheck.py](healthcheck.py)): healthy only while a
  device to publish exists in the account and both the broker and Tuya's
  cloud are connected. The age of the last value is not checked: a sensor
  whose values do not change may not report for hours.

## Commissioning

1. Set `TUYA_CLIENT_ID` and `TUYA_SCHEMA` ([Prerequisites](#prerequisites)).
2. Find the user code in the app: Me, the gear icon at the top right,
   Account and Security, User Code (at the bottom).
3. Log in, in a terminal:

   ```bash
   docker compose run --rm tuya python tuya_login.py
   ```

   It asks for the user code and prints a QR code. Scan it with the app
   (scan icon on the home tab), confirm the login there, then press Enter.
   The tool lists every device of the account with its `device_key` and its
   current values as the module would publish them, and prints the rows for
   the `devices` table. `--list` lists again without logging in.
4. Insert the device rows, with names of your choice
   ([Devices and metrics](#devices-and-metrics)), and add `tuya` to
   `COMPOSE_PROFILES`. `TUYA_DEVICES` only if not every device should be
   published.
5. `docker compose up -d tuya`. The log names every device it publishes and
   then says `Connected to Tuya's cloud, waiting for values`.

| Observation | Likely cause |
|---|---|
| `No QR code: User Code Incorrect (code USERCODE_INCORRECT)` | wrong user code, or a client id Tuya does not know |
| the app does not recognise the QR code | the terminal shows it light on dark; on a light terminal background it comes out inverted. Switch the terminal to a dark background |
| `No login in /data/token.json` | step 3 not done yet; the service picks the login up within 30 s |
| `Loading the devices failed (…)`, repeated | no internet connection, or the login has expired: log in again and `docker compose restart tuya` |
| `Device id … from TUYA_DEVICES is not in the account` | a typo in `TUYA_DEVICES`, or the device was removed from the app |
| connected, no values for an hour | normal while temperature and humidity do not change by more than `temp_sensitivity` / `hum_sensitivity` |
| values published, none in the database | the device row is missing, or the ingester was not restarted after adding it |

## Sources

- The Home Assistant integration,
  [homeassistant/components/tuya](https://github.com/home-assistant/core/tree/dev/homeassistant/components/tuya):
  login flow (`config_flow.py`), use of the SDK (`coordinator.py`), the codes
  of category `wsdcg` (`sensor.py`) and the device dumps in its tests
  (`tests/components/tuya/fixtures/wsdcg_*.json`).
- The SDK, [tuya-device-sharing-sdk](https://github.com/tuya/tuya-device-sharing-sdk)
  0.2.15 (MIT): format of the pushes (`manager.py`), MQTT connection and its
  renewal (`mq.py`). It imports `cryptography` without declaring it; the
  module's requirements add it.
- [tinytuya](https://github.com/jasonacox/tinytuya), README: battery-powered
  Tuya devices sleep and cannot be read in the local network.
