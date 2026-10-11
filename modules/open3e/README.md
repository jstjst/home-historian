# open3e module

Reads data points (DIDs) from Viessmann devices of the E3 platform over the
CAN bus and publishes them to MQTT: heat pumps (Vitocal), battery storage
(Vitocharge), gas boilers (Vitodens). The module is a configuration of
[open3e](https://github.com/open3e/open3e) and contains no code of its own.

| | |
|---|---|
| Profile | `open3e` (simulator: `open3e-sim`) |
| Topics | `<prefix>/open3e/<device>/<DID>_<name>[/<sub-value>…]` |
| Devices (`device_key`) | `open3e/<device>`, one per control unit, keys from [config/devices.json](config/devices.json) |
| Timestamp | from the broker (time of receipt) |
| MQTT QoS | 0 (not configurable in open3e) |
| Status | in use on a Vitocal 250-A with five control units |

## Prerequisites

- A Linux host with a SocketCAN interface on the device's CAN bus.
  [Commissioning](#commissioning) describes the Waveshare RS485 CAN HAT (B) on
  a Raspberry Pi.
- For the [simulator](#simulator) instead of hardware: the `vcan` kernel
  module.

The service runs on the host network, because the CAN interface is a kernel
interface of the host.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPEN3E_CAN_INTERFACE` | `vcan0` | CAN interface of the host; `can0` for a real adapter |
| `OPEN3E_DIDS` | `vitocal.268,vitocal.396,vitocharge.1603` | data points to read, as `<device>.<DID>` or `<device>.[<DID>,<DID>,…]`; `all` for [every data point of a scanned system](#reading-every-data-point) |
| `OPEN3E_INTERVAL_SECONDS` | `60` | pause between two passes |

[config/devices.json](config/devices.json) lists the control units (key, CAN
address, data point list). The shipped file matches the simulator; a
[scan](#5-scan-the-system) writes the one for a real system.

open3e reads its data points from first to last, waits
`OPEN3E_INTERVAL_SECONDS` and starts again. A pass therefore begins every
interval plus the time the pass took. Reading some data points more often
than others would need open3e's command channel, which the module does not
start.

### Reading every data point

```
OPEN3E_DIDS=all
```

reads every data point of every control unit in `devices.json`, with open3e's
option `-a`. This needs the files of a [scan](#5-scan-the-system): "every
data point" is what the unit's `Open3Edatapoints_<address>.py` lists. Without
such a list open3e asks every unit for all 1564 data points it knows and
publishes an error topic for each one the unit does not have (3102 against
the two units of the simulator). The module does not start in that case.

`-a` reads once and exits. For `all`, open3e is therefore started again for
every pass, with `OPEN3E_INTERVAL_SECONDS` in between; a list is read by one
process that keeps running. Against the simulator this gives 143 values from
26 data points per pass and one pass every 62 s at an interval of 60 s.

Consequences:

- Every pass stores every value again, changed or not, including schedules,
  serial numbers and error histories. open3e's list for a Vitocal has 539
  data points with close to 2000 values.
- A pass takes longer, and the interval is added to its duration.
- A new scan changes what is read without a change in `.env`.

To leave data points out, give a list. This prints the numbers of one unit,
to be put in brackets after the device's key
(`OPEN3E_DIDS=vitocal.[256,262,…],vitocharge.[…]`):

```bash
grep -oE '^ *[0-9]+ *:' modules/open3e/config/Open3Edatapoints_680.py \
    | tr -d ' :' | paste -sd, -
```

## Devices and metrics

[devices.sql](devices.sql) adds the device types `vitocal250a` and
`vitocharge` and the two devices of the shipped `devices.json`,
`open3e/vitocal` and `open3e/vitocharge`.

Every key in `devices.json` needs a row in `devices`. For a scanned system,
whose keys are the CAN addresses:

```sql
INSERT INTO devices (type, device_key, name) VALUES
  ('vitocal250a', 'open3e/0x680', 'Vitocal 250-A HPMU'),
  ('vitocal250a', 'open3e/0x68c', 'Vitocal 250-A VCMU');
```

The control units of one device share its type. A data point that several
units deliver is then one metric, and its values are told apart by device.
The ingester reads the list of devices at startup: after adding one,
`docker compose restart ingest`.

[metrics.de.sql](metrics.de.sql) defines the 2729 values a Vitocal 250-A
delivered when every data point of its five control units was read, and two
of a Vitocharge. `value_type` is checked against stored values. Names, units
and categories are derived from open3e's English data point names and are
not checked; where the name states no unit, the unit is a guess. Display
names are German. [metrics.en.sql](metrics.en.sql) has English names for the
data points of the default selection (268, 396, 1603). Other devices deliver
other data points, which the ingester stores as stubs
([docs/architecture.md](../../docs/architecture.md)).

Both files are loaded when the database starts for the first time. On an
existing database:

```bash
for f in devices.sql metrics.de.sql; do
  docker compose exec -T timescaledb sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/open3e/$f
done
docker compose restart ingest
```

## Values

The key of a value is the data point's number and name as open3e knows them,
followed by the path of the sub-value: open3e publishes a complex data point
as one topic per leaf. Data point 268 arrives as
`268_FlowTemperatureSensor.Actual`, `.Minimum`, `.Maximum`, `.Average`,
`.SensorStatus.ID` and `.SensorStatus.Text`.

When a device does not answer, open3e publishes the error message under
`…/<DID>_ERR/…`. It is stored as a text metric.

## Behaviour

- Because of QoS 0, up to about two seconds of values can be lost when the
  ingester stops ([docs/architecture.md](../../docs/architecture.md)).
- The command channel is off. open3e can accept read and write commands on
  an MQTT topic (`-l <topic>`). The module starts without it, because the
  stack only reads and the broker has no authentication by default.
- The debug log is off. The UDS library would otherwise write to a file
  `udsoncan.log` without a size limit; the image redirects it to `/dev/null`.
- open3e is pinned to a commit in the [Dockerfile](Dockerfile), because topic
  format and behaviour depend on the version.
- The service has no healthcheck.

## Commissioning

Steps 1 to 3 describe the Waveshare RS485 CAN HAT (B) on a Raspberry Pi.
With another SocketCAN adapter, start at step 2.

### 1. The HAT

The HAT carries an MCP2515 CAN controller on SPI0 (chip select 0, interrupt
on GPIO 25). [Waveshare's wiki](https://www.waveshare.com/wiki/RS485_CAN_HAT_(B))
gives these lines for `config.txt` (`/boot/firmware/config.txt` on current
systems):

```
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25,spimaxfrequency=1000000
```

After a reboot, `ip -brief link show can0` lists the interface, still `DOWN`.

`oscillator` has to match the crystal on the board, because the kernel
derives the bit timing from it. A wrong value causes no error: the bus runs
at another bit rate and no device answers. Waveshare's other HATs differ; the
RS485 CAN HAT without the "(B)" carries 12 MHz, its boards from before August
2019 8 MHz.

The two RS485 channels of the same HAT are independent of this; the mbus
module uses them ([modules/mbus](../mbus/README.md#3-the-serial-port)).

### 2. Wiring

- CAN-H to H and CAN-L to L, as a twisted pair. On a Vitocal 250-A the bus
  is on plug 91; take the terminals from the installation and service
  instructions of the device. open3e's wiki has
  [photos and notes](https://github.com/open3e/open3e/wiki/020-Inbetriebnahme-CAN-Adapter-am-Raspberry)
  for several devices.
- Ground: that wiki recommends a third wire to the device's CAN ground for an
  isolated adapter, so that the signals have a reference. Waveshare lists
  power and signal isolation for the HAT (B) without saying for which of its
  interfaces. The wiki also notes that in the Vitocal 250-A instructions of
  11/2024 the ground terminal of plug 91 is no longer assigned. Check both
  against the hardware.
- Termination: one 120 Ω resistor at each of the two ends of the bus and none
  in between. The HAT has one, switched by a jumper. Whether it has to be set
  depends on where the HAT sits and on what the bus already has. With every
  device powered off, measure between H and L: about 60 Ω is correct (two
  resistors in parallel), 120 Ω means one is missing, 40 Ω one too many.
- One open3e instance per bus. open3e's README warns that a second one leads
  to conflicts on the bus.

### 3. Bring the interface up

open3e does not set the bit rate. It uses the interface as the host has
configured it and fails if it is down. The E3 bus runs at 250 kbit/s:

```bash
modules/open3e/setup-can.sh             # can0; another name as argument
```

That lasts until the next reboot. To make it permanent, hand the interface to
systemd-networkd, which only touches interfaces that have a `.network` file:

```bash
sudo tee /etc/systemd/network/80-can0.network >/dev/null <<'EOF'
[Match]
Name=can0

[Link]
RequiredForOnline=no

[CAN]
BitRate=250K
RestartSec=10s
EOF
sudo systemctl enable --now systemd-networkd
```

- `RequiredForOnline=no`: a CAN link never counts as online. Without the
  line, whatever waits for the network at boot (Docker among them) can wait
  until networkd's timeout of 120 s.
- `RestartSec` (`restart-ms` in the script): after a bus-off the controller
  restarts after 10 s. Without it the MCP2515 driver puts the chip to sleep,
  and the interface stays down until it is taken down and up by hand.

Check with `ip -details -statistics link show can0`: `state ERROR-ACTIVE`
and `bitrate 250000`.

### 4. Read one data point

Ask the heat pump (address `0x680`, open3e's default) for a single data
point:

```bash
docker compose stop open3e
docker compose --profile open3e run --rm --no-deps open3e \
    open3e -c can0 -r 268 -v
```

```
Configuration file devices.json not found, continuing with generic DID list, ECU 0x680
0x680 268 FlowTemperatureSensor {"Actual": 39.6, "Minimum": 19.3, "Maximum": 63.4, "Average": 38.2, "SensorStatus": {"ID": 0, "Text": "no_error"}}
closing 0x680 - bye!
```

The first line is expected: without `-cnfg` the command reads from `0x680`
with open3e's general data point list.

| Observation | Likely cause |
|---|---|
| `OSError: [Errno 19] No such device` | no `can0`: overlay or `dtparam=spi=on` missing, HAT not seated; look at `dmesg` |
| `Failed to transmit: Network is down [Error Code 100]` | the interface exists but is not up (step 3) |
| `ERR/0x680.268 "Did not receive response in time. …"` after 3 s | the request was sent and not answered: H and L swapped, wrong bit rate or `oscillator`, termination, wrong plug |
| `state ERROR-PASSIVE` or `BUS-OFF` in `ip -details -statistics link show can0`, TX errors counting up | the HAT's frames are not acknowledged: same causes |

### 5. Scan the system

`open3e_depictSystem` asks every address for every data point and writes
what it finds: a `devices.json` and, per control unit, a list of the data
points that unit has (`Open3Edatapoints_<address>.py`). Run it in the
module's image, into [config/](config/), with the module stopped:

```bash
docker compose stop open3e
docker compose --profile open3e run --rm --no-deps --user "$(id -u):$(id -g)" \
    -w /scan -v "$PWD/modules/open3e/config:/scan" \
    open3e open3e_depictSystem -c can0
```

The directory is mounted at `/scan` because the service already mounts it at
`/config`, read-only, and Compose does not replace that mount.

open3e's README puts the scan at 10 to 20 minutes and asks for a new one
after every firmware update of a device. With `-s` it also writes the
`virtdata_<address>.txt` files the simulator plays back.

The `devices.json` it writes needs one change and allows a second:

```json
{
  "vitocal": {
    "tx": "0x680",
    "dpList": "/config/Open3Edatapoints_680.py",
    "prop": "HPMUMASTER"
  }
}
```

- `dpList` has to be the path inside the container. open3e resolves it from
  its working directory, not from the location of `devices.json`.
- The key may be replaced by a name. It is the topic segment, and with it the
  `device_key` `open3e/<key>` and what a list in `OPEN3E_DIDS` refers to. The
  scan writes the address (`"0x680"`), which works as well and stays the
  same after the next scan; the readable name is the device's `name` in the
  database in both cases.

A control unit that should not be read (a gateway, for example) is removed
from `devices.json`. If it stays in without a row in `devices`, it is read in
every pass and its values are dropped.

### 6. Switch the module on

1. In `.env`: `OPEN3E_CAN_INTERFACE=can0`, the data points to read in
   `OPEN3E_DIDS` (or [`all`](#reading-every-data-point)), and the
   `open3e-sim` profile removed. Run `docker compose rm -sf open3e-sim`
   before removing the profile, or the simulator keeps running.
2. Insert a row in `devices` for every key in `devices.json`
   ([Devices and metrics](#devices-and-metrics)) and
   `docker compose restart ingest`.
3. `docker compose up -d open3e`, then `docker compose logs -f open3e`.

## Simulator

[sim/](sim/) holds the configuration for
[virtualE3](https://github.com/lgraf/vituale3). The simulator answers
as two devices (Vitocal `0x680`, Vitocharge `0x6a1`) on the virtual CAN bus
`vcan0`, with fixed values.

```bash
modules/open3e/sim/setup-vcan.sh        # create vcan0 (after every reboot)
docker compose --profile open3e --profile open3e-sim up -d
```

`vcan` exists on Linux only. The script explains what to do about a missing
kernel module and how to make the interface permanent.

Another system can be simulated from a capture of the real one. A
[scan](#5-scan-the-system) with `-s` writes the files the simulator needs:
`devices.json`, `Open3Edatapoints_<address>.py` and `virtdata_<address>.txt`
per control unit. Put them into [sim/](sim/) and adjust the mounts of the
`open3e-sim` service in [compose.yml](../../compose.yml), which names each
file. A capture holds identifiers of the devices it was taken from: the
shipped simulation data has placeholders in data points 256 and 604
(`1234567801234567`, `VIESSMANN-0815`). Check a capture before publishing it.

Data points with simulation data:

- Vitocal (`0x680`): 256, 262, 268, 381, 382, 396, 505, 507, 548, 592, 604, 691, 692, 875, 1006, 1007, 1097
- Vitocharge (`0x6a1`): 256, 262, 382, 505, 507, 592, 604, 1603, 2214

The simulation data comes from open3e's integration test (Apache-2.0).
