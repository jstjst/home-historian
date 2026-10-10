#!/usr/bin/env python3
r"""Read one M-Bus meter once and show its telegram.

For commissioning (see README.md), not part of the running module: sends ONE
REQ_UD2 to a primary address, prints the answer as raw hex and then record by
record: as pyMeterBus decodes it, and as the module would publish it (key and
payload). The two differ where the module scales, and a record without a unit
in the telegram shows "-": units of published values are master data
(seed.sql), not part of the message.

    docker compose --profile mbus run --rm --no-deps mbus \
        python mbus_dump.py /dev/mbus --address 48

Every call wakes the meter once. Kamstrup states the battery lifetime of the
MULTICAL 303 for at most one reading every 120 s, so do not run this in a
loop.
"""

import argparse
import sys

import meterbus
import serial

from mbus_publisher import flatten_telegram


def read_until_silent(port: serial.SerialBase) -> bytes:
    """Read everything until nothing has arrived for a second."""
    data = b""
    while chunk := port.read(256):
        data += chunk
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("device", help="serial device, e.g. /dev/ttySC0, or a pyserial URL")
    parser.add_argument(
        "-a", "--address", type=int, default=meterbus.ADDRESS_BROADCAST_REPLY,
        help="primary address 0-250; default 254 = 'everyone answers', "
             "only usable with EXACTLY ONE meter on the bus",
    )
    parser.add_argument("-b", "--baudrate", type=int, default=2400)
    args = parser.parse_args()

    # M-Bus is always 8E1. A UART without parity (the Pi's mini UART) raises
    # no error here; the meter simply does not answer.
    with serial.serial_for_url(args.device, args.baudrate, 8, "E", 1, timeout=1) as port:
        port.reset_input_buffer()
        request = bytes(meterbus.send_request_frame(port, args.address))
        answer = read_until_silent(port)

    print(f"sent    : {request.hex()}")
    # Half-duplex RS485: some adapters hand back the bytes just sent.
    if answer.startswith(request):
        print("echo    : yes (our own request came back)")
        answer = answer[len(request):]
    else:
        print("echo    : no")

    if not answer:
        print("received: nothing -- check address, A/B wiring, bus voltage, parity")
        return 1
    print(f"received: {answer.hex()}")

    try:
        frame = meterbus.load(answer)
    except meterbus.MBusFrameDecodeError as error:
        print(f"cannot be decoded: {error}")
        return 1
    except meterbus.exceptions.MBusError as error:  # checksum or length
        print(f"cannot be decoded: {error!r}")
        return 1

    header = frame.body.bodyHeader
    print(
        f"\nmanufacturer {header.manufacturer_field.decodeManufacturer}"
        f"  id {bytes(reversed(header.id_nr)).hex()}"
        f"  version 0x{header.version_field.parts[0]:02x}"
        f"  medium 0x{header.measure_medium_field.parts[0]:02x}"
        f"  primary address {frame.header.aField.parts[0]}"
        f"  status 0x{header.status_field.parts[0]:02x}\n"
    )
    published = {
        measurement.topic.rsplit("/", 1)[1]: measurement.payload
        for measurement in flatten_telegram(answer, "meter", "")
    }
    print(f"{'DIF':<8}{'VIF':<10}{'decoded':<20}{'unit':<10}published as")
    for record in frame.records:
        dif = bytes(record.dib.parts).hex().upper()
        vif = bytes(record.vib.parts).hex().upper()
        try:
            value, unit = record.value, record.unit
        except Exception as error:  # one unknown record must not hide the rest
            value, unit = f"?? {error!r}", ""
        if not isinstance(value, (int, str)):
            value = float(value)
        if unit == "none":  # manufacturer-specific: the telegram names no unit
            unit = "-"
        key = f"{dif}_{vif}"
        sent = f"{key} = {published[key]}" if key in published else "(not published)"
        print(f"{dif:<8}{vif:<10}{value!s:<20}{unit:<10}{sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
