import sys
import time
from client import ConsiditionClient
from mapping.map_utils import customers_departing_at, shortest_path_grid


def should_move_on_to_next_tick(response):
    return True


def generate_customer_recommendations(map_obj, current_tick):
    dimX, dimY = map_obj["dimX"], map_obj["dimY"]

    # Client departures
    departing = customers_departing_at(map_obj, current_tick)
    if not departing:
        return []

    recs = []
    for c in departing:
        start = c["fromNode"]
        goal = c["toNode"]
        path = shortest_path_grid(start, goal, dimX, dimY)

        # Minimal viable schema (common in these engines)
        recs.append({
            "customerId": c["id"],
            "path": path  # if engine prefers 'route', flip the key name below
        })

    # logging
    sample = recs[:3]
    print(f"[tick {current_tick}] built {len(recs)} recommendation(s); sample: {sample}")
    return recs


def generate_tick(map_obj, current_tick):
    return {
        "tick": current_tick,
        "customerRecommendations": generate_customer_recommendations(map_obj, current_tick),
    }


def main():
    api_key = "73e3373c-d83e-43b3-b383-c30dcb22b6e6"
    base_url = "http://localhost:8080/api"
    map_name = "Turbohill"

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

            if "errors" in game_response and game_response["errors"]:
                print("Server validation errors:", game_response["errors"])

            if "messages" in game_response and game_response["messages"]:
                print("Server messages:", game_response["messages"])

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
