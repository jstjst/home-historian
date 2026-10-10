"""Simulated M-Bus meters behind a TCP port, to try the mbus module without
hardware.

Answers a REQ_UD2 to ANY primary address with the same data telegram, with
that address written into it. Whatever meters MBUS_METERS names therefore
exist, all with identical, fixed values.

The telegram is a real one from a wired Kamstrup MULTICAL 303 (heat/cooling),
taken from the test suite of wmbusmeters (drivers/src/kamheat.xmq,
"Kamstrup_MC303_Heat_AND_COOLING").

A TCP stream has no parity and no half-duplex line. --echo imitates an RS485
adapter that hands the request back before the answer arrives.
"""

import argparse
import socketserver

TELEGRAM = bytes.fromhex(
    "68888868083072303030302d2c400d0000000004069a00000004863c0000000004146d0f"
    "000004ff077205000004ff08ec0400000322890a0033220000000259720b025d040b0261"
    "6e00022d0e00122db400023b6804123b7a0402ff22000044060000000044863c00000000"
    "44140000000044ff070000000044ff0800000000522d0000523b0000426c00006c16"
)
BROADCAST_ADDRESS = 254


def answer_for(address: int) -> bytes:
    """The telegram as the meter with this primary address would send it."""
    if address == BROADCAST_ADDRESS:
        return TELEGRAM
    # 68 L L 68 | C A CI ... | checksum 16: the checksum covers C to the end
    # of the data, so a changed address needs a new one.
    body = bytearray(TELEGRAM[4:-2])
    body[1] = address
    return TELEGRAM[:4] + bytes(body) + bytes([sum(body) & 0xFF, 0x16])


class Bus(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        # Short frame: 10 C A checksum 16. Anything else is ignored, as a
        # meter would ignore it.
        while frame := self.rfile.read(5):
            if len(frame) < 5 or frame[0] != 0x10 or frame[4] != 0x16:
                continue
            control, address = frame[1], frame[2]
            if frame[3] != (control + address) & 0xFF:
                continue
            if self.server.echo:
                self.wfile.write(frame)
            if control in (0x5B, 0x7B) and (address <= 250 or address == BROADCAST_ADDRESS):
                self.wfile.write(answer_for(address))
                print(f"REQ_UD2 for address {address} answered", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=2400)
    parser.add_argument("--echo", action="store_true", help="send every request back first")
    args = parser.parse_args()

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", args.port), Bus) as server:
        server.echo = args.echo
        print(f"Simulated M-Bus meters on TCP port {args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
