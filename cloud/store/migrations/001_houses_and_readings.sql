-- Houses, each with the token its uplink writes with, and their sensors' readings.

CREATE TABLE houses (
    id text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    name text NOT NULL,
    -- Readable without signing in. Only the simulated example house is: a real one's map shows its floor plan, doors
    -- and windows, and its readings when someone is home.
    public boolean NOT NULL DEFAULT false,
    -- SHA-256 of the house's ingest token. The token is 128 random bits, so a fast hash is enough: a slow one, as
    -- for passwords, guards against guessing, and there's no guessing 128 bits.
    token_sha256 bytea NOT NULL CHECK (length(token_sha256) = 32),
    -- The house as `python3 -m housemon --dump` prints it: its sensors, areas, details and walls. Null until the house
    -- sends it.
    layout jsonb,
    layout_updated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE readings (
    house_id text NOT NULL REFERENCES houses ON DELETE CASCADE,
    sensor_id text NOT NULL,
    ts timestamptz NOT NULL, -- when the sensor took it, by the house's clock
    temperature_c double precision NOT NULL,
    humidity double precision,
    battery integer,
    rssi integer,
    simulated boolean NOT NULL DEFAULT false,
    received_at timestamptz NOT NULL DEFAULT now(), -- when it got here, so the uplink's lag can be seen
    -- One reading per sensor per moment: a batch sent again after a lost reply is stored once (ON CONFLICT DO
    -- NOTHING). Delivery from the house is at least once; this makes it count once.
    PRIMARY KEY (house_id, sensor_id, ts)
);

-- History is every sensor's readings over a time range; the primary key serves one sensor's.
CREATE INDEX readings_by_time ON readings (house_id, ts);
