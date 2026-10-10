# mbus module

Reads meters over wired M-Bus (EN 13757-2/-3), such as heat meters, and
publishes every record of their data telegram to MQTT. Decoding is done by
[pyMeterBus](https://github.com/ganehag/pyMeterBus). Written for the Kamstrup
MULTICAL 303; any meter that answers a plain data request should work, but
only that one has curated metrics.

| | |
|---|---|
| Profile | `mbus` (simulator: `mbus-sim`) |
| Topics | `<prefix>/mbus/<meter>/<DIF>_<VIF>` |
| Devices (`device_key`) | `mbus/<meter>`, names from `MBUS_METERS` |
| Timestamp | from the module: the moment of the reading, shared by all records of a telegram |
| MQTT QoS | 1 |
| Status | tested against the simulator, not on real hardware |

## Prerequisites

- An **M-Bus master** (level converter) on a serial port of the host: a USB
  adapter, or a converter behind an RS485 or RS232 interface. It has to supply
  the bus voltage; the meters only modulate it.
- A serial port that can do **8E1**. M-Bus always uses even parity.
- The **primary address** of every meter (0-250).

[Commissioning](#commissioning) below goes through all three.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MBUS_SERIAL_DEVICE` | `/dev/ttyUSB0` | serial device of the host, passed into the container |
| `MBUS_SERIAL_PORT` | `/dev/mbus` | what the module opens; only set it for a [pyserial URL](https://pyserial.readthedocs.io/en/latest/url_handlers.html) such as `socket://host:port` |
| `MBUS_BAUDRATE` | `2400` | the MULTICAL 303 picks 300 or 2400 by itself |
| `MBUS_METERS` | – | meters to read, as `<name>:<primary address>,…` |
| `MBUS_POLL_INTERVAL_SECONDS` | `120` | polling interval, minimum 120 |

**Mind the battery.** A battery-powered meter pays for every answer. Kamstrup
states the battery lifetime of the MULTICAL 303 for at most one reading every
120 s, and its battery cannot be replaced without breaking the verification
seal. Hence the minimum.

Address 254 makes every meter answer at once. It is accepted for a single
configured meter, to try one out without knowing its address; with several
meters on the bus the answers collide.

## Registering the devices

Names and addresses belong to the installation, so [seed.sql](seed.sql) only
brings the metrics. Every name in `MBUS_METERS` needs a row; the module logs
the keys at startup. The part of the installation a meter sits in goes on the
device, because all meters share their metrics:

```sql
INSERT INTO devices (type, device_key, name, category) VALUES
  ('kamstrup', 'mbus/hc1', 'Heat meter HC1', 'hc1'),
  ('kamstrup', 'mbus/hc2', 'Heat meter HC2', 'hc2');
```

The ingester reads the list of devices at startup, so follow up with
`docker compose restart ingest`.

## The records

The topic of a value is the header of its record in hex, as the meter sends
it: `<DIF>_<VIF>`, each with its extension bytes. The DIF says which value of
a quantity this is (`0x` current, `1x` maximum, `3x` during an error state,
`4x`/`5x` stored at the target date), the VIF which quantity. A meter decides
by its configuration what it sends; these are the records of a MULTICAL 303
heat/cooling meter with the standard datagram, as curated in
[seed.sql](seed.sql):

| Key | Quantity | Payload unit |
|---|---|---|
| `04_06`, `04_863C` | heat energy, cooling energy | Wh |
| `04_14` | volume | m³ |
| `04_FF07`, `04_FF08` | inlet energy E8, outlet energy E9 | m³·°C |
| `02_59`, `02_5D`, `02_61` | flow temperature, return temperature, difference | °C, °C, K |
| `02_2D`, `12_2D` | thermal power, its maximum | W |
| `02_3B`, `12_3B` | volume flow, its maximum | m³/h |
| `03_22`, `33_22` | operating time, of which in error | s |
| `02_FF22` | info code (0 = no error) | – |
| `44_…`, `52_…`, `42_6C` | the values stored at the yearly target date, and that date | as above |

Payloads are in the units pyMeterBus scales to, not the meter's display
units: a meter showing 154 kWh arrives as `154000`.

## Behaviour

- **Every pass publishes every record**, including those that rarely change
  (the target-date values). Each reading is a new measurement with a new time.
- **A meter that does not answer costs only itself.** The others are read
  and published; the pass logs one warning.
- **Healthcheck** ([healthcheck.py](healthcheck.py)): the service only counts
  as healthy if *every* configured meter answered recently and the broker was
  connected.
- **The port is opened for each pass**, so an adapter that was unplugged is
  found again without a restart.
- **An echo is tolerated.** Half-duplex RS485 adapters may hand back the bytes
  just sent; the module recognises its own request and skips it.
- **One telegram per meter.** Follow-up telegrams of a multi-telegram answer
  are not requested, and meters are addressed by primary address only.
- **Read only.** The module sends nothing but the data request.

## Commissioning

Written from the documents named below and tested against the simulator; not
tried on the hardware yet.

### 1. At the meter (MULTICAL 303)

From Kamstrup's technical description 5512-2701:

- **Wired M-Bus is a variant of the meter**, not an add-on: type number
  `303-xxxxxx-xxx20`. `…30` is wireless M-Bus and cannot be converted.
- **Primary address:** shown in the display (TECH loop, reading `2-101-01`).
  From the factory it is the last two or three digits of the customer number,
  which defaults to the serial number. It can be changed in the SETUP loop
  (`3-009`). Every meter on the bus needs its own.
- The meter picks 300 or 2400 baud by itself.

### 2. Wiring

- All meters in parallel on the M-Bus terminals of the master. Polarity does
  not matter on M-Bus.
- A converter behind RS485: A to A, B to B. If its serial side can be set:
  2400 baud, 8 data bits, even parity, 1 stop bit.

### 3. The serial port

Any port works that can send a parity bit. On a Raspberry Pi:

- **The mini UART (`/dev/ttyS0`) cannot.** It is the default on GPIO 14/15 of
  the models with Bluetooth. Nothing fails; the meter just does not answer.
  Use the PL011 instead (`dtoverlay=disable-bt` or `miniuart-bt` in
  `config.txt`, then `/dev/ttyAMA0`).
- **HATs with a UART chip of their own are not affected.** The Waveshare
  RS485 CAN HAT (B), for one, carries an SC16IS752 on SPI1; the kernel driver
  supports parity and names the two channels `/dev/ttySC0` and `/dev/ttySC1`.
  Its wiki gives this line for `config.txt`:

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

[mbus_dump.py](mbus_dump.py) is part of the image. It sends exactly one
request and prints the answer raw and decoded:

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

DIF     VIF       value                   unit
04      06        154000                  Wh
…
```

Run it once per meter, not in a loop (the battery). The DIF and VIF columns
are the keys of [the records](#the-records): compare them with
[seed.sql](seed.sql) to see which of your meter's records are curated.

| Observation | Likely cause |
|---|---|
| `received: nothing`, `echo: no` | wrong serial device or channel, A/B swapped, wrong address, no bus voltage, a UART without parity, a wireless-only meter |
| only the echo comes back | the request leaves but no meter answers: address, bus voltage, baud rate or parity on the converter |
| `cannot be decoded` | parity or baud rate on the converter does not match |

### 5. Switch the module on

1. Set `MBUS_METERS` and add `mbus` to `COMPOSE_PROFILES`.
2. On a database that already exists, apply [seed.sql](seed.sql) by hand (the
   command is in its header); it is only loaded automatically on first start.
3. Insert the device rows ([Registering the devices](#registering-the-devices))
   and `docker compose restart ingest`.
4. `docker compose up -d mbus`. The log should say
   `… values published from N of N meters` on every pass.

## Simulator

[sim/fake_meter.py](sim/fake_meter.py) plays M-Bus meters behind a TCP port.
It answers every primary address with the same telegram, a real one from a
wired MULTICAL 303, with fixed values:

```
COMPOSE_PROFILES=…,mbus,mbus-sim
MBUS_SERIAL_DEVICE=/dev/null
MBUS_SERIAL_PORT=socket://mbus-sim:2400
MBUS_METERS=hc1:48,hc2:49
```

`MBUS_SERIAL_DEVICE=/dev/null` is there because Compose cannot leave the
device out: something has to be passed in, even when the module talks TCP.
