import gpxpy
import osmnx as ox
import geopandas as gpd
from shapely.geometry import LineString, Point
import pandas as pd
from typing import Dict, List
import time
import logging
from gpx_functions import load_gpx_route, create_route_buffer
import requests
import folium
from folium import plugins

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('debug_log.txt', mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_pois_along_route(buffer_area: gpd.GeoDataFrame) -> Dict[str, gpd.GeoDataFrame]:
    """Query OSM for POIs within the route buffer"""
    try:
        # Define tags for shops and campsites
        shop_tags = {
            'shop': [
                'supermarket', 'convenience', 'grocery',
                'general', 'food', 'bakery', 'butcher',
                'greengrocer', 'marketplace', 'mall',
                'department_store'
            ]
        }
        
        campsite_tags = {
            'tourism': ['camp_site', 'caravan_site']
        }

        # Add delay to respect rate limits
        time.sleep(1)
        
        # Get shops
        logger.info("Fetching shops...")
        shops = ox.features_from_polygon(
            buffer_area.geometry.iloc[0],
            tags=shop_tags
        )
        
        time.sleep(1)
        
        # Get campsites
        logger.info("Fetching campsites...")
        campsites = ox.features_from_polygon(
            buffer_area.geometry.iloc[0],
            tags=campsite_tags
        )
        
        return {
            'shops': shops,
            'campsites': campsites
        }
        
    except Exception as e:
        logger.error(f"Error getting POIs: {e}")
        return {'shops': gpd.GeoDataFrame(), 'campsites': gpd.GeoDataFrame()}

def process_pois(pois: Dict[str, gpd.GeoDataFrame], route: LineString, buffer_area: gpd.GeoDataFrame) -> List[Dict]:
    """Process POIs into a standardized format"""
    processed = []
    
    # Process shops
    for idx, row in pois['shops'].iterrows():
        try:
            poi = process_single_poi(row, 'shop', route, buffer_area)
            if poi:
                processed.append(poi)
        except Exception as e:
            logger.warning(f"Error processing shop: {e}")
            continue
    
    # Process campsites
    for idx, row in pois['campsites'].iterrows():
        try:
            poi = process_single_poi(row, 'campsite', route, buffer_area)
            if poi:
                processed.append(poi)
        except Exception as e:
            logger.warning(f"Error processing campsite: {e}")
            continue
    
    # Sort by distance along route
    processed.sort(key=lambda x: x['distance_km'])
    
    return processed

def process_single_poi(row: pd.Series, poi_type: str, route: LineString, buffer_area: gpd.GeoDataFrame) -> Dict:
    """Process a single POI into standardized format"""
    try:
        name = row.get('name', 'Unnamed')
        
        # Get coordinates
        if isinstance(row.geometry, Point):
            point = row.geometry
        else:
            point = row.geometry.centroid
            
        coords = (point.y, point.x)  # lat, lon
            
        # Calculate distance along route
        distance_km = calculate_distance_along_route(point, route)
        
        # Get elevation
        elevation = get_elevation(coords[0], coords[1])
        
        # Get nearest settlement
        nearest = get_nearest_settlement(point, buffer_area)
            
        # Get specific type
        specific_type = (
            row.get('shop') if poi_type == 'shop'
            else row.get('tourism')
        )
        
        # Create Google Maps link
        maps_link = f"https://www.google.com/maps?q={coords[0]},{coords[1]}"
        
        return {
            'name': name,
            'type': poi_type,
            'specific_type': specific_type,
            'coords': coords,
            'elevation': elevation,
            'distance_km': distance_km,
            'nearest_settlement': nearest['name'],
            'settlement_type': nearest['type'],
            'settlement_distance': nearest['distance'],
            'maps_link': maps_link,
            'all_tags': dict(row)
        }
    except Exception as e:
        logger.warning(f"Error processing POI {name}: {e}")
        return None

def calculate_distance_along_route(point: Point, route: LineString) -> float:
    """Calculate the distance along the route to the nearest point to the POI"""
    try:
        # Convert to UTM for accurate distance measurement
        route_gdf = gpd.GeoDataFrame(geometry=[route], crs="EPSG:4326")
        point_gdf = gpd.GeoDataFrame(geometry=[point], crs="EPSG:4326")
        
        # Convert both to same UTM zone
        utm_crs = route_gdf.estimate_utm_crs()
        route_utm = route_gdf.to_crs(utm_crs).geometry.iloc[0]
        point_utm = point_gdf.to_crs(utm_crs).geometry.iloc[0]
        
        # Find the nearest point on the route
        nearest_point_dist = route_utm.project(point_utm)
        
        # Get the total length for verification
        total_length = route_utm.length
        
        # Ensure we don't return a distance beyond the route length
        distance = min(nearest_point_dist, total_length)
        
        # Convert to km and round to 2 decimal places
        distance_km = round(distance / 1000, 2)
        
        # Log some debug info
        logger.debug(f"Point: {point.coords[0]}")
        logger.debug(f"Distance along route: {distance_km}km")
        logger.debug(f"Total route length: {total_length/1000:.2f}km")
        
        return distance_km
        
    except Exception as e:
        logger.warning(f"Error calculating distance: {e}")
        return 0.0

def find_pois_along_route(gpx_file: str, buffer_distance: float = 500) -> tuple[List[Dict], LineString]:
    """Main function to find POIs along a GPX route"""
    try:
        # Load and process the route
        route = load_gpx_route(gpx_file)
        
        # Log route length
        route_gdf = gpd.GeoDataFrame(geometry=[route], crs="EPSG:4326")
        utm_crs = route_gdf.estimate_utm_crs()
        route_utm = route_gdf.to_crs(utm_crs).geometry.iloc[0]
        total_length_km = route_utm.length / 1000
        logger.info(f"Route length: {total_length_km:.2f}km")
        
        # Create a buffer for the entire route
        logger.info("Creating buffer for route...")
        buffer_area = create_route_buffer(route, buffer_distance)
        
        # Get all POIs in one go
        logger.info("Fetching POIs...")
        pois = get_pois_along_route(buffer_area)
        
        # Process the POIs
        processed_pois = process_pois(pois, route, buffer_area)
        
        if not processed_pois:
            logger.warning("No POIs found along route")
            return [], route
            
        logger.info(f"Found {len(processed_pois)} POIs")
        return processed_pois, route
    
    except Exception as e:
        logger.error(f"Error in main processing: {e}")
        raise

def save_results(pois: List[Dict], route: LineString, csv_file: str, html_file: str):
    """Save results to CSV and HTML files"""
    try:
        # Prepare data for DataFrame
        df_data = []
        for poi in pois:
            lat, lon = poi['coords']
            # Format elevation without brackets
            elevation_str = f"{poi['elevation']}m" if poi['elevation'] is not None else "Unknown"
            nearest_settlement = f"{poi['nearest_settlement']} ({poi['settlement_type']}, {poi['settlement_distance']}km)"
            
            df_data.append({
                'Distance': poi['distance_km'],
                'Name': poi['name'],
                'Type': poi['specific_type'],
                'Category': poi['type'],
                'Elevation': elevation_str,
                'Nearest Settlement': nearest_settlement,
                'Coordinates': f"{lat}, {lon}",
                'Google Maps': poi['maps_link'],
                'coords': poi['coords']
            })
        
        # Save to CSV with string formatting
        df = pd.DataFrame(df_data)
        csv_columns = ['Distance', 'Name', 'Type', 'Category', 'Elevation', 'Nearest Settlement', 'Coordinates', 'Google Maps']
        df[csv_columns].to_csv(csv_file, index=False, quoting=1)  # Use subset of columns for CSV
        logger.info(f"Results saved to {csv_file}")
        
        # Create HTML with route
        html_content = create_html_report(df_data, route)
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"HTML report saved to {html_file}")
        
    except Exception as e:
        logger.error(f"Error saving results: {e}")
        raise

