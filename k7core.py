"""
Core routing functions for K7-router.

Shared by the command-line script (K7-router.py) and the web UI (K7-server.py):
geocoding, downloading/caching the OSM bicycle network, k-shortest path search,
route geometry, route differences and GPX export.
"""

from __future__ import annotations

import functools
import itertools
import math
import os
import time

import gpxpy
import gpxpy.gpx
import networkx as nx
import osmnx as ox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GRAPH_CACHE_DIR = os.path.join(BASE_DIR, "graph_cache")

# Same Overpass server / user agent as chosen in K7-router.py.
OVERPASS_URL = "https://gall.openstreetmap.de/api"
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def configure(log_console: bool = False) -> None:
    """Apply OSMnx settings used by both the CLI and the server."""
    ox.settings.use_cache = True
    ox.settings.log_console = log_console
    ox.settings.requests_timeout = 300  # osmnx 2.x name (was "timeout" in 1.x)
    ox.settings.overpass_rate_limit = True
    ox.settings.request_retries = 3
    ox.settings.max_query_area_size = 1e12
    ox.settings.http_user_agent = HTTP_USER_AGENT
    ox.settings.overpass_url = OVERPASS_URL


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@functools.lru_cache(maxsize=256)
def geocode(address: str) -> tuple[float, float]:
    """Geocode an address to (lat, lon). Raises ValueError if nothing is found."""
    try:
        return ox.geocode(address)
    except Exception as exc:  # osmnx raises InsufficientResponseError (a ValueError subclass)
        raise ValueError(f"Could not geocode '{address}': {exc}") from exc


def search_area(start: tuple[float, float], end: tuple[float, float],
                buffer_m: float) -> tuple[tuple[float, float], float]:
    """Center point and radius (meters) of the circle covering both points plus a buffer."""
    center = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    radius = max(haversine(*center, *start), haversine(*center, *end)) + buffer_m
    return center, radius


def _cache_path(center: tuple[float, float], radius_m: float) -> str:
    # Round so that tiny differences in geocoding still hit the same cached graph.
    key = f"{center[0]:.3f}_{center[1]:.3f}_{int(math.ceil(radius_m / 250.0)) * 250}"
    return os.path.join(GRAPH_CACHE_DIR, f"bike_{key}.graphml")


def load_graph(center: tuple[float, float], radius_m: float) -> tuple[nx.MultiDiGraph, bool]:
    """
    Return the bicycle network around `center`, from the on-disk cache if available.

    The second element tells whether the graph came from the cache.
    """
    path = _cache_path(center, radius_m)
    if os.path.exists(path):
        return ox.load_graphml(path), True
    G = ox.graph_from_point(center_point=center, dist=radius_m, network_type="bike", simplify=True)
    os.makedirs(GRAPH_CACHE_DIR, exist_ok=True)
    ox.save_graphml(G, path)
    return G, False


def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    return ox.nearest_nodes(G, X=lon, Y=lat)


def k_shortest_paths(G: nx.MultiDiGraph, orig: int, dest: int, k: int) -> list[list[int]]:
    """
    Strictly ranked k shortest simple paths by OSM segment length.

    The 'length' attribute is already in meters, so no projection is needed.
    Parallel edges are collapsed to the shortest one.
    """
    G_simple = ox.convert.to_digraph(G, weight="length")
    gen = nx.shortest_simple_paths(G_simple, source=orig, target=dest, weight="length")
    return list(itertools.islice(gen, k))


def _best_edge(G: nx.MultiDiGraph, u: int, v: int) -> dict:
    return min(G.get_edge_data(u, v).values(), key=lambda d: d.get("length", 0.0))


def path_length(G: nx.MultiDiGraph, path: list[int]) -> float:
    return sum(_best_edge(G, u, v).get("length", 0.0) for u, v in zip(path[:-1], path[1:]))


