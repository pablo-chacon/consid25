-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- Routes from A*
CREATE TABLE IF NOT EXISTS "astar_routes" (
                                              id SERIAL PRIMARY KEY,
                                              client_id TEXT NOT NULL,
                                              stop_id TEXT,  -- ✅ NEW (was site_id)
                                              parent_station TEXT,  -- ✅ NEW (was stop_area_id)
                                              target_type TEXT CHECK (target_type IN ('poi', 'stop_point')) NOT NULL,
                                              poi_id INTEGER,
                                              origin_lat FLOAT NOT NULL,
                                              origin_lon FLOAT NOT NULL,
                                              destination_lat FLOAT NOT NULL,
                                              destination_lon FLOAT NOT NULL,
                                              path GEOMETRY(LineString, 4326) NOT NULL,
                                              distance FLOAT NOT NULL,
                                              efficiency_score FLOAT,
                                              decision_context TEXT CHECK (
                                                  decision_context IN (
                                                                       'initial_prediction', 'routed_to_poi', 'routed_to_departure',
                                                                       'deviation_detected', 'fallback_astar', 'fallback_stop_point', 'rerouted_mapf'
                                                      )
                                                  ) DEFAULT 'initial_prediction',
                                              predicted_eta TIMESTAMP,
                                              created_at TIMESTAMP DEFAULT NOW()
);

-- MAPF routing results
CREATE TABLE IF NOT EXISTS "mapf_routes" (
                                             "id" SERIAL PRIMARY KEY,
                                             "client_id" TEXT NOT NULL,
                                             "stop_id" TEXT,
                                             "destination_lat" DOUBLE PRECISION NOT NULL,
                                             "destination_lon" DOUBLE PRECISION NOT NULL,
                                             "path" GEOMETRY(LineString, 4326),
                                             "distance" DOUBLE PRECISION GENERATED ALWAYS AS (
                                                      ST_LengthSpheroid(path, 'SPHEROID["WGS 84",6378137,298.257223563]')
                                                      ) STORED,
                                             "success" BOOLEAN DEFAULT TRUE,
                                             "decision_context" TEXT CHECK (
                                                 "decision_context" IN (
                                                                        'initial_prediction',
                                                                        'routed_to_poi',
                                                                        'routed_to_departure',
                                                                        'deviation_detected',
                                                                        'fallback_astar',
                                                                        'rerouted_mapf',
                                                                        'mapf_predicted'  -- ✅ Added new valid option
                                                     )
                                                 ) DEFAULT 'initial_prediction',
                                             "created_at" TIMESTAMP DEFAULT NOW()
);


-- User location patterns
CREATE TABLE "user_patterns" (
                                 "pattern_id" SERIAL PRIMARY KEY,
                                 "client_id" TEXT NOT NULL,
                                 "lat" FLOAT NOT NULL,
                                 "lon" FLOAT NOT NULL,
                                 "pattern_type" VARCHAR(50),
                                 "geom" GEOMETRY(LineString, 4326),
                                 "timestamp" TIMESTAMP DEFAULT NOW()
);

-- Points of Interest
CREATE TABLE "pois" (
                        "poi_id" SERIAL PRIMARY KEY,
                        "client_id" TEXT NOT NULL,
                        "lat" FLOAT NOT NULL,
                        "lon" FLOAT NOT NULL,
                        "geom" GEOMETRY(Point, 4326) NOT NULL,
                        "time_spent" FLOAT NOT NULL,
                        "poi_rank" FLOAT NOT NULL,
                        "source" VARCHAR(50) DEFAULT 'detected',
                        "visit_start" TIMESTAMP,
                        "visit_count" INTEGER DEFAULT 0,
                        "created_at" TIMESTAMP DEFAULT NOW()
);

-- Area zones
CREATE TABLE "hotspots" (
                            "hotspot_id" SERIAL PRIMARY KEY,
                            "client_id" TEXT NOT NULL,
                            "lat" FLOAT NOT NULL,
                            "lon" FLOAT NOT NULL,
                            "radius" FLOAT NOT NULL,
                            "density" FLOAT,
                            "type" VARCHAR(50),
                            "time_spent" FLOAT,
                            "source_type" TEXT DEFAULT 'trajectory',
                            "geom" GEOMETRY(Point, 4326) NOT NULL,
                            "created_at" TIMESTAMP DEFAULT NOW(),
                            "updated_at" TIMESTAMP DEFAULT NOW(),
                            CONSTRAINT "unique_hotspot" UNIQUE ("client_id", "lat", "lon")
);

