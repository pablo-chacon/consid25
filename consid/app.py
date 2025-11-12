import sys
import time
import os
from client import ConsiditionClient
from planner.planner import FlowAwarePlanner
from dotenv import load_dotenv


load_dotenv()
API_KEY = os.getenv("API_KEY") or os.getenv("CONSID_API_KEY", "YOUR-KEY")
BASE_URL = os.getenv("API_BASE") or os.getenv("CONSID_BASE_URL", "http://localhost:8080")
MAP_NAME = os.getenv("MAP_NAME") or os.getenv("CONSID_MAP", "Turbohill")

_raw_play_to_tick = os.getenv("PLAY_TO_TICK", "").strip()
PLAY_TO_TICK = None
USE_PLAY_TO_TICK = False
if _raw_play_to_tick:
    try:
        PLAY_TO_TICK = int(_raw_play_to_tick)
        USE_PLAY_TO_TICK = True
    except ValueError:
        PLAY_TO_TICK = None
        USE_PLAY_TO_TICK = False
else:
    USE_PLAY_TO_TICK = os.getenv("USE_PLAY_TO_TICK", "true").lower() == "true"
    PLAY_TO_TICK = None


def should_move_on_to_next_tick(_response):
    return True


def generate_tick(map_obj, current_tick, planner: FlowAwarePlanner):
    # ensure planner uses latest map snapshot for decisions
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

    # initial tick
    current_tick = generate_tick(map_obj, 0, planner)
    input_payload = {
        "mapName": MAP_NAME,
        "ticks": [current_tick],
    }
    if USE_PLAY_TO_TICK:
        # first submission configured tick; step-by-step use 0
        input_payload["playToTick"] = PLAY_TO_TICK if PLAY_TO_TICK is not None else 0

    total_ticks = int(map_obj.get("ticks", 0))

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

            final_score = game_response.get("score", 0)
            updated_map = game_response.get("map", map_obj) or map_obj

            if should_move_on_to_next_tick(game_response):
                # build next tick using updated map and planner
                good_ticks.append(current_tick)
                next_tick_index = i + 1
                current_tick = generate_tick(updated_map, next_tick_index, planner)

                input_payload = {
                    "mapName": MAP_NAME,
                    "ticks": [*good_ticks, current_tick],
                }
                if USE_PLAY_TO_TICK:
                    input_payload["playToTick"] = next_tick_index
                break
            else:
                # retry same tick with updated decisions
                current_tick = generate_tick(updated_map, i, planner)
                input_payload = {
                    "mapName": MAP_NAME,
                    "ticks": [*good_ticks, current_tick],
                }
                if USE_PLAY_TO_TICK:
                    input_payload["playToTick"] = i

    print(f"Final score: {final_score}")


if __name__ == "__main__":
    main()
