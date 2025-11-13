import sys
import time
import os
from dotenv import load_dotenv
from client import ConsiditionClient
# XGB-backed planner
from ml_planner.planner_xgb import FlowAwarePlannerXGB as FlowAwarePlanner

load_dotenv()

API_KEY = os.getenv("API_KEY") or os.getenv("CONSID_API_KEY", "API_KEY")
BASE_URL = os.getenv("API_BASE") or os.getenv("CONSID_BASE_URL", "http://localhost:8080")
MAP_NAME = os.getenv("MAP_NAME") or os.getenv("CONSID_MAP", "Batterytown")
_raw_play_to_tick = (os.getenv("PLAY_TO_TICK") or "").strip()

IS_CLOUD = "api.considition.com" in (BASE_URL or "")

# Normalize URL to https on cloud
if IS_CLOUD and BASE_URL.startswith("http://"):
    BASE_URL = BASE_URL.replace("http://", "https://", 1)

# Never playToTick on cloud (422 guard)
if IS_CLOUD:
    USE_PLAY_TO_TICK = False
    PLAY_TO_TICK = None
else:
    # Local/dev
    if _raw_play_to_tick:
        try:
            PLAY_TO_TICK = int(_raw_play_to_tick)
            USE_PLAY_TO_TICK = True
        except ValueError:
            PLAY_TO_TICK = None
            USE_PLAY_TO_TICK = False
    else:
        USE_PLAY_TO_TICK = os.getenv("USE_PLAY_TO_TICK", "false").lower() == "true"
        PLAY_TO_TICK = None


def kpis(map_obj):
    states = {}
    for n in map_obj.get("nodes", []):
        for c in n.get("customers", []):
            s = c.get("state")
            states[s] = states.get(s, 0) + 1
    return states


def should_move_on_to_next_tick(_response):
    # Hook for future retry logic; currently always advance.
    return True


def generate_tick(map_obj, current_tick, planner):
    """
    Build a single tick payload.

    - planner.update_map(map_obj) keeps the planner's CA* / MAPF
      reservations and station snapshot in sync with the API map.
    - recommendations_for_tick(current_tick) returns all customer decisions.
    """
    planner.update_map(map_obj)
    return {
        "tick": current_tick,
        "customerRecommendations": planner.recommendations_for_tick(current_tick),
    }


def main():
    client = ConsiditionClient(BASE_URL, API_KEY)

    try:
        map_obj = client.get_map(MAP_NAME)
    except Exception as e:
        print(f"Failed to fetch map: {e}")
        sys.exit(1)

    if not map_obj:
        print("Failed to fetch map!")
        sys.exit(1)

    planner = FlowAwarePlanner(map_obj)

    final_score = 0
    good_ticks = []

    # tracking for score breakdown
    prev_rev = 0
    prev_comp = 0
    prev_score = 0

    # initial tick
    current_tick = generate_tick(map_obj, 0, planner)
    input_payload = {
        "mapName": MAP_NAME,
        "ticks": [current_tick],
    }
    if not IS_CLOUD and USE_PLAY_TO_TICK:
        # first submission configured tick; step-by-step uses 0
        input_payload["playToTick"] = PLAY_TO_TICK if PLAY_TO_TICK is not None else 0

    total_ticks = int(map_obj.get("ticks", 0))

    if total_ticks <= 0:
        print("Map reports zero ticks; nothing to play.")
        sys.exit(1)

    for i in range(total_ticks):
        while True:
            print(f"Playing tick: {i}")
            start = time.perf_counter()
            try:
                game_response = client.post_game(input_payload)
            except Exception as e:
                print(f"Error posting game data: {e}")
                sys.exit(1)
            elapsed_ms = (time.perf_counter() - start) * 1000
            print(f"Tick {i} took: {elapsed_ms:.2f}ms")

            if not game_response:
                print("Got no game response")
                sys.exit(1)

            final_score = int(game_response.get("score", 0))
            updated_map = game_response.get("map", map_obj) or map_obj

            # Per-tick score deltas (note: with USE_PLAY_TO_TICK=false,
            # these are deltas vs last full-game response, not true per-tick deltas)
            rev = int(game_response.get("kwhRevenue", 0))
            comp = int(game_response.get("customerCompletionScore", 0))
            score = int(game_response.get("score", 0))

            d_rev = rev - prev_rev
            d_comp = comp - prev_comp
            d_score = score - prev_score  # kept for completeness/debug

            if (i % 24) == 0 or i == total_ticks - 1:
                print(
                    f"[t={i:4d}] ΔkWh={d_rev:+4d}  Δcust={d_comp:+4d}  "
                    f"totals: kWh={rev} cust={comp} score={score}"
                )
            if (i % 48) == 0:
                states = kpis(updated_map)
                waiting = states.get("WaitingForCharger", 0)
                print(f"   charger_wait={waiting}")

            prev_rev, prev_comp, prev_score = rev, comp, score

            if should_move_on_to_next_tick(game_response):
                # build next tick using updated map and planner
                good_ticks.append(current_tick)
                next_tick_index = i + 1
                current_tick = generate_tick(updated_map, next_tick_index, planner)

                input_payload = {
                    "mapName": MAP_NAME,
                    "ticks": [*good_ticks, current_tick],
                }
                if not IS_CLOUD and USE_PLAY_TO_TICK:
                    input_payload["playToTick"] = next_tick_index
                break
            else:
                # retry same tick with updated decisions
                current_tick = generate_tick(updated_map, i, planner)
                input_payload = {
                    "mapName": MAP_NAME,
                    "ticks": [*good_ticks, current_tick],
                }
                if not IS_CLOUD and USE_PLAY_TO_TICK:
                    input_payload["playToTick"] = i

    print(f"\nFinal kWhRevenue={prev_rev}  customerCompletion={prev_comp}  score={final_score}")


if __name__ == "__main__":
    main()
