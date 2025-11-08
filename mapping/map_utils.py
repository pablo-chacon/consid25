from collections import deque
import heapq


def index_map(map_obj):
    """Build fast lookups."""
    nodes = map_obj.get("nodes", [])
    node_by_id = {n["id"]: n for n in nodes}
    customers = []
    for n in nodes:
        for c in n.get("customers", []):
            customers.append(c)
    return node_by_id, customers


def customers_departing_at(map_obj, tick: int):
    _, customers = index_map(map_obj)
    return [c for c in customers if int(c.get("departureTick", -1)) == int(tick)]


def customers_active(map_obj, tick: int):
    # Define activity window.
    _, customers = index_map(map_obj)
    return [c for c in customers if c.get("state") != "Home" or c.get("departureTick", 10 ** 9) <= tick]


def parse_xy(node_id: str):
    x, y = node_id.split(".")
    return int(x), int(y)


def format_xy(x: int, y: int):
    return f"{x}.{y}"


def neighbors(node_id: str, dimX: int, dimY: int):
    x, y = parse_xy(node_id)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < dimX and 0 <= ny < dimY:
            yield format_xy(nx, ny)


def shortest_path_grid(start_id: str, goal_id: str, dimX: int, dimY: int):
    """Manhattan BFS ignoring obstacles (good first baseline)."""
    if start_id == goal_id:
        return [start_id]
    q = deque([start_id])
    prev = {start_id: None}
    while q:
        u = q.popleft()
        for v in neighbors(u, dimX, dimY):
            if v in prev:
                continue
            prev[v] = u
            if v == goal_id:
                # reconstruct
                path = [v]
                while u is not None:
                    path.append(u)
                    u = prev[u]
                return list(reversed(path))
            q.append(v)
    return [start_id]  # fallback


def manhattan(a, b):
    ax, ay = parse_xy(a)
    bx, by = parse_xy(b)
    return abs(ax - bx) + abs(ay - by)


def shortest_path_grid_astar(start_id: str, goal_id: str, dimX: int, dimY: int):
    if start_id == goal_id:
        return [start_id]

    open_heap = []
    heapq.heappush(open_heap, (0, start_id))
    came_from = {start_id: None}
    g = {start_id: 0}

    while open_heap:
        _, u = heapq.heappop(open_heap)
        if u == goal_id:
            # reconstruct
            path = [u]
            while came_from[u] is not None:
                u = came_from[u]
                path.append(u)
            return list(reversed(path))

        for v in neighbors(u, dimX, dimY):
            ng = g[u] + 1
            if ng < g.get(v, 1e9):
                came_from[v] = u
                g[v] = ng
                f = ng + manhattan(v, goal_id)
                heapq.heappush(open_heap, (f, v))

    return [start_id]
