# open3e module

Reads data points (DIDs) from Viessmann devices of the E3 platform, such as
heat pumps (Vitocal), battery storage (Vitocharge) and gas boilers (Vitodens),
directly over the CAN bus and publishes them to MQTT. The module is a
configuration of [open3e](https://github.com/open3e/open3e); it contains no
code of its own.

| | |
|---|---|
| Profile | `open3e` (simulator: `open3e-sim`) |
| Topics | `<prefix>/open3e/<device>/<DID>_<name>[/<sub-value>…]` |
| Devices (`device_key`) | `open3e/<device>`, names from [config/devices.json](config/devices.json) |
| Timestamp | from the broker (time of receipt) |
| MQTT QoS | 0 (not configurable in open3e) |
| Status | tested against the simulator, not on real hardware |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPEN3E_CAN_INTERFACE` | `vcan0` | CAN interface of the host; `can0` for a real adapter |
| `OPEN3E_DIDS` | `vitocal.268,vitocal.396,vitocharge.1603` | data points to read, as `<device>.<DID>` or `<device>.[<DID>,<DID>,…]`; `all` for [every data point of a scanned system](#reading-every-data-point) |
| `OPEN3E_INTERVAL_SECONDS` | `60` | pause between two passes |

Which control units exist is defined in
[config/devices.json](config/devices.json) (name, CAN address). Every name
there needs a row in `devices`; see [seed.de.sql](seed.de.sql), which also brings the
metrics of a Vitocal 250-A (device type `vitocal250a`).

The service runs on the host network, because the CAN interface is a kernel
interface of the host.

**One pass, one interval.** open3e reads its data points from first to
last, waits `OPEN3E_INTERVAL_SECONDS` and starts again, so a pass begins
every interval plus the time the pass took. Reading some data points more
often than others would need open3e's command channel, which stays off.

## Simulator

[sim/](sim/) holds the configuration for
[virtualE3](https://github.com/philippoo66/vitualE3). The simulator plays two
devices (Vitocal `0x680`, Vitocharge `0x6a1`) on the virtual CAN bus `vcan0`
and answers with fixed values.

```bash
modules/open3e/sim/setup-vcan.sh        # create vcan0 (after every reboot)
docker compose --profile open3e --profile open3e-sim up -d
```

`vcan` exists on Linux only. The script explains what to do about a missing
kernel module and how to make the interface permanent.

Data points with simulation data:

- Vitocal (`0x680`): 256, 262, 268, 381, 382, 396, 505, 507, 548, 592, 604, 691, 692, 875, 1006, 1007, 1097
- Vitocharge (`0x6a1`): 256, 262, 382, 505, 507, 592, 604, 1603, 2214

The simulation data comes from open3e's integration test (Apache-2.0).

## Commissioning

Written from the documents named below and tested against the simulator; not
tried on the hardware yet. The adapter described is the Waveshare RS485 CAN
HAT (B) on a Raspberry Pi. With any other SocketCAN adapter, start at step 2.

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

**`oscillator` has to match the crystal on the board.** The kernel derives
the bit timing from it. With a wrong value nothing fails: the bus runs at
another bit rate and no device answers. Waveshare's other HATs differ; the
RS485 CAN HAT without the "(B)" carries 12 MHz, its boards from before August
2019 8 MHz.

The two RS485 channels of the same HAT are independent of this; the mbus
module uses them ([modules/mbus](../mbus/README.md#3-the-serial-port)).

### 2. Wiring

- **CAN-H to H, CAN-L to L**, as a twisted pair. On a Vitocal 250-A the bus
  is on plug 91; take the terminals from the installation and service
  instructions of your device. open3e's wiki has
  [photos and notes](https://github.com/open3e/open3e/wiki/020-Inbetriebnahme-CAN-Adapter-am-Raspberry)
  for several devices.
- **Ground.** That wiki recommends a third wire to the device's CAN ground
  for an isolated adapter, so that the signals keep a reference. Waveshare
  lists power and signal isolation for the HAT (B) without saying for which
  of its interfaces. The wiki also notes that in the Vitocal 250-A
  instructions of 11/2024 the ground terminal of plug 91 is no longer
  assigned. Check both against your hardware.
- **Termination: exactly one 120 Ω resistor at each of the two ends of the
  bus**, none in between. The HAT has one, switched by a jumper. Whether it
  belongs in depends on where the HAT sits and on what the bus already has.
  With every device powered off, measure between H and L: about 60 Ω is right
  (two resistors in parallel), 120 Ω means one is missing, 40 Ω one too many.
- **One open3e instance per bus.** Its README warns that a second one leads
  to conflicts on the bus.

### 3. Bring the interface up

open3e does not set the bit rate. It takes the interface as the host has
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
  for it until networkd's timeout of 120 s.
- `RestartSec` (`restart-ms` in the script): after a bus-off the controller
  comes back by itself after 10 s. Without it the MCP2515 driver puts the
  chip to sleep, and the interface stays dead until it is taken down and up
  by hand.

Check with `ip -details -statistics link show can0`: `state ERROR-ACTIVE`
and `bitrate 250000`.

### 4. Read one data point

Before anything else, ask the heat pump (address `0x680`, open3e's default)
for a single data point:

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

The first line is expected here: without `-cnfg` the command reads from
`0x680` with open3e's general data point list.

| Observation | Likely cause |
|---|---|
| `OSError: [Errno 19] No such device` | no `can0`: overlay or `dtparam=spi=on` missing, HAT not seated; look at `dmesg` |
| `Failed to transmit: Network is down [Error Code 100]` | the interface exists but is not up (step 3) |
| `ERR/0x680.268 "Did not receive response in time. …"` after 3 s | the request left, nobody answered: H and L swapped, wrong bit rate or `oscillator`, termination, wrong plug |
| `state ERROR-PASSIVE` or `BUS-OFF` in `ip -details -statistics link show can0`, TX errors counting up | the HAT's frames do not get through: same causes |

### 5. Scan the system

`open3e_depictSystem` asks every address for every data point and writes
what it finds: a `devices.json` and, per control unit, a list of the data
points that unit really has (`Open3Edatapoints_<address>.py`). Run it in the
module's image, into [config/](config/), with the module stopped:

```bash
docker compose stop open3e
docker compose --profile open3e run --rm --no-deps --user "$(id -u):$(id -g)" \
    -w /scan -v "$PWD/modules/open3e/config:/scan" \
    open3e open3e_depictSystem -c can0
```

(`/scan`, because the service already mounts the directory at `/config`,
read-only, and Compose does not replace that mount.)

open3e's README puts the scan at 10 to 20 minutes and asks for a new one
after every firmware update of a device. With `-s` it also writes the
`virtdata_<address>.txt` files the simulator plays back.

The `devices.json` it writes needs one change, and allows a second:

```json
{
  "vitocal": {
    "tx": "0x680",
    "dpList": "/config/Open3Edatapoints_680.py",
    "prop": "HPMUMASTER"
  }
}
```

- **`dpList` gets the path inside the container.** open3e resolves it from
  its working directory, not from where `devices.json` lies.
- **The key may become a name.** It is the topic segment and with it the
  `device_key` `open3e/<key>`, and what a list in `OPEN3E_DIDS` refers to.
  The scan writes the address (`"0x680"`), which works just as well and
  survives the next scan: the readable name is the device's `name` in the
  database either way.

A control unit that should not be read at all (a gateway, say) is taken out
of `devices.json`. Left in, but without a row in `devices`, it is still read
in every pass and its values are dropped.

### 6. Switch the module on

1. In `.env`: `OPEN3E_CAN_INTERFACE=can0`, the data points to read in
   `OPEN3E_DIDS` (or [`all`](#reading-every-data-point)),
   and the `open3e-sim` profile removed
   (`docker compose rm -sf open3e-sim` before that, or it keeps running).
2. Every key in `devices.json` needs a row in `devices`; [seed.de.sql](seed.de.sql)
   brings `open3e/vitocal` and `open3e/vitocharge`. After adding one,
   `docker compose restart ingest`.
3. `docker compose up -d open3e`, then `docker compose logs -f open3e`.

## Reading every data point

```
OPEN3E_DIDS=all
```

reads every data point of every control unit in `devices.json`, with open3e's
own option for that (`-a`). It needs the files of a [scan](#5-scan-the-system):
"every data point" is what the unit's `Open3Edatapoints_<address>.py` lists.
Without such a list open3e asks every unit for all 1564 data points it knows
of and publishes an error topic for each one the unit does not have (3102
against the simulator's two units), so the module refuses to start that way.

`-a` reads once and exits. For `all`, open3e is therefore started anew for
every pass, with `OPEN3E_INTERVAL_SECONDS` in between; a list is read by one
process that keeps running. Tested against the simulator: 143 values from 26
data points in every pass, one pass every 62 s at an interval of 60 s.

What that means for the rest of the stack:

- **Every pass stores every value again**, changed or not: schedules,
  serial numbers and error histories as well as temperatures. open3e's list
  for a Vitocal has 539 data points with close to 2000 values.
- **A pass takes time**, and the interval comes on top of it.
- **Unknown values arrive as text stubs** and need curating
  ([docs/architecture.md](../../docs/architecture.md)).
- **A new scan changes what is read**, without touching `.env`.

To leave data points out, give a list instead. This prints the numbers of
one unit, to be put in brackets behind the device's name
(`OPEN3E_DIDS=vitocal.[256,262,…],vitocharge.[…]`):

```bash
grep -oE '^ *[0-9]+ *:' modules/open3e/config/Open3Edatapoints_680.py \
    | tr -d ' :' | paste -sd, -
```

## Switched off on purpose

- **Command channel.** open3e can accept read and *write* commands on an MQTT
  topic (`-l <topic>`). The module starts without it: a historian does not
  write to devices, and by default the broker has no authentication.
- **Debug log.** The UDS library would otherwise write to a file
  `udsoncan.log` without limit; the image redirects it to `/dev/null`.

## Things to know

- Because of QoS 0, up to about two seconds of values can be lost when the
  ingester stops ([docs/architecture.md](../../docs/architecture.md)).
- When a device does not answer, open3e publishes the error message under
  `…/<DID>_ERR/…`. It ends up in the database as a text metric.
- open3e is pinned to a commit in the [Dockerfile](Dockerfile), because topic
  format and behaviour depend on the version.
