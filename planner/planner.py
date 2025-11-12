from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set
import heapq
import math

"""
    - update_map() (must be called each tick before planning).
    - Cooperative A* _ca_star() with edge-time reservations (_reserve_edge*).
    - Charger reservation via _reserve_charger().
    - Persona-weighted choice of on-path station and green-window bias.
    - Reservations persist across ticks keeps fleet deconflicted in time/space.
"""


# Data classes
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


# Planner
class FlowAwarePlanner:
    """
    Flow-aware planner that combines:
      - A* / CA* (time-expanded) to shape corridors and avoid conflicts (MAPF-lite)
      - Charger time-slot reservations to prevent station stampedes
      - Persona-aware utility (green bonus vs queue pressure)
    We only emit charging waypoints (nodeId, chargeTo). The engine drives the vehicles.
    """

    def __init__(self, map_obj: dict):
        self.map = map_obj
        self.nodes: Dict[str, dict] = {n["id"]: n for n in map_obj["nodes"]}
        self.edges: List[dict] = map_obj["edges"]
        self.adj: Dict[str, List[Tuple[str, float, int]]] = self._build_graph(self.edges)
        self.station_by_node: Dict[str, StationInfo] = self._extract_stations(self.nodes)
        self.charged_once: Dict[str, bool] = {}  # customerId -> True when recommended charge

        # MAPF reservation ledgers
        self.edge_reservations: Dict[Tuple[str, str], Set[int]] = {}  # (min(u,v), max(u,v)) -> ticks occupied
        self.node_reservations: Dict[str, Dict[int, int]] = {}  # nodeId -> { tick: used_slots } for chargers

        # Tunables
        self.AVG_SPEED_KMH = 40.0  # converts distances -> ticks
        self.LOAD_SCALE = 5.0  # dampens edge load impact
        self.CONGESTION_WEIGHT = 0.20  # kappa in edge cost = L * (1 + kappa * load/LOAD_SCALE)
        self.SOC_BUFFER = 0.10  # 10% safety margin
        self.VISIT_LIMIT = 20000  # CA* expansion cap

    # Lifecycle
    def update_map(self, map_obj: dict):
        """Refresh map snapshot each tick (keeps reservations)."""
        self.map = map_obj
        self.nodes = {n["id"]: n for n in map_obj["nodes"]}
        self.edges = map_obj["edges"]
        self.adj = self._build_graph(self.edges)
        self.station_by_node = self._extract_stations(self.nodes)

    # Graph builders
    def _build_graph(self, edges) -> Dict[str, List[Tuple[str, float, int]]]:
        adj: Dict[str, List[Tuple[str, float, int]]] = {}
        for e in edges:
            a = e["fromNode"];
            b = e["toNode"]
            L = float(e["length"])
            load = len(e.get("customers", []))  # live congestion
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

    # geometry / heuristics
    def _h(self, a: str, b: str) -> float:
        na, nb = self.nodes[a], self.nodes[b]
        dx = na["posX"] - nb["posX"]
        dy = na["posY"] - nb["posY"]
        return math.hypot(dx, dy)

    def _green_bonus(self, tick: int) -> float:
        # solar 06:00–18:00 → ticks 72..216 in a 24h (288-tick) day
        return 1.0 if 72 <= (tick % 288) <= 216 else 0.0

    # travel timing
    def _travel_ticks(self, u: str, v: str, base_speed_kmh: float = None) -> int:
        """Ticks (5 min) to traverse edge (u,v)."""
        if base_speed_kmh is None:
            base_speed_kmh = self.AVG_SPEED_KMH
        L = None
        for w, Lw, _load in self.adj.get(u, []):
            if w == v:
                L = Lw;
                break
        if L is None:
            return 9999
        hours = float(L) / max(base_speed_kmh, 1.0)
        return max(1, int(round(hours * 12.0)))  # 12 ticks/hour

    # edge reservations (MAPF)
    def _is_edge_reserved(self, u: str, v: str, t0: int, t1: int) -> bool:
        a, b = (u, v) if u < v else (v, u)
        occ = self.edge_reservations.get((a, b))
        if not occ: return False
        for t in range(t0, t1):
            if t in occ:
                return True
        return False

    def _reserve_edge(self, u: str, v: str, t0: int, t1: int):
        a, b = (u, v) if u < v else (v, u)
        occ = self.edge_reservations.setdefault((a, b), set())
        for t in range(t0, t1):
            occ.add(t)

    def _reserve_edge_sequence(self, node_tick_path: List[Tuple[str, int]], until_node: str):
        """Reserve edges along (n_i,t_i)->(n_{i+1},t_{i+1}) up to `until_node` (inclusive)."""
        for i in range(len(node_tick_path) - 1):
            (u, t0), (v, t1) = node_tick_path[i], node_tick_path[i + 1]
            self._reserve_edge(u, v, t0, t1)
            if v == until_node:
                break

    # charger reservations
    def _reserve_charger(self, node_id: str, start_tick: int, duration_ticks: int, capacity: int) -> int:
        # Find the earliest tick >= start_tick with free charger slot, reserve it, return start tick
        r = self.node_reservations.setdefault(node_id, {})
        t = start_tick
        while True:
            used = r.get(t, 0)
            if used < max(1, capacity):
                r[t] = used + 1
                return t
            t += 1

    def _charge_duration_ticks(self, needed_kwh: float, speed_kw: float) -> int:
        # 1 tick = 5 minutes = 1/12 hour; energy delivered per tick = speed_kw / 12
        if speed_kw <= 0:
            return 9999
        ticks = math.ceil((needed_kwh * 12.0) / speed_kw)
        return max(1, ticks)

    # Classic A* (kept as fallback / estimator)
    def _astar(self, start: str, goal: str, kappa: float = None) -> Tuple[List[str], float]:
        """Static A* (no time). Returns (path, distance-like cost)."""
        if kappa is None:
            kappa = self.CONGESTION_WEIGHT
        openq: List[Tuple[float, str]] = [(0.0, start)]
        g = {start: 0.0};
        came: Dict[str, str] = {}
        while openq:
            f, u = heapq.heappop(openq)
            if u == goal:
                break
            for v, L, load in self.adj.get(u, []):
                c = L * (1.0 + kappa * (load / self.LOAD_SCALE))
                ng = g[u] + c
                if ng < g.get(v, float("inf")):
                    g[v] = ng
                    came[v] = u
                    heapq.heappush(openq, (ng + self._h(v, goal), v))
        if start != goal and goal not in came:
            return [start], float("inf")
        path = [goal]
        while path[-1] != start:
            path.append(came[path[-1]])
        path.reverse()
        return path, g.get(goal, 0.0)

    # Cooperative A* (time-expanded)
    def _ca_star(self, start: str, goal: str, start_tick: int, kappa: float = None) -> List[Tuple[str, int]]:
        """Return a (node, tick) path with time-aware reservations (MAPF-lite)."""
        if kappa is None:
            kappa = self.CONGESTION_WEIGHT

        def h_ticks(n: str) -> float:
            # Heuristic: euclid distance / speed -> hours -> ticks
            hours = self._h(n, goal) / max(self.AVG_SPEED_KMH, 1.0)
            return hours * 12.0

        start_state = (start, start_tick)
        g: Dict[Tuple[str, int], float] = {start_state: 0.0}
        came: Dict[Tuple[str, int], Tuple[str, int]] = {}
        pq: List[Tuple[float, Tuple[str, int]]] = [(h_ticks(start), start_state)]
        visits = 0

        while pq and visits < self.VISIT_LIMIT:
            f, (u, t) = heapq.heappop(pq);
            visits += 1
            if u == goal:
                path = [(u, t)]
                cur = (u, t)
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                path.reverse()
                return path

            for v, L, load in self.adj.get(u, []):
                dt = self._travel_ticks(u, v, base_speed_kmh=self.AVG_SPEED_KMH)
                t_next = t + dt

                # Hard avoidance: if edge-time is reserved, skip
                if self._is_edge_reserved(u, v, t, t_next):
                    continue

                # Soft congestion: bump by live load
                edge_cost = L * (1.0 + kappa * (load / self.LOAD_SCALE))

                ng = g[(u, t)] + edge_cost
                state_v = (v, t_next)
                if ng < g.get(state_v, float("inf")):
                    g[state_v] = ng
                    came[state_v] = (u, t)
                    heapq.heappush(pq, (ng + h_ticks(v), state_v))

        # Fallback: no plan found within visit cap
        return [(start, start_tick)]

    # Per-customer planning
    def _plan_single(self, cust: CustomerState, tick: int):
        # ---------- inputs / safety ----------
        soc = max(0.0, float(cust.charge_remaining))
        cap = float(cust.max_charge)
        cons = max(1e-6, float(cust.consumption_per_km))

        # persona-aware buffer
        persona = (cust.persona or "Neutral").lower()
        soc_buffer = self.SOC_BUFFER
        if "stressed" in persona or "dislikes" in persona:
            soc_buffer = max(soc_buffer, 0.12)
        elif "eco" in persona:
            soc_buffer = 0.08

        # ---------- CA* path to destination (we'll reserve only up to the chosen station) ----------
        node_tick_path = self._ca_star(cust.from_node, cust.to_node, tick)
        nodes_seq = [n for (n, _t) in node_tick_path]

        # If CA* failed to move, bail
        if len(nodes_seq) == 1 and nodes_seq[0] == cust.from_node:
            return None

        # Total path distance (sum edge lengths)
        dist_cost = 0.0
        for i in range(len(nodes_seq) - 1):
            u, v = nodes_seq[i], nodes_seq[i + 1]
            L = next((L for w, L, _ in self.adj.get(u, []) if w == v), 0.0)
            dist_cost += L

        # Energy needs
        kwh_needed_total = dist_cost * cons
        kwh_left = soc * cap
        must_charge = (kwh_left < kwh_needed_total * (1.0 + soc_buffer))

        # Cumulative distance to each node along CA* path
        cum_d: Dict[str, float] = {nodes_seq[0]: 0.0}
        for i in range(1, len(nodes_seq)):
            u, v = nodes_seq[i - 1], nodes_seq[i]
            L = next((L for w, L, _ in self.adj.get(u, []) if w == v), 0.0)
            cum_d[v] = cum_d[u] + L

        # Micro top-up rule: secure completion with minimal time
        # If customer hasn’t charged in the last 20% of route,
        # grab a top-up (5–8% of capacity) at first reachable on-path station.
        charged_before = self.charged_once.get(cust.id, False)
        if not charged_before and dist_cost > 0:
            # Remaining fraction <= 0.20 + reachable station quick charge.
            for nid in nodes_seq:
                if nid not in self.station_by_node:
                    continue
                # remaining distance fraction from node
                rem_km = max(0.0, dist_cost - cum_d.get(nid, 0.0))
                if rem_km / max(dist_cost, 1e-6) > 0.20:
                    continue  # not yet in the last 20%
                # arrival tick at nid
                t_arrive = next((t_ for n_, t_ in node_tick_path if n_ == nid), None)
                if t_arrive is None:
                    continue
                # reachable with current energy?
                km_to_station = cum_d.get(nid, 0.0)
                if kwh_left < km_to_station * cons * 1.05:
                    continue
                st = self.station_by_node[nid]
                # reserve edges up to the station
                self._reserve_edge_sequence(node_tick_path, nid)
                # tiny top-up (5–8% of capacity)
                tiny_kwh = max(0.05 * cap, 0.08 * cap)
                charge_ticks = self._charge_duration_ticks(tiny_kwh, st.speed_kw)
                _ = self._reserve_charger(nid, t_arrive, charge_ticks, st.free)
                charge_to = min(1.0, (kwh_left + tiny_kwh) / cap)
                self.charged_once[cust.id] = True
                return {
                    "customerId": cust.id,
                    "chargingRecommendations": [{"nodeId": nid, "chargeTo": round(charge_to, 3)}]
                }

        # Station selection on CA* corridor
        # persona weights (scoring)
        w_green = 1.0 if "eco" in persona else 0.3
        w_queue = 1.2 if "stressed" in persona else 0.6  # stressed dislikes queues more
        w_speed = 0.5
        w_wait = 0.8

        best_station = None  # (node_id, StationInfo, t_arrive, need_kwh, score)

        for nid in nodes_seq:
            st = self.station_by_node.get(nid)
            if not st:
                continue

            # arrival tick at this node from CA* path
            t_arrive = next((t_ for n_, t_ in node_tick_path if n_ == nid), None)
            if t_arrive is None:
                continue

            # energy to reach station
            km_to_station = cum_d.get(nid, 0.0)
            kwh_to_station = km_to_station * cons
            if kwh_left < kwh_to_station * (1.0 + 0.05):
                continue  # can't reach safely

            # energy after station to destination
            km_after = max(0.0, dist_cost - km_to_station)
            kwh_after = km_after * cons
            kwh_rem_after_station = max(0.0, kwh_left - kwh_to_station)
            need_kwh = max(0.0, kwh_after - kwh_rem_after_station)

            # scoring terms
            green = self._green_bonus(t_arrive)

            # predicted wait: node reservations at/after arrival
            res = self.node_reservations.get(nid, {})
            pred_wait = 0
            while res.get(t_arrive + pred_wait, 0) >= max(1, st.free):
                pred_wait += 1  # ticks until a free slot

            queue_pressure = 0.0
            if st.total > 0:
                queue_pressure = max(0, st.total - st.free) / float(st.total)

            speed_benefit = math.log1p(max(st.speed_kw, 0.0))  # diminishing returns

            score = (w_green * green) + (w_speed * speed_benefit) \
                    - (w_queue * queue_pressure) - (w_wait * pred_wait)

            if (best_station is None) or (score > best_station[-1]):
                best_station = (nid, st, t_arrive, need_kwh, score)

        # Fallback if no station scored positive
        if not best_station:
            if not must_charge:
                return None
            # Pick first reachable station on path ignoring score
            fallback = None
            for nid in nodes_seq:
                st = self.station_by_node.get(nid)
                if not st:
                    continue
                t_arrive = next((t_ for n_, t_ in node_tick_path if n_ == nid), None)
                if t_arrive is None:
                    continue
                km_to_station = cum_d.get(nid, 0.0)
                if kwh_left >= km_to_station * cons * (1.0 + 0.05):
                    need_kwh = max(0.0, kwh_needed_total - kwh_left)
                    fallback = (nid, st, t_arrive, need_kwh, 0.0)
                    break
            if not fallback:
                return None
            best_station = fallback

        # Commit reservations & compute charge target
        station_node, st, t_arrive, need_kwh, _score = best_station

        # reserve edges chosen station (MAPF cooperative step)
        self._reserve_edge_sequence(node_tick_path, station_node)

        # reserve charger slot
        charge_ticks = self._charge_duration_ticks(need_kwh, st.speed_kw)

        _ = self._reserve_charger(station_node, t_arrive, charge_ticks, st.free)

        # SoC buffer avoid over-charging
        BUFFER_FINISH = 0.08  # 8% buffer after station
        km_to_station = cum_d.get(station_node, 0.0)
        kwh_to_station = km_to_station * cons
        kwh_rem_after_station = max(0.0, kwh_left - kwh_to_station)

        km_after = max(0.0, dist_cost - km_to_station)
        kwh_after = km_after * cons
        target_kwh_after_station = kwh_after * (1.0 + BUFFER_FINISH)
        required_from_station = max(0.0, target_kwh_after_station - kwh_rem_after_station)

        charge_to = min(1.0, (kwh_left + required_from_station) / cap)

        # mark customer charged 1<
        self.charged_once[cust.id] = True

        return {
            "customerId": cust.id,
            "chargingRecommendations": [
                {"nodeId": station_node, "chargeTo": round(charge_to, 3)}
            ]
        }

    # Tick entrypoint
    def recommendations_for_tick(self, tick: int) -> List[dict]:
        """
        Build charging recommendations for all customers visible on the current map snapshot.
        Skips customers already Charging/Waiting/Arrived/Failed.
        """
        recs: List[dict] = []
        for n in self.map.get("nodes", []):
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