def path_coords(G: nx.MultiDiGraph, path: list[int]) -> list[list[float]]:
    """Route geometry as [[lat, lon], ...], following edge geometries where available."""
    coords: list[list[float]] = []
    for u, v in zip(path[:-1], path[1:]):
        data = _best_edge(G, u, v)
        pu = (G.nodes[u]["y"], G.nodes[u]["x"])
        pv = (G.nodes[v]["y"], G.nodes[v]["x"])
        if "geometry" in data:
            pts = [[lat, lon] for lon, lat in data["geometry"].coords]
            # make sure the geometry runs from u to v
            if haversine(*pts[0], *pu) > haversine(*pts[-1], *pu):
                pts.reverse()
        else:
            pts = [list(pu), list(pv)]
        if coords and coords[-1] == pts[0]:
            pts = pts[1:]
        coords.extend(pts)
    return coords


def diff_segments(G: nx.MultiDiGraph, path: list[int], base: list[int]) -> list[list[list[float]]]:
    """
    Parts of `path` that are not shared with `base`, as a list of polylines.

    Edges are compared undirected, so riding the same street the other way is
    not counted as a difference.
    """
    base_edges = {frozenset((u, v)) for u, v in zip(base[:-1], base[1:])}
    segments: list[list[list[float]]] = []
    run: list[int] = []
    for u, v in zip(path[:-1], path[1:]):
        if frozenset((u, v)) in base_edges:
            if run:
                segments.append(path_coords(G, run))
                run = []
        else:
            if not run:
                run = [u]
            run.append(v)
    if run:
        segments.append(path_coords(G, run))
    return segments


def build_gpx(tracks: list[tuple[str, list[list[float]]]]) -> str:
    """GPX document with one track per (name, coords) pair."""
    gpx = gpxpy.gpx.GPX()
    for name, coords in tracks:
        track = gpxpy.gpx.GPXTrack()
        track.name = name
        seg = gpxpy.gpx.GPXTrackSegment()
        seg.points.extend(gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon) for lat, lon in coords)
        track.segments.append(seg)
        gpx.tracks.append(track)
    return gpx.to_xml()


def find_routes(start_address: str, end_address: str, k: int = 7,
                buffer_m: float = 500.0) -> dict:
    """
    Full pipeline: geocode, load network, rank routes, describe them for the UI.

    Returns a JSON-serialisable dict.
    """
    timings: dict[str, float] = {}

    t = time.perf_counter()
    start = geocode(start_address)
    end = geocode(end_address)
    timings["geocode_s"] = round(time.perf_counter() - t, 2)

    center, radius = search_area(start, end, buffer_m)

    t = time.perf_counter()
    G, cached = load_graph(center, radius)
    timings["graph_s"] = round(time.perf_counter() - t, 2)

    orig = nearest_node(G, *start)
    dest = nearest_node(G, *end)
    if orig == dest:
        raise ValueError("Start and end map to the same network node; choose points further apart.")

    t = time.perf_counter()
    try:
        paths = k_shortest_paths(G, orig, dest, k)
    except nx.NetworkXNoPath as exc:
        raise ValueError("No path between start and end in the bicycle network.") from exc
    timings["routing_s"] = round(time.perf_counter() - t, 2)

    best_len = path_length(G, paths[0]) if paths else 0.0
    routes = []
    for i, path in enumerate(paths):
        length = path_length(G, path)
        routes.append({
            "rank": i + 1,
            "length_m": round(length, 1),
            "delta_m": round(length - best_len, 1),
            "n_nodes": len(path),
            "coords": path_coords(G, path),
            "diff": diff_segments(G, path, paths[0]) if i > 0 else [],
        })

    return {
        "start": {"address": start_address, "lat": start[0], "lon": start[1], "node": int(orig)},
        "end": {"address": end_address, "lat": end[0], "lon": end[1], "node": int(dest)},
        "area": {"center": list(center), "radius_m": round(radius), "cached": cached,
                 "nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "routes": routes,
        "timings": timings,
    }
