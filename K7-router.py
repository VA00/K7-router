#
# K-Shortest Bicycle Path Finder using OpenStreetMap
#
# This script finds the top K-shortest bicycle routes between two specified addresses,
# downloads the necessary map data using a point+radius approach, and saves
# each route as a separate GPX file.
#
# Required libraries: osmnx, networkx, gpxpy
# Install them using pip:
# pip install osmnx networkx gpxpy
#

import osmnx as ox
import networkx as nx
import gpxpy
import gpxpy.gpx
import itertools
import math
import numpy as np

def find_k_shortest_bike_routes():
    """
    Main function to find and save the K-shortest bicycle routes.
    """
    # --- Configuration ---
    START_ADDRESS = "Urbańczyka 1, Chrzanów, Poland"
    END_ADDRESS = "Krzywa 1, Płoki, Poland"
    K_PATHS = 13  # The number of shortest paths to find
    BUFFER_DISTANCE = 500  # Additional buffer in meters for safety

    # --- 1. Configure OSMnx ---
    print("Configuring OSMnx...")
    
    # OSMnx 2.x uses ox.settings (no ox.config)
    ox.settings.use_cache = True
    ox.settings.log_console = True
    ox.settings.timeout = 180
    ox.settings.overpass_rate_limit = True
    ox.settings.request_retries = 3
    
    # Set max query area size (use float for proper handling)
    ox.settings.max_query_area_size = 1e12  # 1 trillion m² (1 million km²)
   
    print(f"max_query_area_size = {ox.settings.max_query_area_size}")
    print("Configuration complete.")

    # --- 2. Geocode start and end points ---
    print(f"Geocoding start address: '{START_ADDRESS}'...")
    try:
        start_lat, start_lon = ox.geocode(START_ADDRESS)
        print(f" -> Found coordinates: ({start_lat}, {start_lon})")
    except ValueError:
        print(f"Error: Could not geocode the start address '{START_ADDRESS}'.")
        return

    print(f"Geocoding end address: '{END_ADDRESS}'...")
    try:
        end_lat, end_lon = ox.geocode(END_ADDRESS)
        print(f" -> Found coordinates: ({end_lat}, {end_lon})")
    except ValueError:
        print(f"Error: Could not geocode the end address '{END_ADDRESS}'.")
        return

    # --- 3. Calculate center point and search radius ---
    print("Calculating center point and search radius...")
    
    # Calculate center point between start and end
    center_lat = (start_lat + end_lat) / 2
    center_lon = (start_lon + end_lon) / 2
    
    # Calculate distance from center to furthest point, plus buffer
    def haversine_distance(lat1, lon1, lat2, lon2):
        """Calculate distance in meters between two lat/lon points"""
        R = 6371000  # Earth radius in meters
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    dist_to_start = haversine_distance(center_lat, center_lon, start_lat, start_lon)
    dist_to_end = haversine_distance(center_lat, center_lon, end_lat, end_lon)
    max_dist = max(dist_to_start, dist_to_end)
    
    # Add buffer distance for alternative routes
    search_dist = max_dist + BUFFER_DISTANCE
    
    print(f" -> Center point: ({center_lat:.6f}, {center_lon:.6f})")
    print(f" -> Search radius: {search_dist:.1f} meters")

    # --- 4. Download the street network for the area ---
    print("Downloading bicycle network data from OpenStreetMap...")
    try:
        G = ox.graph_from_point(
            center_point=(center_lat, center_lon),
            dist=search_dist,
            network_type='bike',
            simplify=True
        )
        print(f" -> Network data downloaded successfully ({len(G.nodes)} nodes, {len(G.edges)} edges)")
    except Exception as e:
        print(f"Error downloading or creating graph: {e}")
        return

    # --- 5. Find the nearest network nodes to the start and end points ---
    print("Finding nearest network nodes to start and end points...")
    
    # Find nearest nodes on UNPROJECTED graph (using lat/lon coordinates)
    # Note: This requires scikit-learn. To avoid this, you can manually find nodes
    # or use graph.get_nearest_node() with a distance threshold
    try:
        orig_node = ox.nearest_nodes(G, X=start_lon, Y=start_lat)
        dest_node = ox.nearest_nodes(G, X=end_lon, Y=end_lat)
    except ImportError:
        print("Installing scikit-learn is recommended for better performance.")
        print("Run: pip install scikit-learn --break-system-packages")
        print("\nUsing fallback method to find nearest nodes...")
        # Fallback: find closest node manually
        import numpy as np
        def find_nearest_node_manual(graph, lon, lat):
            min_dist = float('inf')
            nearest = None
            for node, data in graph.nodes(data=True):
                dist = np.sqrt((data['x'] - lon)**2 + (data['y'] - lat)**2)
                if dist < min_dist:
                    min_dist = dist
                    nearest = node
            return nearest
        
        orig_node = find_nearest_node_manual(G, start_lon, start_lat)
        dest_node = find_nearest_node_manual(G, end_lon, end_lat)
    
    print(f" -> Start node: {orig_node}, End node: {dest_node}")
    
    # Check if start and end are the same node
    if orig_node == dest_node:
        print("\n⚠️  Warning: Start and end addresses map to the same network node!")
        print("This happens when addresses are very close or on the same street segment.")
        print("The straight-line distance between your addresses is only ~220 meters.")
        print("\nTo find alternative routes, try addresses that are:")
        print("  • Further apart (at least 500+ meters)")
        print("  • On different major streets")
        print("  • In different neighborhoods")
        return
    
    # Project graph to UTM for accurate distance calculations in pathfinding
    print("Projecting graph for accurate distance calculations...")
    G_proj = ox.project_graph(G)

    # --- 6. Calculate the K-shortest paths based on 'length' ---
    print(f"Calculating the {K_PATHS} shortest paths by distance...")
    
    # Convert MultiDiGraph to DiGraph for pathfinding
    # This combines parallel edges by keeping the shortest one
    G_simple = ox.convert.to_digraph(G_proj, weight='length')
    
    try:
        paths_generator = nx.shortest_simple_paths(G_simple, source=orig_node, target=dest_node, weight='length')
        # Use itertools.islice to get the top K paths without computing all of them
        k_shortest_paths = list(itertools.islice(paths_generator, K_PATHS))
    except nx.NetworkXNoPath:
        print(f"No path could be found between the start and end points.")
        return
    
    if not k_shortest_paths:
        print("No routes found. The start and end points might be in disconnected parts of the network.")
        return

    print(f" -> Found {len(k_shortest_paths)} routes.")

    # --- 7. Generate and save GPX files for each route ---
    for i, path in enumerate(k_shortest_paths):
        gpx = gpxpy.gpx.GPX()

        # Create first track in our GPX file
        gpx_track = gpxpy.gpx.GPXTrack()
        gpx_track.name = f"Route {i + 1}" 
        gpx.tracks.append(gpx_track)

        # Create first segment in our GPX track
        gpx_segment = gpxpy.gpx.GPXTrackSegment()
        gpx_track.segments.append(gpx_segment)

        # Add points to the segment (using original unprojected graph for lat/lon)
        for node_id in path:
            node = G.nodes[node_id]
            gpx_segment.points.append(gpxpy.gpx.GPXTrackPoint(latitude=node['y'], longitude=node['x']))
        
        # Calculate total distance for logging (manually sum edge lengths)
        route_length = 0
        for u, v in zip(path[:-1], path[1:]):
            # Get edge data from the simple graph
            edge_data = G_simple[u][v]
            route_length += edge_data.get('length', 0)
        
        # Save the GPX file
        filename = f"route_{i + 1}.gpx"
        with open(filename, 'w') as f:
            f.write(gpx.to_xml())
        
        print(f"Saved route {i + 1} to '{filename}' (Distance: {route_length / 1000:.3f} km)")
    
    print("\nProcess finished successfully.")


if __name__ == "__main__":
    find_k_shortest_bike_routes()
