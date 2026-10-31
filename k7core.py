"""
Core routing functions for K7-router.

Shared by the command-line script (K7-router.py) and the web UI (K7-server.py):
geocoding, downloading/caching the OSM bicycle network, k-shortest path search,
route geometry, route differences and GPX export.
"""

from __future__ import annotations

import functools
import glob
import itertools
import math
import os
import re
import time

import gpxpy
import gpxpy.gpx
import networkx as nx
import osmnx as ox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GRAPH_CACHE_DIR = os.path.join(BASE_DIR, "graph_cache")

# Local OSM extract (*.osm.pbf, e.g. from download.geofabrik.de or
# download.openstreetmap.fr/extracts). When present, the street network is
# built from it instead of being downloaded from Overpass. Set explicitly with
# set_pbf_path(); by default the first *.pbf file next to this module is used.
PBF_PATH: str | None = None

# The same filter OSMnx sends to Overpass for network_type="bike", applied
# locally when reading the PBF extract.
_EXCLUDED_HIGHWAY = re.compile(
    r"abandoned|bus_guideway|construction|corridor|elevator|escalator|footway|motor|no|planned"
    r"|platform|proposed|raceway|razed|rest_area|services|steps")

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


def set_pbf_path(path: str | None) -> None:
    """Use this PBF extract as the data source (None = auto-detect / Overpass)."""
    global PBF_PATH
    PBF_PATH = path


def pbf_path() -> str | None:
    """The PBF extract to use, or None if there is none (then Overpass is used)."""
    if PBF_PATH:
        return PBF_PATH if os.path.isfile(PBF_PATH) else None
    found = sorted(glob.glob(os.path.join(BASE_DIR, "*.pbf")))
    return found[0] if found else None


def _bike_way_ok(tags: dict) -> bool:
    hw = tags.get("highway")
    if not hw or _EXCLUDED_HIGHWAY.search(hw):
        return False
    if "yes" in (tags.get("area") or "") or "private" in (tags.get("access") or ""):
        return False
    if "no" in (tags.get("bicycle") or "") or "private" in (tags.get("service") or ""):
        return False
    return True


def graph_from_pbf(pbf: str, center: tuple[float, float], radius_m: float) -> nx.MultiDiGraph:
    """
    Bicycle network around `center` built from a local PBF extract.

    Two passes over the file with pyosmium: first the highway ways passing the
    bike filter that touch the bounding box of the search circle, then the
    nodes they use. The result is written as OSM XML next to the graph cache
    and loaded with OSMnx exactly like an Overpass response. The area is the
    bounding square of the circle, so it is slightly larger than what Overpass
    would return for the same radius.
    """
    import osmium  # optional dependency: pip install osmium

    lat, lon = center
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    south, north, west, east = lat - dlat, lat + dlat, lon - dlon, lon + dlon

    ways: list[tuple[int, list[int], dict]] = []
    node_ids: set[int] = set()
    fp = (osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY)
          .with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
          .with_filter(osmium.filter.KeyFilter("highway")))
    for w in fp:
        tags = dict(w.tags)
        if not _bike_way_ok(tags):
            continue
        if not any(n.location.valid() and south <= n.location.lat <= north and west <= n.location.lon <= east
                   for n in w.nodes):
            continue
        refs = [n.ref for n in w.nodes]
        ways.append((w.id, refs, tags))
        node_ids.update(refs)

    os.makedirs(GRAPH_CACHE_DIR, exist_ok=True)
    xml_path = os.path.join(GRAPH_CACHE_DIR, f"extract_{os.getpid()}.osm")
    writer = osmium.SimpleWriter(xml_path, overwrite=True)
    try:
        for n in osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(node_ids)):
            writer.add_node(n)
        for wid, refs, tags in ways:
            writer.add_way(osmium.osm.mutable.Way(id=wid, nodes=refs, tags=tags, version=1))
        writer.close()
        return ox.graph_from_xml(xml_path, simplify=True, retain_all=False)
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass


def _cache_path(center: tuple[float, float], radius_m: float, source: str) -> str:
    # Round so that tiny differences in geocoding still hit the same cached graph.
    # The PBF file's modification time is part of the key, so a fresh download
    # invalidates graphs built from the old one.
    key = f"{center[0]:.3f}_{center[1]:.3f}_{int(math.ceil(radius_m / 250.0)) * 250}"
    return os.path.join(GRAPH_CACHE_DIR, f"bike_{source}_{key}.graphml")


def load_graph(center: tuple[float, float], radius_m: float) -> tuple[nx.MultiDiGraph, bool, str]:
    """
    Return the bicycle network around `center`, from the on-disk cache if available.

    Built from the local PBF extract when one is configured or found (see
    pbf_path()), otherwise downloaded from Overpass. Returns (graph, cached,
    source) where source is "pbf" or "overpass".
    """
    pbf = pbf_path()
    if pbf:
        source = f"pbf{int(os.path.getmtime(pbf))}"
    else:
        source = "overpass"
    path = _cache_path(center, radius_m, source)
    if os.path.exists(path):
        return ox.load_graphml(path), True, ("pbf" if pbf else "overpass")
    if pbf:
        G = graph_from_pbf(pbf, center, radius_m)
    else:
        G = ox.graph_from_point(center_point=center, dist=radius_m, network_type="bike", simplify=True)
    os.makedirs(GRAPH_CACHE_DIR, exist_ok=True)
    ox.save_graphml(G, path)
    return G, False, ("pbf" if pbf else "overpass")


def nearest_node(G: nx.MultiDiGraph, lat: float, lon: float) -> int:
    return ox.nearest_nodes(G, X=lon, Y=lat)


def _diff_runs(path: list[int], reference: list[int]) -> list[list[int]]:
    """
    Maximal runs of `path` whose edges are not in `reference`, as node lists.

    Each run starts and ends on a node shared with the reference (or at the
    path's own ends). Edges are compared undirected.
    """
    ref_edges = {frozenset((u, v)) for u, v in zip(reference[:-1], reference[1:])}
    runs: list[list[int]] = []
    run: list[int] = []
    for u, v in zip(path[:-1], path[1:]):
        if frozenset((u, v)) in ref_edges:
            if run:
                runs.append(run)
                run = []
        else:
            if not run:
                run = [u]
            run.append(v)
    if run:
        runs.append(run)
    return runs


def local_stretch(G_simple: nx.DiGraph, path: list[int], reference: list[int]) -> float:
    """
    Worst local stretch of `path` relative to `reference`.

    For every section where the path leaves the reference, divide the section's
    length by the shortest-path distance between the section's end nodes. A
    parallel way gives a ratio near 1; a detour around a splitter island or
    through a parking lot gives 2-3. This is the "uniformly bounded stretch"
    criterion of Abraham, Delling, Goldberg & Werneck (2010), evaluated only on
    the windows where two routes differ.
    """
    worst = 1.0
    for run in _diff_runs(path, reference):
        section = sum(G_simple[u][v]["length"] for u, v in zip(run[:-1], run[1:]))
        shortest = nx.dijkstra_path_length(G_simple, run[0], run[-1], weight="length")
        if shortest > 0:
            worst = max(worst, section / shortest)
    return worst


def k_shortest_paths(G: nx.MultiDiGraph, orig: int, dest: int, k: int,
                     max_stretch: float | None = 0.15,
                     max_candidates: int | None = None) -> dict:
    """
    Strictly ranked k shortest simple paths by OSM segment length, optionally
    filtered for local optimality.

    Candidates come from Yen's algorithm in order of increasing length. The
    shortest one is always accepted. Every further candidate is compared with
    each already accepted route: if on any differing section it is longer than
    the shortest connection of that section's ends by more than `max_stretch`
    (0.15 = 15 %), it is rejected as a pointless micro-detour. `max_stretch=None`
    disables the filter. At most `max_candidates` candidates are examined
    (default 10*k, at least 50) so a hopeless search terminates.

    The 'length' attribute is already in meters, so no projection is needed.
    Parallel edges are collapsed to the shortest one.

    Returns a dict with 'accepted' (paths), 'rejected' (list of (path, stretch)),
    'examined' (candidate count), 'exhausted' (True if the candidate limit was
    hit before k routes were accepted) and 'graph' (the DiGraph used).
    """
    G_simple = ox.convert.to_digraph(G, weight="length")
    gen = nx.shortest_simple_paths(G_simple, source=orig, target=dest, weight="length")
    limit = max_candidates or max(10 * k, 50)
    accepted: list[list[int]] = []
    rejected: list[tuple[list[int], float]] = []
    examined = 0
    for path in gen:
        examined += 1
        if max_stretch is None or not accepted:
            accepted.append(path)
        else:
            stretch = max(local_stretch(G_simple, path, ref) for ref in accepted)
            if stretch <= 1.0 + max_stretch:
                accepted.append(path)
            else:
                rejected.append((path, stretch))
        if len(accepted) >= k or examined >= limit:
            break
    return {"accepted": accepted, "rejected": rejected, "examined": examined,
            "exhausted": len(accepted) < k, "graph": G_simple}


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
    return [path_coords(G, run) for run in _diff_runs(path, base)]


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