def create_html_report(data: List[Dict], route: LineString) -> str:
    """Create HTML report from POI data with interactive map"""
    html_template = """<!DOCTYPE html>
<html>
<head>
    <title>Route POI Report</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; display: flex; }}
        #sidebar {{ width: 40%; height: 100vh; overflow-y: auto; padding: 20px; box-sizing: border-box; }}
        #map {{ width: 60%; height: 100vh; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        .shop-supermarket {{ background-color: #e3f2fd; }}
        .shop-convenience {{ background-color: #bbdefb; }}
        .shop-other {{ background-color: #90caf9; }}
        .campsite {{ background-color: #c8e6c9; }}
        .distance {{ font-weight: bold; }}
        .elevation {{ color: #805ad5; }}
        .poi-row {{ cursor: pointer; }}
        .poi-row:hover {{ background-color: #f5f5f5; }}
        a {{ color: #2b6cb0; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div id="sidebar">
        <h1>POIs Along Route</h1>
        <table>
            <tr>
                <th>Distance (km)</th>
                <th>Name</th>
                <th>Type</th>
                <th>Elevation</th>
                <th>Nearest Settlement</th>
            </tr>
            {rows}
        </table>
    </div>
    <div id="map"></div>
    <script>
        // Initialize the map
        var map = L.map('map');
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '© OpenStreetMap contributors'
        }}).addTo(map);

        // Add route
        var routeCoords = {route_coords};
        var routeLine = L.polyline(routeCoords, {{color: 'blue', weight: 3}}).addTo(map);
        
        // Add markers
        var markers = {markers_data};
        markers.forEach(function(marker) {{
            L.marker([marker.lat, marker.lon], {{
                icon: L.icon({{
                    iconUrl: marker.icon,
                    iconSize: [25, 41],
                    iconAnchor: [12, 41],
                    popupAnchor: [1, -34]
                }})
            }})
            .bindPopup(marker.popup)
            .addTo(map);
        }});

        // Fit map to route
        map.fitBounds(routeLine.getBounds());

        // Zoom to POI function
        function zoomToPOI(lat, lon) {{
            map.setView([lat, lon], 15);
        }}
    </script>
</body>
</html>"""

    # Prepare route coordinates
    route_coords = [[y, x] for x, y in route.coords]
    
    # Prepare markers data
    markers_data = []
    for item in data:
        lat, lon = item['coords']
        icon_url = ('https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/'
                   f"marker-icon-{'green' if item['Category'] == 'campsite' else 'red'}.png")
        
        popup_html = f"""
            <b>{item['Name']}</b><br>
            Type: {item['Type']}<br>
            Distance: {item['Distance']}km<br>
            Elevation: {item['Elevation']}<br>
            <a href="{item['Google Maps']}" target="_blank">View in Google Maps</a>
        """
        
        markers_data.append({
            'lat': lat,
            'lon': lon,
            'icon': icon_url,
            'popup': popup_html.replace('"', '\\"').replace('\n', '')
        })

    # Generate rows
    rows = []
    for item in data:
        lat, lon = item['coords']
        row_class = 'campsite' if item['Category'] == 'campsite' else f"shop-{item['Type']}"
        
        row = f"""
        <tr class="poi-row {row_class}" onclick="zoomToPOI({lat}, {lon})">
            <td class="distance">{item['Distance']} km</td>
            <td>{item['Name']}</td>
            <td>{item['Type']}</td>
            <td class="elevation">{item['Elevation']}</td>
            <td>{item['Nearest Settlement']}</td>
        </tr>"""
        rows.append(row)

    # Replace placeholders in template
    html_content = html_template.format(
        rows=''.join(rows),
        route_coords=str(route_coords),
        markers_data=str(markers_data)
    )
    
    return html_content

