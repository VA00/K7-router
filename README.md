# K7-router

Brute-force strictly ranked bicycle commute route planner.

## Rationale

Top well-known routing apps — Google Maps, BRouter, GraphHopper, OSRM — excel at fast optimal route finding in dynamic traffic environments using famous A* or Dijkstra algorithms. However, as every user knows, their alternative routes are simply ridiculous and usually useless.

On your daily commute you know exactly which corner you can cut through, and which of nearly identical paths might be better today. These alternatives often differ by just a single block or a parallel road of similar length. Yet most popular apps are exceptionally bad at listing those obvious alternatives.

This is particularly important for bicycle commuting, where every mistake is felt in your legs and delays become significant. The aim of this app is to provide a ranked list of possible routes where the 2nd best route is indeed 2nd best — longer by just 2 meters compared to the optimal one. This might be as small a change as passing a roundabout clockwise or counterclockwise. Both are legal for bicycle paths, at least in Poland, but there are numerous reasons for choosing one over the other.

K7-router gives you a list of 7 routes that differ minimally. Any of them would be considered optimal by standard routing algorithms, but for you on a bicycle, it might be the difference between being doored or not.

Use the router for your own goals, even if it's as trivial as counteracting boredom on fixed routes. Since the router works offline, we don't care about computational costs. Road networks don't change daily, especially for cyclepaths — even in GDP leader countries like Poland, which build new roads like crazy.

## Implementation

OpenStreetMap road network is used as the database for shortest path graphs. You can update it yourself for your own goals, with benefits for everyone.

For now, a Python script returning GPX files is provided. Start and end locations must be typed inside the code. But even in this simple form it's very useful — check it out to learn how little you know about your daily route!

I found a surprising alternative on a route I've used for more than a decade. It was the 7th alternative (I already knew the other 6), and that's why I named the app K7-router. The number of progressively longer routes is configurable.

So far, the code has been tested on short routes up to 10 km in not very dense areas. For now, only the length of OSM road segments is used to find routes.

### Pointless detours and the local-optimality filter

Plain k-shortest-paths (Yen's algorithm) treats any legal path as an alternative, including
leaving the optimal route only to loop around a roundabout splitter island via its one-way
legs, or to cut through a petrol station forecourt or a parking lot. Those variants are
filtered out with a *local optimality* criterion (the "uniformly bounded stretch" of Abraham,
Delling, Goldberg & Werneck, 2010): on every section where a candidate leaves an already
accepted route, it may be longer than the shortest connection of that section's end points by
at most a configurable percentage (default 15 %). Measured on real cases, a cycle path running
parallel to the roadway stretches by about 1 %, while roundabout and parking-lot detours
stretch by 160 % to 700 %, so the threshold has a wide safe range. Genuinely parallel ways,
even 1 m apart, are kept — they are exactly the alternatives this tool is about.

### Known limitation: routes are combinations of local choices

A commute route typically contains a few *independent* places where you can choose, e.g.
roadway vs. cycle path here, clockwise vs. counterclockwise around a roundabout there. Ranking
whole routes by length then produces every *combination* of those choices: with local
alternatives costing +2.8 m and +18 m you get routes at +2.8, +18 and +20.8 m, and the third
one tells you nothing new. In practice a list of 7 routes often carries only 3 or 4 distinct
decisions, while the next genuinely different alternative does not make the cut. The human
brain classifies such combinations as "the same route" automatically; the algorithm does not.

Note that this is not a matter of identical lengths: edge lengths are full-precision great-circle
distances, exact ties practically never occur, and Yen never returns the same path twice. The
routes are distinct — they are just not independent.

The planned remedy is to rank *local alternatives* instead of full routes: extract the unique
differing sections (entry node, exit node, nodes in between) from a long list of Yen candidates,
rank them by their own local extra length, and let the user compose a route from independent
choices. In that view, the 7th route of K7-router is the 7th local decision, not the 7th
combination of six known ones.

## Requirements

- Python 3.8+
- [osmnx](https://github.com/gboeing/osmnx)
- [networkx](https://networkx.org/)
- [gpxpy](https://github.com/tkrajina/gpxpy)

## Installation

```bash
pip install osmnx networkx gpxpy osmium
```

### Map data

By default the router builds its street network from a **local OpenStreetMap extract**:
download the `*.osm.pbf` file of your region, e.g. from
[download.geofabrik.de](https://download.geofabrik.de/) or
[download.openstreetmap.fr/extracts](https://download.openstreetmap.fr/extracts/), and put it
next to the scripts (or pass `--pbf FILE` to the server). Extracts are updated daily, a
voivodeship is a few hundred MB, and routing then needs no network access at all. Reading
the extract requires the `osmium` package (pyosmium).

Without a PBF file the network is downloaded from the public Overpass API instead. That
works, but the public servers are often at capacity and reject automated clients, so the
local file is strongly recommended.

## Usage

### Web UI (recommended)

```bash
python K7-server.py
```

Then open <http://127.0.0.1:8707> in a browser. Type the start and end addresses,
choose how many routes you want and press *Find routes*. You get:

- a ranked list of routes with their length and the difference to the shortest one in meters,
- all routes drawn on an OpenStreetMap (or CyclOSM) background; hover or click to highlight one,
- a *Differences vs. #1* view that draws the shared part grey and only the differing sections in color,
- GPX download per route or for all routes at once,
- links that open the current map view on osm.org or in the iD editor,
- a local-optimality filter (*Max local stretch*): a candidate is rejected when, on any
  section where it leaves an already accepted route, it is more than the given percentage
  longer than the shortest connection of that section's end points (default 15 %). This
  removes pointless micro-detours such as looping around a roundabout splitter island or
  through a parking lot, while keeping genuinely parallel ways (cycle path vs. roadway,
  which differ by about 1 %). Rejected candidates are listed separately and can be shown
  on the map to calibrate the threshold; leave the field empty to disable the filter.

The street network for an area is built once (from the local extract, or downloaded from
Overpass) and cached in `graph_cache/`, so repeated queries in the same area take seconds.
The server only listens on localhost and needs no extra dependencies beyond the ones above.

### Command line

1. Open `K7-router.py` in a text editor
2. Edit the configuration variables at the top of `find_k_shortest_bike_routes()`:
   ```python
   START_ADDRESS = "Your start address"
   END_ADDRESS = "Your destination address"
   K_PATHS = 7  # Number of alternative routes to find
   BUFFER_DISTANCE = 500  # Search buffer in meters
   ```
3. Run the script:
   ```bash
   python K7-router.py
   ```
4. Output: GPX files named `route_1.gpx`, `route_2.gpx`, etc., ranked by distance

## License

MIT License — see [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Ideas for future development:
- CLI arguments for addresses
- Ranking independent local alternatives instead of full-route combinations (see the known limitation above)
- Cost functions beyond pure distance (elevation, surface type, traffic)
- Integration with BRouter profiles (see included `.brf` files)
