"""Healthcheck: was the last complete pass too long ago?

The publisher only touches the heartbeat file when every meter answered AND
the broker was connected. If the file goes stale the process is still running
but no longer delivering -- exactly the case a plain process check misses.
"""

import os
import sys
import time
from pathlib import Path

heartbeat_file = Path(os.environ.get("MBUS_HEARTBEAT_FILE", "/run/mbus/heartbeat"))
poll_interval = int(os.environ.get("MBUS_POLL_INTERVAL_SECONDS", "120"))

# Two intervals plus a margin: a single missed pass (one garbled telegram,
# say) should not mark the container unhealthy yet.
deadline = poll_interval * 2 + 60

if not heartbeat_file.exists():
    # The first pass may still be running; the start period covers that.
    print(f"no heartbeat yet at {heartbeat_file}")
    sys.exit(1)

age = time.time() - heartbeat_file.stat().st_mtime
if age > deadline:
    print(f"last complete pass {age:.0f}s ago (allowed: {deadline}s)")
    sys.exit(1)

print(f"last complete pass {age:.0f}s ago")
sys.exit(0)
