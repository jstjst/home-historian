# mbus module

Reads meters over wired M-Bus (EN 13757-2/-3), such as heat meters, and
publishes every record of their data telegram to MQTT. Decoding is done by
[pyMeterBus](https://github.com/ganehag/pyMeterBus). The module was written
for the Kamstrup MULTICAL 303. Any meter that answers a plain data request
should work, but only that one has metric definitions.

| | |
|---|---|
| Profile | `mbus` (simulator: `mbus-sim`) |
| Topics | `<prefix>/mbus/<meter>/<DIF>_<VIF>` |
| Devices (`device_key`) | `mbus/<meter>`, one per meter, names from `MBUS_METERS` |
| Timestamp | from the module: the time of the reading, shared by all records of a telegram |
| MQTT QoS | 1 |
| Status | in use with four MULTICAL 303 |

## Prerequisites

- An M-Bus master (level converter) on a serial port of the host: a USB
  adapter, or a converter behind an RS485 or RS232 interface. It has to supply
  the bus voltage; the meters only modulate it.
- A serial port that supports 8E1. M-Bus always uses even parity.
- The primary address of every meter (0-250).

[Commissioning](#commissioning) covers all three.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MBUS_SERIAL_DEVICE` | `/dev/ttyUSB0` | serial device of the host, passed into the container |
| `MBUS_SERIAL_PORT` | `/dev/mbus` | what the module opens; only set it for a [pyserial URL](https://pyserial.readthedocs.io/en/latest/url_handlers.html) such as `socket://host:port` |
| `MBUS_BAUDRATE` | `2400` | the MULTICAL 303 detects 300 or 2400 by itself |
| `MBUS_METERS` | – | meters to read, as `<name>:<primary address>,…` |
| `MBUS_POLL_INTERVAL_SECONDS` | `120` | polling interval, minimum 120 |

The minimum of 120 s protects the meter's battery. Kamstrup states the
battery lifetime of the MULTICAL 303 for at most one reading every 120 s, and
the battery cannot be replaced without breaking the verification seal.

Address 254 makes every meter answer at once. It is accepted for a single
configured meter, to try one out without knowing its address. With several
meters on the bus the answers collide.

## Devices and metrics

[devices.sql](devices.sql) adds the device type `multical303` and no devices,
because names and addresses belong to the installation. Every name in
`MBUS_METERS` needs a row in `devices`; the module logs the keys at startup.
The part of the installation a meter sits in goes on the device, because all
meters share their metrics:

```sql
INSERT INTO devices (type, device_key, name, category) VALUES
  ('multical303', 'mbus/hc1', 'Heat meter HC1', 'hc1'),
  ('multical303', 'mbus/hc2', 'Heat meter HC2', 'hc2');
```

The ingester reads the list of devices at startup: after adding one,
`docker compose restart ingest`.

[metrics.de.sql](metrics.de.sql) defines the records listed under
[Values](#values), with German display names.
[metrics.en.sql](metrics.en.sql) has English names for all of them. A meter
configured differently sends other records, which the ingester stores as
stubs ([docs/architecture.md](../../docs/architecture.md)).

Both files are loaded when the database starts for the first time. On an
existing database:

```bash
for f in devices.sql metrics.de.sql; do
  docker compose exec -T timescaledb sh -c \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < modules/mbus/$f
done
```

## Values

The key of a value is the header of its record in hex, as the meter sends
it: `<DIF>_<VIF>`, each with its extension bytes. The DIF says which value of
a quantity it is (`0x` current, `1x` maximum, `3x` during an error state,
`4x`/`5x` stored at the target date). The VIF says which quantity and at
which resolution. A meter's configuration decides what it sends. These are
the records of two MULTICAL 303 with the standard datagram:

| Key | Quantity | Payload unit |
|---|---|---|
| `04_05` or `04_06` | heat energy, sent in 0.1 kWh or in kWh | Wh |
| `04_863C` | cooling energy (heat/cooling meters only) | Wh |
| `04_13` or `04_14` | volume, sent in litres or in 0.01 m³ | m³ |
| `04_FF07`, `04_FF08` | E8, E9: volume × inlet / outlet temperature | m³·°C |
| `02_59`, `02_5D`, `02_61` | flow temperature, return temperature, difference | °C, °C, K |
| `02_2D`, `12_2D` | thermal power, its maximum | W |
| `02_3B`, `12_3B` | volume flow, its maximum | m³/h |
| `03_22`, `33_22` | operating time, of which in error | s |
| `02_FF22` | info code (0 = no error) | – |
| `44_…`, `52_…`, `42_6C` | the values stored at the yearly target date, and that date | as above |

Payloads are in the units pyMeterBus scales to, not the meter's display
units: a meter showing 154 kWh arrives as `154000`. This holds for every
resolution, so `04_05` and `04_06` are the same quantity in the same unit
under two keys and have the same name in `metrics.de.sql`.

E8 and E9 are Kamstrup's registers for average temperatures. With every
volume step the meter adds volume × inlet temperature to E8 and volume ×
outlet temperature to E9. Over any period, the increase of E8 divided by the
increase of the volume is the flow-weighted mean inlet temperature; E9 gives
the outlet temperature in the same way.

E8 and E9 are the only values the module scales itself. Their records are
manufacturer-specific and carry no scale, and their resolution follows that
of the volume (technical description 5512-2701, 7.1.2): a meter that counts
the volume in litres sends m³·°C × 10, one that counts in 0.01 m³ sends
m³·°C. The module reads the volume's resolution from the volume record of the
same telegram and publishes m³·°C. Example: a meter with 1.71 m³ at about
21 °C sent 377, which is published as `37.7`. Without a volume record in the
telegram, E8 and E9 are skipped with a warning.

The module has no setting for the meter's configuration. Unit and resolution
are the meter's B and CCC code (display, TECH loop, `2-011-10`), and every
telegram carries both in its VIFs. B = 3 with CCC = 407, for example, means
0.1 kWh, litres, l/h and 0.1 kW and arrives as `04_05`, `04_13`, `02_3B` and
`02_2D`. A meter set to GJ (B = 2) sends its energy in joules under other
keys (`04_0E`, `04_0F`), which have no definitions.

## Behaviour

- Every pass publishes every record, including those that rarely change (the
  target-date values). Each reading is a new measurement with a new time.
- A request that gets no reply is repeated once. Before every request the bus
  is left idle for 0.1 s.
- A meter that does not answer after the repeat is skipped. The others are
  read and published, and the pass logs a warning.
- The port is opened for each pass, so an adapter that was unplugged is found
  again without a restart.
- An echo is tolerated. Half-duplex RS485 adapters may return the bytes just
  sent; the module recognises its own request and skips it.
- One telegram per meter. Follow-up telegrams of a multi-telegram answer are
  not requested, and meters are addressed by primary address only.
- The module sends nothing but the data request.
- Healthcheck ([healthcheck.py](healthcheck.py)): the service counts as
  healthy only if every configured meter answered recently and the broker was
  connected.

## Commissioning

### 1. At the meter (MULTICAL 303)

From Kamstrup's technical description 5512-2701:

- Wired M-Bus is a variant of the meter, not an add-on: type number
  `303-xxxxxx-xxx20`. `…30` is wireless M-Bus and cannot be converted.
- The primary address is shown in the display (TECH loop, reading
  `2-101-01`). From the factory it is the last two or three digits of the
  customer number, which defaults to the serial number. It can be changed in
  the SETUP loop (`3-009`). Every meter on the bus needs its own.
- The meter detects 300 or 2400 baud by itself.

### 2. Wiring

- All meters in parallel on the M-Bus terminals of the master. Polarity does
  not matter on M-Bus.
- A converter behind RS485: A to A, B to B. If its serial side can be set:
  2400 baud, 8 data bits, even parity, 1 stop bit.

### 3. The serial port

Any port that can send a parity bit works. On a Raspberry Pi:

- The mini UART (`/dev/ttyS0`) cannot. It is the default on GPIO 14/15 of the
  models with Bluetooth. No error is reported; the meter does not answer.
  Use the PL011 instead (`dtoverlay=disable-bt` or `miniuart-bt` in
  `config.txt`, then `/dev/ttyAMA0`).
- HATs with a UART chip of their own are not affected. The Waveshare RS485
  CAN HAT (B) carries an SC16IS752 on SPI1; the kernel driver supports parity
  and names the two channels `/dev/ttySC0` and `/dev/ttySC1`. Its wiki gives
  this line for `config.txt`:

  ```
  dtoverlay=sc16is752-spi1,int_pin=24
  ```

  Current Raspberry Pi kernels (6.12) mark that overlay as deprecated. If no
  `/dev/ttySC0` appears after the reboot, its replacement is:

  ```
  dtoverlay=sc16is75x-spi,sc16is752,spi1-1cs,spi1-0,int_pin=24
  ```

Set `MBUS_SERIAL_DEVICE` to the device and check that it belongs to the group
`dialout` (`ls -l /dev/ttySC0`); the container gets its access through that
group.

### 4. Read one telegram

[mbus_dump.py](mbus_dump.py) is part of the image. It sends one request and
prints the answer raw, decoded, and as the module would publish it:

```bash
docker compose --profile mbus build mbus
docker compose --profile mbus run --rm --no-deps mbus \
    python mbus_dump.py /dev/mbus --address <address>
```

```
sent    : 105b308b16
echo    : no
received: 68888868083072…6c16

manufacturer KAM  id 30303030  version 0x40  medium 0x0d  primary address 48  status 0x00

DIF     VIF       decoded             unit      published as
04      05        0                   Wh        04_05 = 0
04      13        1.71                m^3       04_13 = 1.71
04      FF07      377.0               -         04_FF07 = 37.7
…
```

Run it once per meter, not in a loop, because every request uses the meter's
battery. "decoded" is what pyMeterBus reads from the record, "published as"
the key and payload the module sends; they differ where the module scales (E8
and E9). A `-` means the record carries no unit. The published message
carries no unit: units are part of the metric definitions. Compare the keys
with [metrics.de.sql](metrics.de.sql) to see which of the meter's records
have a definition.

| Observation | Likely cause |
|---|---|
| `received: nothing`, `echo: no` | wrong serial device or channel, A/B swapped, wrong address, no bus voltage, a UART without parity, a wireless-only meter |
| only the echo comes back | the request is sent but no meter answers: address, bus voltage, baud rate or parity on the converter |
| `cannot be decoded` | parity or baud rate on the converter does not match |

### 5. Switch the module on

1. Set `MBUS_METERS` and add `mbus` to `COMPOSE_PROFILES`.
2. On a database that already exists, apply `devices.sql` and
   `metrics.de.sql` ([Devices and metrics](#devices-and-metrics)).
3. Insert the device rows and `docker compose restart ingest`.
4. `docker compose up -d mbus`. The log should say
   `… values published from N of N meters` on every pass.

## Simulator

[sim/fake_meter.py](sim/fake_meter.py) answers as M-Bus meters behind a TCP
port. Every primary address gets the same telegram, a real one from a wired
MULTICAL 303, with fixed values:

```
COMPOSE_PROFILES=…,mbus,mbus-sim
MBUS_SERIAL_DEVICE=/dev/null
MBUS_SERIAL_PORT=socket://mbus-sim:2400
MBUS_METERS=hc1:48,hc2:49
```

`MBUS_SERIAL_DEVICE=/dev/null` is needed because Compose cannot leave the
device out of the service, even when the module connects over TCP.
