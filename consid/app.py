import sys
import time
from client import ConsiditionClient
# --- drop this near the top of app.py ---
from heapq import heappush, heappop
import math

WH_PER_KM = 150.0  # tune quickly (vehicle energy model)
RESERVE_KWH = 4.0  # safety buffer
GREEN_BONUS = 0.5  # lowers cost for green charging choices
MAX_DETOUR_KM = 2.0  # acceptable extra to pick green over regular


class Planner:
    def __init__(self, map_obj):
        self.map = map_obj
        self.G, self.veh, self.cust, self.sta = self._from_map(map_obj)
        self.assign = {}  # customer_id -> vehicle_id
        self.plan = {}  # vehicle_id -> [node,...]
        self.dest = {}  # vehicle_id -> target node (pickup, dropoff or station)

    # map ingestion
    def _from_map(self, m):
        # Adjust field names to match the actual schema you print from get_map()
        G = {n["id"]: n.get("edges", []) for n in m["graph"]}
        veh = {v["id"]: {"node": v["node"], "soc": float(v["soc_kwh"])} for v in m["vehicles"]}
        cust = {c["id"]: {"pickup": c["pickup"], "drop": c["dropoff"], "prefs": c.get("prefs", {})} for c in
                m["customers"]}
        sta = [{"node": s["node"], "kw": s.get("kw", 50), "green": bool(s.get("green", False))} for s in m["stations"]]
        return G, veh, cust, sta

    # ----- core utils -----
    def _dist_km(self, a, b):
        # If graph provides edge distances, rely on them; otherwise fallback
        return 1.0

    def _energy_needed_kwh(self, path_len_km):
        return (WH_PER_KM * path_len_km) / 1000.0

    def _neighbors(self, n):
        # Expect edges like [{"to": node_id, "dist_km": float}]
        return self.G.get(n, [])

    def _astar(self, s, t):
        # A* on given graph with edge 'dist_km' and straight-line heuristic if available
        # If you lack coords, Dijkstra == A* with h=0
        openq, came, g = [], {}, {s: 0.0}
        heappush(openq, (0.0, s))
        while openq:
            _, u = heappop(openq)
            if u == t:
                break
            for e in self._neighbors(u):
                v, w = e["to"], float(e.get("dist_km", 1.0))
                ng = g[u] + w
                if ng < g.get(v, math.inf):
                    g[v] = ng;
                    came[v] = u
                    heappush(openq, (ng, v))
        if t not in came and s != t:
            return None, math.inf
        # Reconstruct
        path = [t]
        while path[-1] != s:
            path.append(came[path[-1]])
        path.reverse()
        return path, g.get(t, 0.0)

    def _nearest_station(self, from_node, prefer_green=True):
        best = None
        for s in self.sta:
            path, d = self._astar(from_node, s["node"])
            if path is None:
                continue
            cost = d - (GREEN_BONUS if (prefer_green and s["green"]) else 0.0)
            if best is None or cost < best[0]:
                best = (cost, path, d, s)
        return best  # (score, path, dist_km, station)

    # ----- one-tick plan -----
    def step(self):
        recs = []

        # 1) Assign unassigned customers greedily with energy feasibility
        unassigned = [cid for cid in self.cust if cid not in self.assign]
        for cid in unassigned:
            c = self.cust[cid]
            best = None
            for vid, v in self.veh.items():
                # pickup path
                p_pick, d1 = self._astar(v["node"], c["pickup"])
                if p_pick is None:
                    continue
                # dropoff path
                p_drop, d2 = self._astar(c["pickup"], c["drop"])
                if p_drop is None:
                    continue

                need_kwh = self._energy_needed_kwh(d1 + d2)
                if v["soc"] >= need_kwh + RESERVE_KWH:
                    score = d1 + d2
                    if best is None or score < best[0]:
                        best = (score, vid, p_pick + p_drop[1:], need_kwh)
                else:
                    # consider one charge
                    st = self._nearest_station(v["node"], prefer_green=True)
                    if not st:
                        continue
                    _, p_charge, dS, s_meta = st
                    p_after, d_after = self._astar(s_meta["node"], c["pickup"])
                    p_drop2, d2b = self._astar(c["pickup"], c["drop"])
                    if p_after and p_drop2:
                        score = dS + d_after + d2b - (GREEN_BONUS if s_meta["green"] else 0.0)
                        if best is None or score < best[0]:
                            best = (score, vid, p_charge + p_after[1:] + p_drop2[1:], None)

            if best:
                _, vid, full_path, _ = best
                self.assign[cid] = vid
                self.plan[vid] = full_path
                self.dest[vid] = self.cust[cid]["drop"]

        # 2) Emit one-step actions for each vehicle
        for vid, v in self.veh.items():
            cur = v["node"]
            path = self.plan.get(vid)
            if not path or len(path) < 2:
                # idle or arrived
                recs.append({"vehicleId": vid, "action": "wait"})
                continue

            nxt = path[1]
            # Consume energy of this edge
            # (In a real build, read exact edge dist; here we grab from neighbors)
            edge = next((e for e in self._neighbors(cur) if e["to"] == nxt), None)
            dist = float(edge.get("dist_km", 1.0)) if edge else 1.0
            need = self._energy_needed_kwh(dist)

            if v["soc"] < need + RESERVE_KWH:
                # detour to nearest station this tick
                st = self._nearest_station(cur, prefer_green=True)
                if st:
                    _, p_charge, dS, s_meta = st
                    # step one hop toward station
                    hop = p_charge[1] if len(p_charge) >= 2 else cur
                    recs.append({"vehicleId": vid, "action": "drive", "to": hop})
                else:
                    recs.append({"vehicleId": vid, "action": "wait"})
            else:
                recs.append({"vehicleId": vid, "action": "drive", "to": nxt})

        return recs


