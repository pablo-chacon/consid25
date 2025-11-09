from __future__ import annotations
import os
import time
import logging
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from dotenv import load_dotenv

# UOS logging + env
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ev_subscriber.db")
load_dotenv()

# Persistent connection
_db_conn: Optional[psycopg.Connection] = None


def get_db_connection(retries: int = 5, delay: int = 5) -> psycopg.Connection:
    """
    UrbanOS-style persistent PostgreSQL connection with retry logic.
    - Uses .env: POSTGRES_DB/USER/PASSWORD/HOST/PORT
    - autocommit=True
    - row_factory=dict_row
    """
    global _db_conn
    if _db_conn is not None and not _db_conn.closed:
        return _db_conn

    dbname = os.getenv("POSTGRES_DB")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")

    for attempt in range(1, retries + 1):
        try:
            _db_conn = psycopg.connect(
                dbname=dbname,
                user=user,
                password=password,
                host=host,
                port=port,
                autocommit=True,
                row_factory=dict_row,
            )
            log.info("Connected to PostgreSQL.")
            return _db_conn
        except psycopg.OperationalError as e:
            log.error(f"DB connect attempt {attempt}/{retries} failed: {e}")
            time.sleep(delay)

    raise RuntimeError("Failed to connect to the database after multiple attempts")


# ---------- SQL (UPSERTS) ----------
_SQL_UPSERT_MAP = """
INSERT INTO consid_maps (map_name, dim_x, dim_y)
VALUES (%s, %s, %s)
ON CONFLICT (map_name) DO UPDATE
SET dim_x = EXCLUDED.dim_x,
    dim_y = EXCLUDED.dim_y;
"""

_SQL_UPSERT_TICK = """
INSERT INTO consid_ticks (map_name, tick)
VALUES (%s, %s)
ON CONFLICT (map_name, tick) DO NOTHING;
"""

_SQL_UPSERT_NODE = """
INSERT INTO consid_nodes (map_name, node_id, x, y, zone_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (map_name, node_id) DO UPDATE
SET x = EXCLUDED.x,
    y = EXCLUDED.y,
    zone_id = EXCLUDED.zone_id;
"""

_SQL_UPSERT_TARGET = """
INSERT INTO consid_node_targets (map_name, node_id, target_type, target_props)
VALUES (%s, %s, %s, %s::jsonb)
ON CONFLICT (map_name, node_id) DO UPDATE
SET target_type = EXCLUDED.target_type,
    target_props = EXCLUDED.target_props;
"""

_SQL_UPSERT_CHARGER = """
INSERT INTO consid_charger_status (map_name, tick, node_id, available, broken, total, speed_per_charger)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (map_name, tick, node_id) DO UPDATE
SET available = EXCLUDED.available,
    broken = EXCLUDED.broken,
    total = EXCLUDED.total,
    speed_per_charger = EXCLUDED.speed_per_charger;
"""

_SQL_INSERT_EDGE = """
INSERT INTO consid_edges (map_name, from_node, to_node, cost)
VALUES (%s, %s, %s, %s)
ON CONFLICT DO NOTHING;
"""

# --- NEW: EV trajectories UPSERT ---
_SQL_UPSERT_EV_TRAJ = """
INSERT INTO ev_trajectories (
  map_name, ev_id, tick, node_id, x, y,
  soc_kwh, battery_kwh, state, customer_id, trip_intent_id, meta
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (map_name, ev_id, tick) DO UPDATE SET
  node_id        = EXCLUDED.node_id,
  x              = EXCLUDED.x,
  y              = EXCLUDED.y,
  soc_kwh        = EXCLUDED.soc_kwh,
  battery_kwh    = EXCLUDED.battery_kwh,
  state          = EXCLUDED.state,
  customer_id    = EXCLUDED.customer_id,
  trip_intent_id = EXCLUDED.trip_intent_id,
  meta           = EXCLUDED.meta;
"""


# ---------- Single-row UPSERTs ----------
def upsert_map(map_name: str, dim_x: int, dim_y: int) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(_SQL_UPSERT_MAP, (map_name, dim_x, dim_y))
    except Exception as e:
        log.error(f"upsert_map: {e}")


def upsert_tick(map_name: str, tick: int) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(_SQL_UPSERT_TICK, (map_name, tick))
    except Exception as e:
        log.error(f"upsert_tick: {e}")


def upsert_node(
        map_name: str,
        node_id: str,
        x: int,
        y: int,
        zone_id: Optional[str] = None,
) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(_SQL_UPSERT_NODE, (map_name, node_id, x, y, zone_id))
    except Exception as e:
        log.error(f"upsert_node: {e}")


def upsert_target(
        map_name: str,
        node_id: str,
        target_type: str,
        target_props: Dict[str, Any],
) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(_SQL_UPSERT_TARGET, (map_name, node_id, target_type, Json(target_props)))
    except Exception as e:
        log.error(f"upsert_target: {e}")


