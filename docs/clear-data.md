# Clearing the data

How to discard all measurements and all uncurated metrics in a running
installation, while the schema, the devices, the curated metrics and Grafana
stay as they are.

This cannot be undone. If the measurements might still be needed, back the
database up first.

| | |
|---|---|
| Removed | every row of `measurements`; everything materialised in `measurements_1h` and `consumption_1d`; every metric that is still a stub (`display_name = native_key`) |
| Kept | `devices`, curated metrics, the schema, both aggregates and their refresh policies, the role `grafana`, the dashboards, the broker's buffer |

To start from nothing at all, `docker compose down -v` is the shorter way. It
removes every volume, so the devices, the curated metrics, Grafana's state,
the broker's buffer and the ViCare token go with it.

## Steps

All `psql` calls below run as the database owner. With the database on another
host, run the same statements with `psql` against that database.

**1. Look at what would be deleted.** A stub is recognised by its name only.
A metric that was given a unit or a type but still carries its `native_key` as
`display_name` counts as a stub and is deleted. Give it a `display_name` first
if it should stay.

```bash
docker compose exec -T timescaledb sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
SELECT metric_id, type, native_key, unit, category, value_type
FROM metrics WHERE display_name = native_key ORDER BY type, native_key;
SQL
```

**2. Stop the ingester.** The modules keep running; the broker buffers their
values.

```bash
docker compose stop ingest
```

**3. Clear, and check the result.** All four counts have to be 0.

```bash
docker compose exec -T timescaledb sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
TRUNCATE measurements;
TRUNCATE measurements_1h, consumption_1d;
DELETE FROM metrics WHERE display_name = native_key;

SELECT (SELECT count(*) FROM measurements)    AS measurements,
       (SELECT count(*) FROM measurements_1h) AS measurements_1h,
       (SELECT count(*) FROM consumption_1d)  AS consumption_1d,
       (SELECT count(*) FROM metrics WHERE display_name = native_key) AS stubs;
SQL
```

**4. Start the ingester.**

```bash
docker compose start ingest
```

## What to expect afterwards

- The database does not stay empty. The values the broker buffered while
  the ingester was stopped are written as soon as it is back, with their
  original timestamps.
- Stubs of quantities that are still published come back, with a new
  `metric_id`, on their first value. Deleting stubs only gets rid of keys that
  nobody publishes any more (a retired device, a narrower selection of data
  points, renamed topics). To keep a quantity out of the database permanently,
  stop publishing it in the module.
- Dashboards show the new values at once. Truncating an aggregate resets
  its watermark, so until the next refresh it reads everything from the raw
  data.

## Why the steps are what they are

- The ingester has to be stopped. It caches `(type, native_key) →
  metric_id` for up to an hour (`INGEST_METRIC_CACHE_SECONDS`). If a stub is
  deleted underneath it, it keeps writing rows with the old `metric_id`. There
  is no foreign key to stop that, and those rows show up in `v_measurements`
  without a name. Restarting the ingester afterwards does not repair them.
- The aggregates are cleared separately. `TRUNCATE measurements` leaves
  what is already materialised in `measurements_1h` and `consumption_1d`
  untouched, and the refresh policies only look back 3 and 7 days. Older
  buckets would stay on the dashboards permanently.
- The measurements are removed with `TRUNCATE`, not `DELETE`. It drops the
  chunks, so the disk space is free immediately.
- Measurements and stubs are removed together. Deleting stubs while keeping
  their measurements would leave rows without a metric, as above.

Checked against TimescaleDB 2.17.2, the version in `compose.yml`.
