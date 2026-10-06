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
| `OPEN3E_DIDS` | `vitocal.268,vitocal.396,vitocharge.1603` | data points to read, as `<device>.<DID>` |
| `OPEN3E_INTERVAL_SECONDS` | `5` | polling interval |

Which control units exist is defined in
[config/devices.json](config/devices.json) (name, CAN address). Every name
there needs a row in `devices`; see [seed.sql](seed.sql), which also brings a
few curated metrics.

The service runs on the host network, because the CAN interface is a kernel
interface of the host.

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

## Real hardware

1. Connect the CAN adapter and bring the interface up:
   `sudo ip link set can0 up type can bitrate 250000`
2. Scan the real system once: `open3e_depictSystem` produces a matching
   `devices.json` along with data point lists. Put them into `config/`.
3. In `.env`: `OPEN3E_CAN_INTERFACE=can0`, adjust `OPEN3E_DIDS`, remove the
   `open3e-sim` profile.
4. Add a row to `devices` for every device in `devices.json`.

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