def upsert_charger_status(
        map_name: str,
        tick: int,
        node_id: str,
        available: Optional[int],
        broken: Optional[int],
        total: Optional[int],
        speed_per_charger: Optional[int],
) -> None:
    try:
        a = available if available is not None else 0
        b = broken if broken is not None else 0
        t = total if total is not None else 0
        s = speed_per_charger if speed_per_charger is not None else 0

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(_SQL_UPSERT_CHARGER, (map_name, tick, node_id, a, b, t, s))
    except Exception as e:
        log.error(f"upsert_charger_status: {e}")


def upsert_ev_traj(
        map_name: str,
        ev_id: str,
        tick: int,
        node_id: str,
        x: int,
        y: int,
        soc_kwh: Optional[float] = None,
        battery_kwh: Optional[float] = None,
        state: Optional[str] = None,
        customer_id: Optional[str] = None,
        trip_intent_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Idempotent per (map_name, ev_id, tick).
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                _SQL_UPSERT_EV_TRAJ,
                (map_name, ev_id, tick, node_id, x, y,
                 soc_kwh, battery_kwh, state, customer_id, trip_intent_id, Json(meta or {}))
            )
    except Exception as e:
        log.error(f"upsert_ev_traj: {e}")


# ---------- Batch helpers ----------
def upsert_nodes_batch(
        rows: Iterable[Tuple[str, str, int, int, Optional[str]]],
) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(_SQL_UPSERT_NODE, rows)
    except Exception as e:
        log.error(f"upsert_nodes_batch: {e}")


def upsert_targets_batch(
        rows: Iterable[Tuple[str, str, str, Dict[str, Any]]],
) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(
                _SQL_UPSERT_TARGET,
                ((m, n, t, Json(p)) for (m, n, t, p) in rows),
            )
    except Exception as e:
        log.error(f"upsert_targets_batch: {e}")


def upsert_chargers_batch(
        rows: Iterable[Tuple[str, int, str, Optional[int], Optional[int], Optional[int], Optional[int]]],
) -> None:
    def _n(v: Optional[int]) -> int:  # normalize NULL->0
        return v if v is not None else 0

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(
                _SQL_UPSERT_CHARGER,
                ((m, t, n, _n(a), _n(b), _n(ttl), _n(spd)) for (m, t, n, a, b, ttl, spd) in rows),
            )
    except Exception as e:
        log.error(f"upsert_chargers_batch: {e}")


def insert_edges_batch(
        rows: Iterable[Tuple[str, str, str, float]],
) -> None:
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(_SQL_INSERT_EDGE, rows)
    except Exception as e:
        log.error(f"insert_edges_batch: {e}")


def upsert_ev_traj_batch(
        rows: Iterable[Tuple[str, str, int, str, int, int, Optional[float], Optional[float], Optional[str], Optional[str], Optional[str], Optional[Dict[str, Any]]]],
) -> None:
    """
    rows: (map_name, ev_id, tick, node_id, x, y, soc_kwh, battery_kwh, state, customer_id, trip_intent_id, meta_dict)
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.executemany(
                _SQL_UPSERT_EV_TRAJ,
                (
                    (m, e, t, n, x, y, sk, bk, st, cid, tid, Json(md or {}))
                    for (m, e, t, n, x, y, sk, bk, st, cid, tid, md) in rows
                ),
            )
    except Exception as e:
        log.error(f"upsert_ev_traj_batch: {e}")


# ---------- Convenience reads ----------
def fetch_latest_tick(map_name: str) -> Optional[int]:
    """
    Return latest ingested tick for a map, or None.
    """
    sql = "SELECT MAX(tick) AS tick FROM consid_ticks WHERE map_name = %s"
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(sql, (map_name,))
            row = cur.fetchone()
            return int(row["tick"]) if row and row["tick"] is not None else None
    except Exception as e:
        log.error(f"fetch_latest_tick: {e}")
        return None


def charger_candidates_for_tick(
        map_name: str,
        tick: int,
        limit: int = 1,
) -> Sequence[Dict[str, Any]]:
    """
    Candidate chargers for a tick ordered by speed desc, available desc.
    Returns dict rows: {node_id, speed_per_charger, available}
    """
    sql = """
    SELECT c.node_id,
           s.speed_per_charger,
           s.available
    FROM consid_node_targets t
    JOIN consid_charger_status s
      ON s.map_name = t.map_name
     AND s.node_id  = t.node_id
     AND s.tick     = %s
    JOIN consid_nodes c
      ON c.map_name = t.map_name
     AND c.node_id  = t.node_id
    WHERE t.map_name = %s
      AND t.target_type = 'ChargingStation'
      AND s.speed_per_charger > 0
    ORDER BY s.speed_per_charger DESC, s.available DESC
    LIMIT %s;
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(sql, (tick, map_name, limit))
            return cur.fetchall() or []
    except Exception as e:
        log.error(f"charger_candidates_for_tick: {e}")
        return []
