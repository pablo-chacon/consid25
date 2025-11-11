from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import heapq
import math

@dataclass
class StationInfo:
    node_id: str
    free: int
    broken: int
    total: int
    speed_kw: float

@dataclass
class CustomerState:
    id: str
    persona: str
    from_node: str
    to_node: str
    charge_remaining: float
    max_charge: float
    consumption_per_km: float
    state: str
    departure_tick: Optional[int] = None

class FlowAwarePlanner:
    def __init__(self, map_obj: dict):
        self.map = map_obj
        self.nodes = {n["id"]: n for n in map_obj["nodes"]}
        self.edges = map_obj["edges"]
        self.adj = self._build_graph(self.edges)
        self.station_by_node = self._extract_stations(self.nodes)
        # time-expanded charger reservations: nodeId -> { tick: used_slots }
        self.reservations: Dict[str, Dict[int, int]] = {}

    def _build_graph(self, edges) -> Dict[str, List[Tuple[str, float, int]]]:
        adj = {}
        for e in edges:
            a = e["fromNode"]; b = e["toNode"]; L = float(e["length"])
            load = len(e.get("customers", []))  # live congestion signal
            adj.setdefault(a, []).append((b, L, load))
            adj.setdefault(b, []).append((a, L, load))  # undirected
        return adj

    def _extract_stations(self, nodes) -> Dict[str, StationInfo]:
        out = {}
        for nid, n in nodes.items():
            tgt = n.get("target")
            if tgt and tgt.get("Type") == "ChargingStation":
                out[nid] = StationInfo(
                    node_id=nid,
                    free=int(tgt.get("amountOfAvailableChargers", 0)),
                    broken=int(tgt.get("totalAmountOfBrokenChargers", 0)),
                    total=int(tgt.get("totalAmountOfChargers", 0)),
                    speed_kw=float(tgt.get("chargeSpeedPerCharger", 0.0)),
                )
        return out

    def _h(self, a: str, b: str) -> float:
        na, nb = self.nodes[a], self.nodes[b]
        dx = na["posX"] - nb["posX"]; dy = na["posY"] - nb["posY"]
        return math.hypot(dx, dy)

    def _astar(self, start: str, goal: str, kappa: float = 0.2) -> Tuple[List[str], float]:
        # edge cost = length * (1 + kappa * normalized_load)
        # normalize load by a small constant to keep cost bounded
        LOAD_SCALE = 5.0
        openq = [(0.0, start)]
        g = {start: 0.0}; came = {}
        while openq:
            f, u = heapq.heappop(openq)
            if u == goal: break
            for v, L, load in self.adj.get(u, []):
                c = L * (1.0 + kappa * (load / LOAD_SCALE))
                ng = g[u] + c
                if ng < g.get(v, float("inf")):
                    g[v] = ng
                    came[v] = u
                    heapq.heappush(openq, (ng + self._h(v, goal), v))
        if goal not in came and start != goal:  # disconnected
            return [start], float("inf")
        # rebuild
        path = [goal]
        while path[-1] != start:
            path.append(came[path[-1]])
        path.reverse()
        return path, g.get(goal, 0.0)

    def _predict_arrival_tick(self, dist_cost: float, base_speed_kmh: float = 40.0) -> int:
        # map ticks = 5 minutes each → 12 ticks/hour
        hours = dist_cost / max(base_speed_kmh, 1.0)
        ticks = int(round(hours * 12))
        return max(1, ticks)

    def _green_bonus(self, tick: int) -> float:
        # crude but aligned with docs: solar 06:00–18:00 → ticks 72..216
        return 1.0 if 72 <= tick % 288 <= 216 else 0.0

    def _reserve(self, node_id: str, start_tick: int, duration_ticks: int, capacity: int) -> int:
        """Find earliest tick >= start_tick with free capacity, reserve it, return start."""
        r = self.reservations.setdefault(node_id, {})
        t = start_tick
        while True:
            used = r.get(t, 0)
            if used < capacity:
                r[t] = used + 1
                return t
            t += 1

    def _charge_duration_ticks(self, needed_kwh: float, speed_kw: float) -> int:
        # 1 tick = 5 minutes = 1/12 hour; energy delivered per tick = speed_kw / 12
        if speed_kw <= 0: return 9999
        ticks = math.ceil(needed_kwh * 12.0 / speed_kw)
        return max(1, ticks)

    def _plan_single(self, cust: CustomerState, tick: int):
        # Safety margins
        soc = max(0.0, float(cust.charge_remaining))
        cap = float(cust.max_charge)
        cons = max(1e-6, float(cust.consumption_per_km))

        # Always ensure at least one charge before arrival (scoring)
        path, dist_cost = self._astar(cust.from_node, cust.to_node)
        km_per_cost = 1.0  # engine uses edge length modifier internally; cost~km
        km = dist_cost * km_per_cost
        kwh_needed = km * cons
        kwh_left = soc * cap

        must_charge = (kwh_left < kwh_needed * 1.10)  # 10% buffer

        # Candidate: pick the best station on/near the A* path
        best = None
        for nid in path:
            if nid in self.station_by_node:
                st = self.station_by_node[nid]
                # persona & grid heuristics
                green = self._green_bonus(tick)
                queue_pressure = max(0, st.total - st.free) / max(1, st.total)
                # utility: favor green when eco, favor lower queue, favor on-path
                persona = (cust.persona or "Neutral").lower()
                w_green = 1.0 if "eco" in persona else 0.3
                w_queue = 1.0 if "stressed" in persona else 0.6
                u = (w_green * green) - (w_queue * queue_pressure)
                best = max(best or (-9e9, None), (u, nid))

        if not best:
            return None  # no station on this path; engine will still move

        _, station_node = best
        st = self.station_by_node[station_node]

        # How much to charge? Enough to safely reach dest (or next leg).
        need_kwh = max(0.0, kwh_needed - kwh_left)
        charge_to = min(1.0, soc + (need_kwh / cap) + 0.05)  # slight overfill

        # Reservation: spread if capacity tight
        t_arrive = tick + self._predict_arrival_tick(dist_cost)
        start_at = self._reserve(station_node, t_arrive, self._charge_duration_ticks(need_kwh, st.speed_kw), st.free)

        # We can encode only nodeId + chargeTo; engine handles the timing.
        return {
            "customerId": cust.id,
            "chargingRecommendations": [
                {"nodeId": station_node, "chargeTo": round(charge_to, 3)}
            ]
        }

    def recommendations_for_tick(self, tick: int) -> List[dict]:
        recs: List[dict] = []
        for n in self.map["nodes"]:
            for c in n.get("customers", []):
                state = c.get("state")
                if state in ("DestinationReached", "FailedToCharge", "RanOutOfJuice", "Charging", "WaitingForCharger"):
                    continue
                cs = CustomerState(
                    id=c["id"],
                    persona=c.get("persona", "Neutral"),
                    from_node=c.get("fromNode") or n["id"],
                    to_node=c.get("toNode"),
                    charge_remaining=c.get("chargeRemaining", 0.0),
                    max_charge=c.get("maxCharge", 1.0),
                    consumption_per_km=c.get("energyConsumptionPerKm", 0.15),
                    state=state,
                    departure_tick=c.get("departureTick"),
                )
                rec = self._plan_single(cs, tick)
                if rec:
                    recs.append(rec)
        return recs