def get_elevation(lat: float, lon: float) -> float:
    """Fetch elevation data using open-meteo API"""
    try:
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data['elevation']
        return None
    except Exception as e:
        logger.warning(f"Error getting elevation: {e}")
        return None

def get_nearest_settlement(point: Point, buffer_area: gpd.GeoDataFrame) -> Dict:
    """Find the nearest settlement (village or larger) to a point"""
    try:
        # Query for settlements
        settlement_tags = {
            'place': ['city', 'town', 'village']
        }
        
        settlements = ox.features_from_polygon(
            buffer_area.geometry.iloc[0],
            tags=settlement_tags
        )
        
        if settlements.empty:
            return {'name': 'Unknown', 'type': 'Unknown', 'distance': 0}
        
        # Convert settlements to same CRS as point for distance calculation
        point_gdf = gpd.GeoDataFrame(geometry=[point], crs="EPSG:4326")
        utm_crs = point_gdf.estimate_utm_crs()
        point_utm = point_gdf.to_crs(utm_crs)
        settlements_utm = settlements.to_crs(utm_crs)
        
        # Calculate distances to all settlements
        distances = settlements_utm.geometry.distance(point_utm.geometry.iloc[0])
        nearest_idx = distances.idxmin()
        nearest = settlements.loc[nearest_idx]
        
        return {
            'name': nearest.get('name', 'Unknown'),
            'type': nearest.get('place', 'Unknown'),
            'distance': round(distances[nearest_idx] / 1000, 2)  # Convert to km
        }
        
    except Exception as e:
        logger.warning(f"Error finding nearest settlement: {e}")
        return {'name': 'Unknown', 'type': 'Unknown', 'distance': 0}

def main():
    try:
        gpx_file = "gpx_test.gpx"
        logger.info(f"Processing GPX file: {gpx_file}")
        
        # Find POIs and get route
        pois, route = find_pois_along_route(gpx_file, buffer_distance=500)
        
        if pois:
            # Save results
            save_results(
                pois,
                route,
                'route_pois.csv',
                'route_pois.html'
            )
            
            # Console output
            print(f"\nFound {len(pois)} POIs along route:")
            for poi in pois:
                print(f"\n{poi['name']} ({poi['specific_type']})")
                print(f"Location: {poi['maps_link']}")
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    main()
