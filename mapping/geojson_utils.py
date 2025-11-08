from typing import List, Tuple, Dict, Any


def parse_xy(node_id: str) -> Tuple[int, int]:
    x, y = node_id.split(".")
    return int(x), int(y)


def grid_to_latlon(x: int, y: int, anchor_lat=59.33, anchor_lon=18.06, step_deg=0.001, invert_y=False) -> Tuple[
    float, float]:
    """
    Synthetic transform grid -> lat/lon so Folium has a basemap location.
    Keep these constants the same per map so everything lines up.
    """
    lat = anchor_lat + (-y if invert_y else y) * step_deg
    lon = anchor_lon + x * step_deg
    return float(lat), float(lon)


def grid_path_to_geojson_feature(
        path_ids: List[str],
        tick: int,
        customer_id: str,
        extra_props: Dict[str, Any] = None,
        anchor_lat=59.33, anchor_lon=18.06, step_deg=0.001, invert_y=False
) -> Dict[str, Any]:
    xy = [parse_xy(pid) for pid in path_ids]
    latlon = [grid_to_latlon(x, y, anchor_lat, anchor_lon, step_deg, invert_y) for x, y in xy]
    # GeoJSON wants [lon, lat]
    coords = [[lon, lat] for (lat, lon) in latlon]
    props = {"tick": tick, "customerId": customer_id, **(extra_props or {})}
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {
            "type": "LineString",
            "coordinates": coords
        }
    }


def feature_collection(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}
