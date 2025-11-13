import json
import math
import os
from typing import Dict, List
import numpy as np
from planner.planner import FlowAwarePlanner, CustomerState

"""
XGB policy-backed planner (flow-aware, persona-sensitive).

- Always uses CA* (time-expanded A* with MAPF reservations) for routing.
- Stressed personas:
    * Micro/bump charge at the first reachable station on the corridor,
      preferring green if possible.
- Other personas:
    * XGB scores "charge" vs "skip" for each reachable station along
      the CA* corridor.
    * We then size charge_to in a moderate SoC band (not greedy),
      with a small green bias.
- Falls back to vanilla FlowAwarePlanner behavior if model missing.

Env:
  POLICY_DIR          path with policy_xgb.json + policy_features.json (default ./model_artifacts)
  POLICY_CHARGE_BIAS  float, add to predicted reward when choosing 'charge' (default 0.0)
"""

try:
    import xgboost as xgb
except Exception:
    xgb = None  # fallback if missing


class _XGBPolicy:
    def __init__(self, policy_dir: str):
        self.model = None
        self.cols: List[str] = []
        self.charge_bias = float(os.environ.get("POLICY_CHARGE_BIAS", "0.0"))
        if xgb is None:
            return
        mpath = os.path.join(policy_dir, "policy_xgb.json")
        fpath = os.path.join(policy_dir, "policy_features.json")
        if os.path.exists(mpath) and os.path.exists(fpath):
            try:
                self.model = xgb.XGBRegressor()
                self.model.load_model(mpath)
                with open(fpath, "r") as f:
                    self.cols = json.load(f)
            except Exception:
                self.model = None
                self.cols = []

    def ready(self) -> bool:
        return self.model is not None and len(self.cols) > 0

    def _vectorize(self, feats: Dict) -> np.ndarray:
        x = np.zeros((1, len(self.cols)), dtype=float)
        for i, c in enumerate(self.cols):
            x[0, i] = float(feats.get(c, 0.0))
        return x

    def predict_reward(self, feats: Dict, action: str) -> float:
        """
        Action in {"charge","skip"}.
        """
        if not self.ready():
            return 0.0
        x = self._vectorize(feats)
        y = float(self.model.predict(x)[0])
        if action == "charge":
            y += self.charge_bias
        return y