-- Predicted POI visit times
CREATE TABLE "predicted_pois_sequence" (
                                           "id" SERIAL PRIMARY KEY,
                                           "client_id" TEXT NOT NULL,
                                           "predicted_lat" FLOAT NOT NULL,
                                           "predicted_lon" FLOAT NOT NULL,
                                           "predicted_visit_time" TIMESTAMP NOT NULL,
                                           "prediction_type" VARCHAR(10) CHECK ("prediction_type" IN ('daily', 'weekly')),
                                           "geom" GEOMETRY(Point, 4326) NOT NULL,
                                           "poi_rank" FLOAT,
                                           "time_spent" FLOAT,
                                           "created_at" TIMESTAMP DEFAULT NOW(),
                                           UNIQUE ("client_id", "predicted_visit_time")
);

-- Considition namespace
CREATE TABLE IF NOT EXISTS consid_maps (
                                       map_name TEXT PRIMARY KEY,
                                       dim_x INT NOT NULL,
                                       dim_y INT NOT NULL,
                                       created_at TIMESTAMPTZ DEFAULT now()
);


CREATE TABLE IF NOT EXISTS consid_nodes (
                                        map_name TEXT NOT NULL,
                                        node_id TEXT NOT NULL,  -- e.g. "1.7"
                                        x INT NOT NULL,
                                        y INT NOT NULL,
                                        zone_id TEXT,
                                        PRIMARY KEY (map_name, node_id),
                                        FOREIGN KEY (map_name) REFERENCES consid_maps(map_name) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS consid_node_targets (
                                       map_name TEXT NOT NULL,
                                       node_id TEXT NOT NULL,
                                       target_type TEXT NOT NULL,     -- 'Null' | 'ChargingStation' | ...
                                       target_props JSONB NOT NULL DEFAULT '{}'::jsonb,
                                       PRIMARY KEY (map_name, node_id),
                                       FOREIGN KEY (map_name, node_id) REFERENCES consid_nodes(map_name, node_id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS consid_ticks (
                                        map_name TEXT NOT NULL,
                                        tick INT NOT NULL,
                                        ingested_at TIMESTAMPTZ DEFAULT now(),
                                        PRIMARY KEY (map_name, tick),
                                        FOREIGN KEY (map_name) REFERENCES consid_maps(map_name) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS consid_charger_status (
                                         map_name TEXT NOT NULL,
                                         tick INT NOT NULL,
                                         node_id TEXT NOT NULL,
                                         available INT,
                                         broken INT,
                                         total INT,
                                         speed_per_charger INT,
                                         PRIMARY KEY (map_name, tick, node_id),
                                         FOREIGN KEY (map_name, tick) REFERENCES consid_ticks(map_name, tick) ON DELETE CASCADE,
                                         FOREIGN KEY (map_name, node_id) REFERENCES consid_nodes(map_name, node_id) ON DELETE CASCADE
);

-- Neighbor grid edges for A* or preview routing
CREATE TABLE IF NOT EXISTS consid_edges (
                                        map_name TEXT NOT NULL,
                                        from_node TEXT NOT NULL,
                                        to_node TEXT NOT NULL,
                                        cost NUMERIC NOT NULL,
                                        PRIMARY KEY (map_name, from_node, to_node),
                                        FOREIGN KEY (map_name, from_node) REFERENCES consid_nodes(map_name, node_id) ON DELETE CASCADE
);


-- Per-EV per-tick trace (grid-based)
CREATE TABLE IF NOT EXISTS ev_trajectories (
                                       map_name TEXT NOT NULL,
                                       ev_id    TEXT NOT NULL,           -- vehicle id
                                       tick     INT  NOT NULL,           -- sim tick (or real step)
                                       node_id  TEXT NOT NULL,           -- "x.y" or grid id
                                       x        INT  NOT NULL,
                                       y        INT  NOT NULL,

                                       soc_kwh          NUMERIC,         -- state of charge (kWh)
                                       battery_kwh      NUMERIC,         -- capacity (kWh)
                                       state            TEXT,            -- driving|charging|waiting|boarding|alighting|idle
                                       customer_id      TEXT,            -- if carrying a customer/group
                                       trip_intent_id   TEXT,            -- optional: current mission/trip id
                                       meta             JSONB NOT NULL DEFAULT '{}'::jsonb,

                                       ts               TIMESTAMPTZ DEFAULT now(),  -- ingest timestamp (not sim time)

                                       PRIMARY KEY (map_name, ev_id, tick),
                                       FOREIGN KEY (map_name, node_id) REFERENCES consid_nodes(map_name, node_id) ON DELETE CASCADE
);

-- Discrete EV events (charging, pickup, dropoff, reroute, etc.)
CREATE TABLE IF NOT EXISTS ev_events (
                                     map_name   TEXT NOT NULL,
                                     ev_id      TEXT NOT NULL,
                                     tick       INT  NOT NULL,
                                     event_type TEXT NOT NULL, -- charge_start|charge_end|pickup|dropoff|reroute|fault|wait_start|wait_end
                                     node_id    TEXT NOT NULL,
                                     meta       JSONB NOT NULL DEFAULT '{}'::jsonb,
                                     ts         TIMESTAMPTZ DEFAULT now(),
                                     PRIMARY KEY (map_name, ev_id, tick, event_type)
);

-- Link a grid node to a public-transit stop_point (bidirectional by convention)
CREATE TABLE IF NOT EXISTS transfer_edges (
                                      map_name  TEXT NOT NULL,
                                      node_id   TEXT NOT NULL,      -- grid node (consid_nodes)
                                      stop_gid  TEXT NOT NULL,      -- your GTFS stop_point/site gid
                                      xfer_cost_s INT NOT NULL DEFAULT 90,  -- park/lock/board penalty
                                      meta      JSONB NOT NULL DEFAULT '{}'::jsonb,
                                      PRIMARY KEY (map_name, node_id, stop_gid)
);

CREATE TABLE IF NOT EXISTS "consid_games" (
                                              "game_id"    TEXT PRIMARY KEY,
                                              "map_name"   TEXT NOT NULL REFERENCES "consid_maps"("map_name") ON DELETE CASCADE,
                                              "play_to_tick" INT NOT NULL,
                                              "started_at" TIMESTAMPTZ DEFAULT now(),
                                              "last_response_at" TIMESTAMPTZ,
                                              "score_total" NUMERIC,
                                              "score_kwh_revenue" NUMERIC,
                                              "score_customer_satisfaction" NUMERIC
);

CREATE TABLE IF NOT EXISTS "consid_customers" (
                                                  "map_name"     TEXT NOT NULL,
                                                  "customer_id"  TEXT NOT NULL,
                                                  "persona"      TEXT,
                                                  "vehicle_type" TEXT,
                                                  "max_charge_kwh" NUMERIC,
                                                  "ev_id"        TEXT,       -- if the API binds a vehicle identity
                                                  "created_at"   TIMESTAMPTZ DEFAULT now(),
                                                  PRIMARY KEY ("map_name", "customer_id")
);


CREATE TABLE IF NOT EXISTS "consid_zones" (
                                              "map_name"  TEXT NOT NULL REFERENCES "consid_maps"("map_name") ON DELETE CASCADE,
                                              "zone_id"   TEXT NOT NULL,
                                              "bbox"      JSONB,                 -- or 4 ints if you prefer
                                              "meta"      JSONB NOT NULL DEFAULT '{}'::jsonb,
                                              PRIMARY KEY ("map_name","zone_id")
);

CREATE TABLE IF NOT EXISTS "consid_zone_logs" (
                                                  "map_name"        TEXT NOT NULL,
                                                  "tick"            INT  NOT NULL,
                                                  "zone_id"         TEXT NOT NULL,
                                                  "total_production"   NUMERIC,      -- kWh produced this tick
                                                  "total_demand"       NUMERIC,      -- kWh demand
                                                  "total_revenue"      NUMERIC,      -- if provided
                                                  "weather_type"       TEXT,
                                                  "sourceinfo_json"    JSONB,        -- detailed per-source arrays
                                                  "storageinfo_json"   JSONB,        -- storages arrays
                                                  "ingested_at"        TIMESTAMPTZ DEFAULT now(),
                                                  PRIMARY KEY ("map_name","tick","zone_id"),
                                                  FOREIGN KEY ("map_name","zone_id") REFERENCES "consid_zones"("map_name","zone_id") ON DELETE CASCADE
);



-- POI indexes
CREATE INDEX IF NOT EXISTS "pois_geom_idx" ON "pois" USING GIST ("geom");
CREATE INDEX IF NOT EXISTS "pois_client_idx" ON "pois" ("client_id");
CREATE INDEX IF NOT EXISTS "pois_client_lat_lon_idx" ON "pois" ("client_id", "lat", "lon");
CREATE INDEX IF NOT EXISTS "pois_rank_idx" ON "pois" ("poi_rank");
-- Hotspot and prediction indexes
CREATE INDEX IF NOT EXISTS "hotspots_geom_idx" ON "hotspots" USING GIST ("geom");
CREATE INDEX IF NOT EXISTS "hotspots_client_idx" ON "hotspots" ("client_id");
CREATE INDEX IF NOT EXISTS "hotspots_type_idx" ON "hotspots" ("type");
CREATE INDEX IF NOT EXISTS "hotspots_source_type_idx" ON "hotspots" ("source_type");

CREATE INDEX IF NOT EXISTS "pois_sequence_client_idx" ON "predicted_pois_sequence" ("client_id");
CREATE INDEX IF NOT EXISTS "predicted_pois_sequence_time_idx" ON "predicted_pois_sequence" ("predicted_visit_time");
CREATE INDEX IF NOT EXISTS "predicted_pois_sequence_type_idx" ON "predicted_pois_sequence" ("prediction_type");
-- Routing indexes
CREATE INDEX IF NOT EXISTS "astar_routes_stop_id_idx" ON astar_routes ("stop_id");
CREATE INDEX IF NOT EXISTS "astar_routes_client_idx" ON "astar_routes" ("client_id");
CREATE INDEX IF NOT EXISTS idx_astar_stop_eta ON astar_routes (stop_id, predicted_eta);

CREATE INDEX IF NOT EXISTS "astar_routes_eta_idx" ON "astar_routes" ("distance");
CREATE INDEX IF NOT EXISTS "astar_routes_geom_idx" ON "astar_routes" USING GIST ("path");
CREATE INDEX IF NOT EXISTS "mapf_routes_client_idx" ON "mapf_routes" ("client_id");
CREATE INDEX IF NOT EXISTS "mapf_routes_coords_idx" ON "mapf_routes" ("destination_lat", "destination_lon");

-- Consid/EV's indexes
CREATE INDEX IF NOT EXISTS idx_cons_nodes_map_xy ON consid_nodes (map_name, x, y);
CREATE INDEX IF NOT EXISTS idx_cons_nodes_zone ON consid_nodes (zone_id);
CREATE INDEX IF NOT EXISTS idx_cons_targets_type ON consid_node_targets (target_type);
CREATE INDEX IF NOT EXISTS idx_cons_targets_props_gin ON consid_node_targets USING GIN (target_props jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_cons_ticks_map_ingested ON consid_ticks (map_name, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_cons_charger_status_tick ON consid_charger_status (map_name, tick);
CREATE INDEX IF NOT EXISTS idx_cons_charger_speed_available ON consid_charger_status (speed_per_charger DESC, available DESC);
CREATE INDEX IF NOT EXISTS idx_cons_edges_fromnode ON consid_edges (map_name, from_node);
CREATE INDEX IF NOT EXISTS idx_cons_edges_tonode ON consid_edges (map_name, to_node);
CREATE INDEX IF NOT EXISTS idx_cons_ticks_map_tick ON consid_ticks (map_name, tick DESC);
CREATE INDEX IF NOT EXISTS idx_ev_traj_ev_scan ON ev_trajectories (map_name, ev_id, tick);
CREATE INDEX IF NOT EXISTS idx_ev_traj_node_lookup ON ev_trajectories (map_name, node_id, tick);
CREATE INDEX IF NOT EXISTS idx_ev_traj_state ON ev_trajectories (state);
CREATE INDEX IF NOT EXISTS idx_ev_traj_meta_gin ON ev_trajectories USING GIN (meta jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_ev_events_type ON ev_events (map_name, event_type, tick DESC);
CREATE INDEX IF NOT EXISTS idx_ev_events_node ON ev_events (map_name, node_id, tick DESC);
CREATE INDEX IF NOT EXISTS idx_transfer_edges_lookup ON transfer_edges (map_name, node_id);
CREATE INDEX IF NOT EXISTS "idx_cons_customers_map_persona" ON "consid_customers" ("map_name","persona");
CREATE INDEX IF NOT EXISTS "idx_cons_zone_logs_tick" ON "consid_zone_logs" ("map_name","tick");
CREATE INDEX IF NOT EXISTS "idx_cons_zone_logs_green" ON "consid_zone_logs" ((COALESCE("total_production",0) - COALESCE("total_demand",0)) DESC);

-- Views
CREATE OR REPLACE VIEW "view_astar_eta" AS
SELECT
    ar.client_id,
    ar.destination_lat,
    ar.destination_lon,
    ar.distance AS route_distance,
    gd.speed AS current_speed,
    ar.created_at,
    CASE
        WHEN gd.speed > 0 THEN ar.created_at + (ar.distance / gd.speed) * INTERVAL '1 second'
        ELSE NULL
        END AS estimated_eta
FROM astar_routes ar
         JOIN LATERAL (
    SELECT speed
    FROM geodata
    WHERE geodata.client_id = ar.client_id
    ORDER BY timestamp DESC
    LIMIT 1
    ) gd ON true;


CREATE OR REPLACE VIEW view_top_daily_poi AS
    SELECT DISTINCT ON ("client_id") *
    FROM "predicted_pois_sequence"
    WHERE "prediction_type" = 'daily'
    ORDER BY "client_id", "predicted_visit_time" ASC;


-- Combined POIs for routing
CREATE OR REPLACE VIEW view_combined_pois AS
SELECT
    p.client_id,
    p.lat,
    p.lon,
    p.poi_rank,
    NULL::FLOAT AS time_spent,
    p.geom,
    'stable' AS poi_type,
    p.created_at,
    NULL::timestamp AS predicted_visit_time
FROM pois p

UNION ALL

-- predicted sequence (daily/weekly)
SELECT
    s.client_id,
    s.predicted_lat  AS lat,
    s.predicted_lon  AS lon,
    0.5              AS poi_rank,
    NULL::FLOAT      AS time_spent,
    s.geom,
    ('predicted_' || s.prediction_type) AS poi_type,
    s.created_at,
    s.predicted_visit_time
FROM predicted_pois_sequence s;


CREATE OR REPLACE VIEW "view_hotspots_heatmap" AS
SELECT
    hotspot_id,
    client_id,
    lat,
    lon,
    radius,
    density,
    type,
    source_type,
    time_spent,
    created_at,
    updated_at,
    ST_AsGeoJSON(geom)::json AS geojson
FROM hotspots;


CREATE OR REPLACE VIEW "view_hotspot_overlay" AS
SELECT
    h.client_id,
    h.lat AS hotspot_lat,
    h.lon AS hotspot_lon,
    h.radius,
    h.density,
    h.source_type,
    s.stop_id,
    s.stop_name,
    s.stop_lat,
    s.stop_lon,
    ST_Distance(
            ST_SetSRID(ST_MakePoint(h.lon, h.lat), 4326),
            ST_SetSRID(ST_MakePoint(s.stop_lon, s.stop_lat), 4326)
    ) AS meters_to_stop
FROM hotspots h
         LEFT JOIN gtfs_stops s
                   ON ST_DWithin(
                           ST_SetSRID(ST_MakePoint(h.lon, h.lat), 4326),
                           ST_SetSRID(ST_MakePoint(s.stop_lon, s.stop_lat), 4326),
                           300
                      )
WHERE h.created_at >= NOW() - INTERVAL '24 hours';


CREATE OR REPLACE VIEW "view_latest_client_trajectories" AS
SELECT *
FROM (
         SELECT *,
                ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY created_at DESC) AS rn
         FROM trajectories
     ) sub
WHERE rn <= 8;


CREATE OR REPLACE VIEW "view_daily_routing_summary" AS
SELECT
    "client_id",
    "destination_lat",
    "destination_lon",
    "created_at",
    'MAPF' AS "method"
FROM "mapf_routes"
UNION
SELECT
    "client_id",
    "destination_lat",
    "destination_lon",
    "created_at",
    'A*' AS "method"
FROM "astar_routes";


CREATE OR REPLACE VIEW "view_mapf_active_routes" AS
SELECT *
FROM (
         SELECT
             mr.*,
             ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY created_at DESC) AS rn
         FROM mapf_routes mr
         WHERE mr.success = TRUE
           AND mr.created_at >= NOW() - INTERVAL '1 hour'
     ) sub
WHERE rn = 1;

-- Flat history with a 'source' tag
CREATE OR REPLACE VIEW view_routes_history AS
SELECT
    'optimized'::text AS source,
    client_id, stop_id, origin_lat, origin_lon,
    destination_lat, destination_lon, path,
    segment_type, is_chosen, created_at, NULL::text AS reason,
    NULL::text AS previous_stop_id, NULL::text AS previous_segment_type
FROM optimized_routes
UNION ALL
SELECT
    'reroute'::text AS source,
    client_id, stop_id, origin_lat, origin_lon,
    destination_lat, destination_lon, path,
    segment_type, is_chosen, created_at, reason,
    previous_stop_id, previous_segment_type
FROM reroutes;

-- “Latest view” per (client_id, stop_id, segment_type, destination)
-- Prefers the most recent record across both tables
CREATE OR REPLACE VIEW view_routes_unified_latest AS
SELECT *
FROM (
         SELECT
             h.*,
             ROW_NUMBER() OVER (
                 PARTITION BY client_id, COALESCE(stop_id,'∅'), segment_type,
                     destination_lat, destination_lon
                 ORDER BY created_at DESC
                 ) AS rn
         FROM view_routes_history h
     ) ranked
WHERE rn = 1;

-- “Live chosen per client” (one row per client, last decision)
CREATE OR REPLACE VIEW view_routes_live AS
SELECT *
FROM (
         SELECT
             h.*,
             ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY created_at DESC) AS rn
         FROM view_routes_history h
         WHERE is_chosen = TRUE
     ) x
WHERE rn = 1;

-- Optimized routes and re-routes
CREATE OR REPLACE VIEW view_routes_unified AS
SELECT
    client_id,
    stop_id,
    destination_lat,
    destination_lon,
    path,
    segment_type,
    created_at
FROM (
         SELECT
             r.*,
             ROW_NUMBER() OVER (
                 PARTITION BY client_id, destination_lat, destination_lon, COALESCE(segment_type,'')
                 ORDER BY created_at DESC
                 ) AS rn
         FROM (
                  SELECT client_id, stop_id, destination_lat, destination_lon, path, segment_type, created_at
                  FROM optimized_routes
                  WHERE is_valid = TRUE

                  UNION ALL

                  SELECT client_id, stop_id, destination_lat, destination_lon, path, segment_type, created_at
                  FROM reroutes
              ) r
     ) x
WHERE rn = 1;

-- POIs to GTFS stops (nearest + within radius)
CREATE OR REPLACE VIEW "view_pois_nearest_stop" AS
SELECT
    p."poi_id",
    p."client_id",
    p."lat",
    p."lon",
    p."geom"        AS poi_geom,
    p."poi_rank",
    p."time_spent",
    p."source",
    p."visit_start",
    p."visit_count",
    p."created_at"  AS poi_created_at,
    s."stop_id",
    s."stop_name",
    s."parent_station",
    s."platform_code",
    s."stop_lat",
    s."stop_lon",
    s."geom"        AS stop_geom,
    ST_DistanceSphere(p."geom", s."geom")::float AS meters_to_stop
FROM "pois" p
         LEFT JOIN LATERAL (
    SELECT
        gs."stop_id", gs."stop_name", gs."parent_station", gs."platform_code",
        gs."stop_lat", gs."stop_lon", gs."geom"
    FROM "gtfs_stops" gs
    ORDER BY p."geom" <-> gs."geom"   -- KNN, uses GiST on gtfs_stops.geom if present
    LIMIT 1
    ) s ON TRUE;


CREATE OR REPLACE VIEW "view_pois_stops_within_300m" AS
SELECT
    p."poi_id",
    p."client_id",
    p."lat",
    p."lon",
    p."geom"        AS poi_geom,
    p."poi_rank",
    p."time_spent",
    s."stop_id",
    s."stop_name",
    s."parent_station",
    s."platform_code",
    s."stop_lat",
    s."stop_lon",
    s."geom"        AS stop_geom,
    ST_DistanceSphere(p."geom", s."geom")::float AS meters_to_stop
FROM "pois" p
         JOIN "gtfs_stops" s
              ON ST_DWithin(p."geom"::geography, s."geom"::geography, 300);

-- Latest routes (optimized + reroutes) per client, like view_latest_client_trajectories
CREATE OR REPLACE VIEW "view_latest_client_routes" AS
SELECT *
FROM (
         SELECT
             h.*,
             ROW_NUMBER() OVER (PARTITION BY h."client_id" ORDER BY h."created_at" DESC) AS rn
         FROM "view_routes_history" h
     ) sub
WHERE rn <= 8;

-- A* and MAPF unified
CREATE OR REPLACE VIEW "view_routes_astar_mapf_unified" AS
SELECT
    ar."client_id",
    ar."stop_id",
    ar."destination_lat",
    ar."destination_lon",
    ar."path",
    ar."distance"::double precision          AS distance_meters,
    ar."decision_context",
    ar."predicted_eta",
    TRUE                                     AS success,
    'astar'::text                            AS method,
    ar."created_at"
FROM "astar_routes" ar

UNION ALL

SELECT
    mr."client_id",
    mr."stop_id",
    mr."destination_lat",
    mr."destination_lon",
    mr."path",
    mr."distance"::double precision          AS distance_meters,
    mr."decision_context",
    NULL::timestamp                          AS predicted_eta,
    mr."success",
    'mapf'::text                             AS method,
    mr."created_at"
FROM "mapf_routes" mr;

-- Latest (one) per client across A*+MAPF
CREATE OR REPLACE VIEW "view_routes_astar_mapf_latest" AS
SELECT *
FROM (
         SELECT
             u.*,
             ROW_NUMBER() OVER (PARTITION BY u."client_id" ORDER BY u."created_at" DESC) AS rn
         FROM "view_routes_astar_mapf_unified" u
     ) x
WHERE rn = 1;

-- ETA accuracy vs actual departure (error in seconds, + means ETA was early)
-- Matches A* predicted_eta with nearest departure at same stop within ±5 minutes.
CREATE OR REPLACE VIEW "view_eta_accuracy_seconds" AS
WITH eta_base AS (
    SELECT
        ar."client_id",
        ar."stop_id",
        ar."predicted_eta",
        ar."created_at" AS route_created_at
    FROM "astar_routes" ar
    WHERE ar."predicted_eta" IS NOT NULL
),
     nearest_departure AS (
         SELECT
             e."client_id",
             e."stop_id",
             e."predicted_eta",
             tu."departure_time",
             tu."trip_id",
             tu."delay_seconds",
             tu."status",
             ROW_NUMBER() OVER (
                 PARTITION BY e."client_id", e."stop_id", e."predicted_eta"
                 ORDER BY ABS(EXTRACT(EPOCH FROM (tu."departure_time" - e."predicted_eta"))) ASC
                 ) AS rn
         FROM eta_base e
                  JOIN "trip_updates" tu
                       ON tu."stop_id" = e."stop_id"
                           AND tu."departure_time" BETWEEN (e."predicted_eta" - INTERVAL '5 minutes')
                              AND (e."predicted_eta" + INTERVAL '5 minutes')
     )
SELECT
    n."client_id",
    n."stop_id",
    n."trip_id",
    n."predicted_eta",
    n."departure_time",
    COALESCE(n."delay_seconds", 0) AS delay_seconds,
    n."status",
    (EXTRACT(EPOCH FROM (n."departure_time" - n."predicted_eta"))::int) AS eta_error_seconds
FROM nearest_departure n
WHERE n.rn = 1;


-- Weekly plan joined with POI labels + nearest stop
CREATE OR REPLACE VIEW "view_client_weekly_schedule_enriched" AS
SELECT
    w."id",
    w."client_id",
    w."visit_day",
    w."predicted_time",
    w."prediction_type",
    w."poi_lat",
    w."poi_lon",
    w."path",
    w."segment_type",
    w."created_at",
    pn."stop_id"         AS nearest_stop_id,
    pn."stop_name"       AS nearest_stop_name,
    pn."meters_to_stop"  AS poi_to_stop_meters
FROM "client_weekly_schedule" w
         LEFT JOIN LATERAL (
    SELECT
        gs."stop_id",
        gs."stop_name",
        ST_DistanceSphere(
                ST_SetSRID(ST_MakePoint(w."poi_lon", w."poi_lat"), 4326),
                gs."geom"
        )::float AS meters_to_stop
    FROM "gtfs_stops" gs
    ORDER BY ST_SetSRID(ST_MakePoint(w."poi_lon", w."poi_lat"), 4326) <-> gs."geom"
    LIMIT 1
    ) pn ON TRUE;


-- Latest tick per map
CREATE OR REPLACE VIEW consid_latest_tick AS
    SELECT
        map_name, MAX(tick) AS tick
    FROM consid_ticks
GROUP BY map_name;

-- Chargers as routable POIs
CREATE OR REPLACE VIEW view_assets_chargers AS
    SELECT
        ('consid:' || n.map_name || ':' || n.node_id)   AS asset_id,
        'charging_station'                               AS asset_type,
        n.map_name,
        n.node_id,
        n.x, n.y,
        t.target_props                                   AS meta
    FROM consid_node_targets t
             JOIN consid_nodes n USING (map_name, node_id)
WHERE t.target_type = 'ChargingStation';

-- Ensure consistent quoting
DROP VIEW IF EXISTS "view_assets_chargers" CASCADE;
CREATE OR REPLACE VIEW "view_assets_chargers" AS
SELECT
    ('consid:' || n."map_name" || ':' || n."node_id") AS asset_id,
    'charging_station'::text                          AS asset_type,
    n."map_name",
    n."node_id",
    n."x",
    n."y",
    t."target_props"                                  AS meta
FROM "consid_node_targets" t
         JOIN "consid_nodes" n
              ON n."map_name" = t."map_name"
                  AND n."node_id"  = t."node_id"
WHERE t."target_type" = 'ChargingStation';

-- Unify EV ticks with LIVE human positions (from geodata latest)
DROP VIEW IF EXISTS "view_trajectories_unified" CASCADE;
CREATE OR REPLACE VIEW "view_trajectories_unified" AS
SELECT
    'ev'::text                       AS mode,
    et."map_name"                    AS map_name,
    et."ev_id"                       AS agent_id,
    et."tick"                        AS tick,
    et."node_id"                     AS node_id,
    et."x"::double precision         AS x,
    et."y"::double precision         AS y,
    et."soc_kwh"                     AS soc_kwh,
    et."battery_kwh"                 AS battery_kwh,
    et."state"                       AS state,
    et."customer_id"                 AS customer_id,
    et."meta"                        AS meta
FROM "ev_trajectories" et

UNION ALL

SELECT
    'human'::text                    AS mode,
    NULL::text                       AS map_name,
    g."client_id"                    AS agent_id,
    NULL::int                        AS tick,
    NULL::text                       AS node_id,
    g."lon"::double precision        AS x,
    g."lat"::double precision        AS y,
    NULL::numeric                    AS soc_kwh,
    NULL::numeric                    AS battery_kwh,
    g."activity"                     AS state,
    NULL::text                       AS customer_id,
    jsonb_build_object(
            'session_id', g."session_id",
            'speed',      g."speed",
            'timestamp',  g."timestamp"
    )                                AS meta
FROM "view_geodata_latest_point" g;


DROP VIEW IF EXISTS "consid_latest_charger_status" CASCADE;
CREATE VIEW "consid_latest_charger_status" AS
SELECT cs."map_name", cs."node_id",
       cs."available", cs."broken", cs."total", cs."speed_per_charger"
FROM "consid_charger_status" cs
         JOIN (
    SELECT "map_name", "node_id", MAX("tick") AS max_tick
    FROM "consid_charger_status"
    GROUP BY "map_name","node_id"
) m ON m."map_name" = cs."map_name" AND m."node_id" = cs."node_id" AND m."max_tick" = cs."tick";


DROP VIEW IF EXISTS "consid_greenest_zones_now" CASCADE;
CREATE VIEW "consid_greenest_zones_now" AS
WITH lt AS (
    SELECT "map_name", MAX("tick") AS tick
    FROM "consid_zone_logs"
    GROUP BY "map_name"
)
SELECT z."map_name", z."zone_id",
       zl."total_production", zl."total_demand",
       (COALESCE(zl."total_production",0) - COALESCE(zl."total_demand",0)) AS "surplus_kwh",
       zl."weather_type"
FROM lt
         JOIN "consid_zone_logs" zl
              ON zl."map_name" = lt."map_name" AND zl."tick" = lt."tick"
         JOIN "consid_zones" z
              ON z."map_name" = zl."map_name" AND z."zone_id" = zl."zone_id"
ORDER BY "surplus_kwh" DESC;


DROP VIEW IF EXISTS "view_ev_latest_state" CASCADE;
CREATE VIEW "view_ev_latest_state" AS
SELECT *
FROM (
         SELECT et.*,
                ROW_NUMBER() OVER (PARTITION BY et."map_name", et."ev_id" ORDER BY et."tick" DESC) AS rn
         FROM "ev_trajectories" et
     ) x
WHERE rn = 1;