def should_move_on_to_next_tick(response):
    return True


def generate_customer_recommendations(map_obj, current_tick):
    return []


def generate_tick(map_obj, current_tick):
    return {
        "tick": current_tick,
        "customerRecommendations": generate_customer_recommendations(map_obj, current_tick),
    }


def main():
    api_key = "INSERT API KEY HERE"
    base_url = "INSERT YOUR CHOSEN PORT HERE"
    map_name = "INSERT MAP NAME HERE"

    client = ConsiditionClient(base_url, api_key)
    map_obj = client.get_map(map_name)

    if not map_obj:
        print("Failed to fetch map!")
        sys.exit(1)

    final_score = 0
    good_ticks = []

    current_tick = generate_tick(map_obj, 0)
    input_payload = {
        "mapName": map_name,
        "ticks": [current_tick],
    }

    total_ticks = int(map_obj.get("ticks", 0))

    for i in range(total_ticks):
        while True:
            print(f"Playing tick: {i} with input: {input_payload}")
            start = time.perf_counter()
            game_response = client.post_game(input_payload)
            elapsed_ms = (time.perf_counter() - start) * 1000
            print(f"Tick {i} took: {elapsed_ms:.2f}ms")

            if not game_response:
                print("Got no game response")
                sys.exit(1)

            # Sum the scores directly (assuming they are numbers)
            final_score = (
                    game_response.get("customerCompletionScore", 0)
                    + game_response.get("kwhRevenue", 0)
                    + game_response.get("score", 0)
            )

            if should_move_on_to_next_tick(game_response):
                good_ticks.append(current_tick)
                updated_map = game_response.get("map", map_obj) or map_obj
                current_tick = generate_tick(updated_map, i + 1)
                input_payload = {
                    "mapName": map_name,
                    "playToTick": i + 1,
                    "ticks": [*good_ticks, current_tick],
                }
                break

            updated_map = game_response.get("map", map_obj) or map_obj
            current_tick = generate_tick(updated_map, i)
            input_payload = {
                "mapName": map_name,
                "playToTick": i,
                "ticks": [*good_ticks, current_tick],
            }

    print(f"Final score: {final_score}")


if __name__ == "__main__":
    main()
