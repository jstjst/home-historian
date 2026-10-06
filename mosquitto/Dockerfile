# Mosquitto broker with the "message-timestamp" example plugin.
# The plugin attaches an MQTT 5 user property "timestamp" (ISO 8601, time of
# receipt in the broker) to every message. The official image does not ship
# it precompiled, so it is built here.

# ---- Stage 1: compile the plugin (musl/Alpine, matching the broker image) ----
FROM alpine:3.19 AS builder
ARG MOSQ_VERSION=v2.0.20
RUN apk add --no-cache build-base git
RUN git clone --depth 1 --branch ${MOSQ_VERSION} \
      https://github.com/eclipse-mosquitto/mosquitto.git /src
WORKDIR /src/plugins/message-timestamp
RUN gcc -I/src/include -I/src -fPIC -shared \
      mosquitto_message_timestamp.c -o /mosquitto_message_timestamp.so

# ---- Stage 2: broker image + plugin ----
FROM eclipse-mosquitto:2.0
COPY --from=builder /mosquitto_message_timestamp.so \
     /mosquitto/plugins/mosquitto_message_timestamp.so