def _describe_route(G: nx.MultiDiGraph, path: list[int], base: list[int], best_len: float,
                    G_simple: nx.DiGraph) -> dict:
    length = float(path_length(G, path))  # plain float: graphs read from XML carry numpy scalars
    is_base = path == base
    return {
        "length_m": round(length, 1),
        "delta_m": round(length - float(best_len), 1),
        "n_nodes": len(path),
        "local_stretch": 1.0 if is_base else round(local_stretch(G_simple, path, base), 3),
        "coords": path_coords(G, path),
        "diff": [] if is_base else diff_segments(G, path, base),
    }


def find_routes(start_address: str, end_address: str, k: int = 7,
                buffer_m: float = 500.0, max_stretch: float | None = 0.15) -> dict:
    """
    Full pipeline: geocode, load network, rank routes, describe them for the UI.

    `max_stretch` is the local-optimality threshold (0.15 = a differing section
    may be at most 15 % longer than the shortest connection of its ends);
    None disables the filter. Returns a JSON-serialisable dict.
    """
    timings: dict[str, float] = {}

    t = time.perf_counter()
    start = geocode(start_address)
    end = geocode(end_address)
    timings["geocode_s"] = round(time.perf_counter() - t, 2)

    center, radius = search_area(start, end, buffer_m)

    t = time.perf_counter()
    G, cached, source = load_graph(center, radius)
    timings["graph_s"] = round(time.perf_counter() - t, 2)

    orig = nearest_node(G, *start)
    dest = nearest_node(G, *end)
    if orig == dest:
        raise ValueError("Start and end map to the same network node; choose points further apart.")

    t = time.perf_counter()
    try:
        search = k_shortest_paths(G, orig, dest, k, max_stretch=max_stretch)
    except nx.NetworkXNoPath as exc:
        raise ValueError("No path between start and end in the bicycle network.") from exc
    timings["routing_s"] = round(time.perf_counter() - t, 2)

    paths = search["accepted"]
    G_simple = search["graph"]
    base = paths[0]
    best_len = path_length(G, base)

    routes = []
    for i, path in enumerate(paths):
        routes.append({"rank": i + 1, **_describe_route(G, path, base, best_len, G_simple)})

    rejected = []
    for j, (path, stretch) in enumerate(search["rejected"]):
        rejected.append({"rank": j + 1, **_describe_route(G, path, base, best_len, G_simple),
                         "rejected_stretch": round(stretch, 3)})

    return {
        "start": {"address": start_address, "lat": start[0], "lon": start[1], "node": int(orig)},
        "end": {"address": end_address, "lat": end[0], "lon": end[1], "node": int(dest)},
        "area": {"center": list(center), "radius_m": round(radius), "cached": cached, "source": source,
                 "pbf": os.path.basename(pbf_path()) if source == "pbf" else None,
                 "nodes": G.number_of_nodes(), "edges": G.number_of_edges()},
        "filter": {"max_stretch": max_stretch, "examined": search["examined"],
                   "rejected": len(rejected), "exhausted": search["exhausted"]},
        "routes": routes,
        "rejected": rejected,
        "timings": timings,
    }
