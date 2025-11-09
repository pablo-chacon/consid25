from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Tuple
from fastapi import FastAPI, Body, HTTPException, Query
from pydantic import BaseModel, Field
from db.db_connection import (
    upsert_map,
    upsert_tick,
    upsert_nodes_batch,
    upsert_targets_batch,
    upsert_chargers_batch,
    insert_edges_batch,
    upsert_ev_traj_batch,
)


logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ev_subscriber.main")
app = FastAPI(title="UrbanOS EV Subscriber", version="1.0.0")


# Pydantic models
class ConsidNode(BaseModel):
    id: str
    posX: int
    posY: int
    zoneId: Optional[str] = None
    target: Dict[str, Any] = Field(default_factory=dict)  # e.g., {"Type":"ChargingStation", ...}


class ConsidMap(BaseModel):
    name: str
    dimX: int
    dimY: int
    nodes: List[ConsidNode] = Field(default_factory=list)


class GameSnapshot(BaseModel):
    tick: Optional[int] = None
    map: Optional[ConsidMap] = None


class EVPoint(BaseModel):
    evId: str
    tick: int
    # Either provide nodeId OR x/y
    nodeId: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None

    socKwh: Optional[float] = None
    batteryKwh: Optional[float] = None
    state: Optional[str] = None          # driving|charging|waiting|idle|...
    customerId: Optional[str] = None
    tripIntentId: Optional[str] = None
    meta: Optional[dict] = None


class EVIngestPayload(BaseModel):
    mapName: str
    points: List[EVPoint]


class FullIngestPayload(BaseModel):
    snapshot: GameSnapshot
    ev: Optional[EVIngestPayload] = None


def _grid_neighbors(node_id: str, dimX: int, dimY: int) -> List[str]:
    xs, ys = node_id.split(".")
    x, y = int(xs), int(ys)
    out: List[str] = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < dimX and 0 <= ny < dimY:
            out.append(f"{nx}.{ny}")
    return out


def _node_from_xy(x: int, y: int) -> str:
    return f"{int(x)}.{int(y)}"


def _xy_from_node(node_id: str) -> Tuple[int, int]:
    xs, ys = node_id.split(".")
    return int(xs), int(ys)


def persist_map_and_tick(cmap: ConsidMap, tick: Optional[int]) -> None:
    """
    Upsert: map -> nodes -> targets -> (tick, chargers@tick)
    """
    upsert_map(cmap.name, int(cmap.dimX), int(cmap.dimY))

    node_rows = [(cmap.name, n.id, int(n.posX), int(n.posY), n.zoneId) for n in cmap.nodes]
    if node_rows:
        upsert_nodes_batch(node_rows)

    target_rows = []
    for n in cmap.nodes:
        tgt = n.target or {}
        ttype = str(tgt.get("Type", "Null"))
        target_rows.append((cmap.name, n.id, ttype, tgt))
    if target_rows:
        upsert_targets_batch(target_rows)

    if tick is not None:
        upsert_tick(cmap.name, int(tick))
        charger_rows = []
        for n in cmap.nodes:
            tgt = n.target or {}
            if str(tgt.get("Type")) == "ChargingStation":
                charger_rows.append((
                    cmap.name, int(tick), n.id,
                    tgt.get("amountOfAvailableChargers"),
                    tgt.get("totalAmountOfBrokenChargers"),
                    tgt.get("totalAmountOfChargers"),
                    tgt.get("chargeSpeedPerCharger"),
                ))
        if charger_rows:
            upsert_chargers_batch(charger_rows)


def maybe_generate_edges(cmap: ConsidMap, cost: float = 1.0) -> None:
    """
    Populate 4-neighbor grid edges (idempotent, ON CONFLICT DO NOTHING).
    """
    dimX, dimY = int(cmap.dimX), int(cmap.dimY)
    node_ids = {n.id for n in cmap.nodes}
    rows = []
    for nid in node_ids:
        for nbr in _grid_neighbors(nid, dimX, dimY):
            if nbr in node_ids:
                rows.append((cmap.name, nid, nbr, float(cost)))
    if rows:
        insert_edges_batch(rows)


def persist_ev_points(map_name: str, points: List[EVPoint]) -> int:
    """
    Batch UPSERT EV telemetry rows.
    Idempotent key: (map_name, ev_id, tick)
    """
    batch = []
    missing: List[str] = []

    for p in points:
        # Derive nodeId or x/y if one side missing
        if p.nodeId and (p.x is None or p.y is None):
            x, y = _xy_from_node(p.nodeId)
        elif (p.x is not None and p.y is not None) and not p.nodeId:
            p.nodeId = _node_from_xy(p.x, p.y)
            x, y = int(p.x), int(p.y)
        elif p.nodeId and (p.x is not None and p.y is not None):
            x, y = int(p.x), int(p.y)  # trust payload
        else:
            missing.append(f"{p.evId}@{p.tick}")
            continue

        batch.append((
            map_name, p.evId, int(p.tick),
            p.nodeId, int(x), int(y),
            p.socKwh, p.batteryKwh, p.state, p.customerId, p.tripIntentId, p.meta or {}
        ))

    if batch:
        upsert_ev_traj_batch(batch)

    if missing:
        log.warning(f"Skipped EV rows missing nodeId/x,y -> {missing}")

    return len(batch)


# API
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ingest")
def ingest_snapshot(
        payload: GameSnapshot = Body(...),
        generate_edges: bool = Query(default=True, description="Generate 4-neighbor edges"),
):
    """
    Ingest Consid map snapshot:
      - consid_maps, consid_nodes, consid_node_targets
      - consid_ticks (if tick present)
      - consid_charger_status (if ChargingStation targets and tick present)
      - consid_edges (optional 4-neighbor)
    """
    if payload.map is None:
        raise HTTPException(status_code=400, detail="No map found in payload")

    try:
        persist_map_and_tick(payload.map, payload.tick)
        if generate_edges:
            maybe_generate_edges(payload.map, cost=1.0)
        return {"status": "ok", "map": payload.map.name, "tick": payload.tick}
    except Exception as e:
        log.exception("ingest error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/ev")
def ingest_ev(payload: EVIngestPayload = Body(...)):
    """
    Ingest EV telemetry rows for a map.
    """
    if not payload.points:
        raise HTTPException(status_code=400, detail="No points provided")
    try:
        n = persist_ev_points(payload.mapName, payload.points)
        return {"status": "ok", "ingested": n}
    except Exception as e:
        log.exception("ingest_ev error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ingest/full")
def ingest_full(payload: FullIngestPayload = Body(...), generate_edges: bool = True):
    """
    Ingest map/tick and EV rows in one call.
    """
    if payload.snapshot is None or payload.snapshot.map is None:
        raise HTTPException(status_code=400, detail="snapshot.map is required")

    try:
        persist_map_and_tick(payload.snapshot.map, payload.snapshot.tick)
        if generate_edges:
            maybe_generate_edges(payload.snapshot.map, cost=1.0)

        ev_count = 0
        if payload.ev and payload.ev.points:
            ev_count = persist_ev_points(payload.ev.mapName, payload.ev.points)

        return {
            "status": "ok",
            "map": payload.snapshot.map.name,
            "tick": payload.snapshot.tick,
            "ev_ingested": ev_count,
        }
    except Exception as e:
        log.exception("ingest_full error")
        raise HTTPException(status_code=500, detail=str(e))