class FlowAwarePlannerXGB(FlowAwarePlanner):
    def __init__(self, map_obj: dict):
        super().__init__(map_obj)
        policy_dir = os.environ.get("POLICY_DIR", "./model_artifacts")
        self._policy = _XGBPolicy(policy_dir)

        # SoC band for XGB-based decisions (moderate, not greedy)
        self.MIN_SOC = max(0.30, self.MIN_SOC)
        self.MAX_SOC = min(0.92, getattr(self, "MAX_SOC", 0.92))

    # Policy/Heuristic choosers (node-based GREEN)
    def _choose_station_with_policy(
            self,
            node_tick_path,
            nodes_seq,
            cum_d,
            kwh_left: float,
            cons: float,
            dist_cost: float,
            tick: int,
            cap: float,
    ):
        """
        Learned policy picks station and action.
        Returns (nid, t_arrive, action_id, need_kwh) or None.
        action_id 1 = "charge", 0 = "skip".
        """
        if not (hasattr(self, "_policy") and self._policy and self._policy.ready()):
            return None

        rows = []
        meta = []  # (nid, t_arrive, km_to_station, soc_at_station)
        EPS = 1e-9

        for nid in nodes_seq:
            st = self.station_by_node.get(nid)
            if not st:
                continue
            t_arrive = next((t_ for n_, t_ in node_tick_path if n_ == nid), None)
            if t_arrive is None:
                continue

            km_to_station = cum_d.get(nid, 0.0)
            kwh_to_station = km_to_station * cons
            if kwh_left < kwh_to_station * 1.05:
                continue  # not reachable

            green = 1.0 if getattr(st, "is_green", False) else 0.0

            queue_pressure = 0.0
            if st.total > 0:
                queue_pressure = max(0, st.total - st.free) / float(st.total)

            soc_at_station = max(0.0, (kwh_left - kwh_to_station) / max(EPS, cap))
            km_after = max(0.0, dist_cost - km_to_station)

            rows.append([
                green,
                float(st.speed_kw),
                queue_pressure,
                soc_at_station,
                km_after,
                float(st.free),
            ])
            meta.append((nid, t_arrive, km_to_station, soc_at_station))

        if not rows:
            return None

        try:
            action_ids = []
            for r in rows:
                feats = {
                    "is_green": r[0],
                    "station_speed_kw": r[1],
                    "queue_len": r[2],
                    "soc": r[3],
                    "dist_to_goal_km": r[4],
                    "free": r[5],
                }
                r_charge = self._policy.predict_reward(feats, "charge")
                r_skip = self._policy.predict_reward(feats, "skip")
                action_ids.append(1 if r_charge >= r_skip else 0)
        except Exception:
            return None

        # Translate action to kWh
        for idx, action_id in enumerate(action_ids):
            if action_id == 0:
                continue

            nid, t_arrive, km_to_station, soc_at_station = meta[idx]
            st = self.station_by_node[nid]
            kwh_to_station = km_to_station * cons
            kwh_rem_after_station = max(0.0, kwh_left - kwh_to_station)

            km_after = max(0.0, dist_cost - km_to_station)
            kwh_after = km_after * cons

            buffer = 0.12
            target_after = kwh_after * (1.0 + buffer)
            min_required = max(0.0, target_after - kwh_rem_after_station)

            base_target_soc = 0.70 if getattr(st, "is_green", False) else 0.62
            base_target_soc = max(self.MIN_SOC, min(self.MAX_SOC, base_target_soc))
            target_kwh = max(0.0, base_target_soc * cap - kwh_rem_after_station)

            need_kwh = max(min_required, target_kwh, 0.05 * cap)
            return (nid, t_arrive, int(action_id), float(need_kwh))

        return None

    def _choose_station_heuristic(
            self,
            node_tick_path,
            nodes_seq,
            cum_d,
            kwh_left: float,
            cons: float,
            dist_cost: float,
            tick: int,
            cap: float,
    ):
        """
        Baseline heuristic station picker, returns (nid, t_arrive, need_kwh) or None.
        Prefers GREEN STATIONS (node flag), low queue, reachable-on-path;
        sizes charge to finish leg + buffer.
        """
        best = None  # (score, (nid, t_arrive, need_kwh))
        for nid in nodes_seq:
            st = self.station_by_node.get(nid)
            if not st:
                continue

            t_arrive = next((t_ for n_, t_ in node_tick_path if n_ == nid), None)
            if t_arrive is None:
                continue

            km_to_station = cum_d.get(nid, 0.0)
            kwh_to_station = km_to_station * cons
            if kwh_left < kwh_to_station * 1.05:
                continue  # not reachable

            km_after = max(0.0, dist_cost - km_to_station)
            kwh_after = km_after * cons
            kwh_rem_after_station = max(0.0, kwh_left - kwh_to_station)

            BUFFER_FINISH = 0.12
            target_after = kwh_after * (1.0 + BUFFER_FINISH)
            need_kwh = max(0.0, target_after - kwh_rem_after_station)

            green = 1.0 if getattr(st, "is_green", False) else 0.0
            queue_pressure = 0.0
            if st.total > 0:
                queue_pressure = max(0, st.total - st.free) / float(st.total)
            speed_benefit = math.log1p(max(st.speed_kw, 0.0))

            score = (1.0 * green) + (0.5 * speed_benefit) - (0.7 * queue_pressure)
            cand = (score, (nid, t_arrive, max(need_kwh, 0.05 * cap)))
            if (best is None) or (score > best[0]):
                best = cand

        return None if best is None else best[1]

    # Planning step
    def _plan_single(self, cust: CustomerState, tick: int):
        """
        Planning step that:

        - Always computes a CA* corridor (A* + MAPF).
        - For stressed personas: micro/bump charge at the first reachable
          station, preferring green.
        - For other personas: use XGB to decide where to charge, with a
          moderate SoC target.
        """
        soc = max(0.0, float(cust.charge_remaining))
        cap = float(cust.max_charge)
        cons = max(1e-6, float(cust.consumption_per_km))

        persona = (cust.persona or "Neutral").lower()
        is_stressed = ("stressed" in persona) or ("hurry" in persona)

        # CA* main corridor (time-expanded A* with reservations)
        node_tick_path = self._ca_star(cust.from_node, cust.to_node, tick)
        nodes_seq = [n for (n, _t) in node_tick_path]
        if len(nodes_seq) == 1 and nodes_seq[0] == cust.from_node:
            return None

        # path length
        dist_cost = 0.0
        for i in range(len(nodes_seq) - 1):
            u, v = nodes_seq[i], nodes_seq[i + 1]
            L = next((L for w, L, _ in self.adj.get(u, []) if w == v), 0.0)
            dist_cost += L

        # cumulative distances
        cum_d = {nodes_seq[0]: 0.0}
        for i in range(1, len(nodes_seq)):
            u, v = nodes_seq[i - 1], nodes_seq[i]
            L = next((L for w, L, _ in self.adj.get(u, []) if w == v), 0.0)
            cum_d[v] = cum_d[u] + L

        kwh_left = soc * cap

        # Stressed personas: bump/micro charge at first possible station
        if is_stressed and dist_cost > 0.0:
            first_reachable = None
            first_green_reachable = None

            for nid in nodes_seq:
                st = self.station_by_node.get(nid)
                if not st:
                    continue

                km_to_station = cum_d.get(nid, 0.0)
                kwh_to_station = km_to_station * cons
                if kwh_left < kwh_to_station * 1.05:
                    continue  # can't reach safely

                tup = (nid, st, km_to_station)
                if first_reachable is None:
                    first_reachable = tup
                if getattr(st, "is_green", False) and first_green_reachable is None:
                    first_green_reachable = tup

                if first_reachable is not None and first_green_reachable is not None:
                    break

            choice = first_green_reachable or first_reachable
            if choice is not None:
                nid, st, km_to_station = choice
                t_arrive = next((t_ for n_, t_ in node_tick_path if n_ == nid), None)
                if t_arrive is not None:
                    kwh_to_station = km_to_station * cons
                    kwh_rem_after_station = max(0.0, kwh_left - kwh_to_station)

                    # Micro bump: 0.55 SOC for normal, 0.60 if green
                    base_target_soc = 0.60 if getattr(st, "is_green", False) else 0.55
                    base_target_soc = max(self.MIN_SOC, min(self.MAX_SOC, base_target_soc))

                    target_kwh = max(0.0, base_target_soc * cap - kwh_rem_after_station)
                    tiny_floor = 0.05 * cap
                    need_kwh = max(target_kwh, tiny_floor)

                    # Reserve corridor & charger
                    self._reserve_edge_sequence(node_tick_path, nid)
                    charge_ticks = self._charge_duration_ticks(need_kwh, st.speed_kw)
                    _ = self._reserve_charger(nid, t_arrive, charge_ticks, st.free)

                    charge_to = (kwh_rem_after_station + need_kwh) / max(cap, 1e-9)
                    charge_to = max(self.MIN_SOC, min(self.MAX_SOC, charge_to))

                    return {
                        "customerId": cust.id,
                        "chargingRecommendations": [
                            {"nodeId": nid, "chargeTo": round(float(charge_to), 3)}
                        ],
                    }

        # --- 2) Non-stressed (or stressed fallback): XGB-based station choice ---
        picked = self._choose_station_with_policy(
            node_tick_path=node_tick_path,
            nodes_seq=nodes_seq,
            cum_d=cum_d,
            kwh_left=kwh_left,
            cons=cons,
            dist_cost=dist_cost,
            tick=tick,
            cap=cap,
        )

        if picked is None:
            heur = self._choose_station_heuristic(
                node_tick_path=node_tick_path,
                nodes_seq=nodes_seq,
                cum_d=cum_d,
                kwh_left=kwh_left,
                cons=cons,
                dist_cost=dist_cost,
                tick=tick,
                cap=cap,
            )
            if heur is None:
                return None
            nid, t_arrive, need_kwh = heur
            action = -1  # heuristic marker
        else:
            nid, t_arrive, action, need_kwh = picked

        st = self.station_by_node.get(nid)
        if st is None:
            return None

        # Reserve edge-time + charger
        self._reserve_edge_sequence(node_tick_path, nid)
        charge_ticks = self._charge_duration_ticks(need_kwh, st.speed_kw)
        _ = self._reserve_charger(nid, t_arrive, charge_ticks, st.free)

        # Final SoC
        km_to_station = cum_d.get(nid, 0.0)
        kwh_to_station = km_to_station * cons
        kwh_rem_after_station = max(0.0, kwh_left - kwh_to_station)
        charge_to = (kwh_rem_after_station + need_kwh) / max(cap, 1e-9)

        charge_to = max(self.MIN_SOC, min(self.MAX_SOC, charge_to))

        return {
            "customerId": cust.id,
            "chargingRecommendations": [
                {"nodeId": nid, "chargeTo": round(float(charge_to), 3)}
            ],
        }
