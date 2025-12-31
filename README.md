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

## Requirements

- Python 3.8+
- [osmnx](https://github.com/gboeing/osmnx)
- [networkx](https://networkx.org/)
- [gpxpy](https://github.com/tkrajina/gpxpy)

## Installation

```bash
pip install osmnx networkx gpxpy
```

## Usage

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
- Web interface or CLI arguments for addresses
- Cost functions beyond pure distance (elevation, surface type, traffic)
- Integration with BRouter profiles (see included `.brf` files)
