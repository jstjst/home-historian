# open3e module: reads data points from Viessmann E3 devices (CAN/UDS) and
# publishes them to MQTT. open3e itself: https://github.com/open3e/open3e
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# Pinned to a commit (version 0.7.5; open3e has no release tags): topic
# format and QoS behaviour depend on the version.
ARG OPEN3E_REF=6553c10453a6bae1210908f38a5af2481a46a3e5
RUN pip install --no-cache-dir "git+https://github.com/open3e/open3e.git@${OPEN3E_REF}"

# udsoncan writes a debug log "udsoncan.log" into the working directory
# without being asked, and it grows without bound. Send it to /dev/null.
WORKDIR /work
RUN ln -s /dev/null /work/udsoncan.log

CMD ["open3e", "--help"]
