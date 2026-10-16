"""Healthcheck: were both connections up recently?

The publisher touches the heartbeat file every 30 s, but only while a
device to publish exists AND the broker AND Tuya's cloud are connected. A
battery sensor reports rarely, so the age of the last value says nothing; the
connections are what the module can vouch for.
"""

import os
import sys
import time
from pathlib import Path

heartbeat_file = Path(os.environ.get("TUYA_HEARTBEAT_FILE", "/run/tuya/heartbeat"))

# Four heartbeats: the SDK renews its connection to Tuya every two hours,
# which takes a few seconds and should not count.
deadline = 120

if not heartbeat_file.exists():
    # Loading the devices may still be running; the start period covers that.
    print(f"no heartbeat yet at {heartbeat_file}")
    sys.exit(1)

age = time.time() - heartbeat_file.stat().st_mtime
if age > deadline:
    print(f"last heartbeat {age:.0f}s ago (allowed: {deadline}s)")
    sys.exit(1)

print(f"last heartbeat {age:.0f}s ago")
sys.exit(0)
