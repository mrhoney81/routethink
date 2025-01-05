import gpxpy
import osmnx as ox
import geopandas as gpd
from shapely.geometry import LineString, Point
import pandas as pd
from typing import Dict, List
import time
import logging
from gpx_functions import load_gpx_route, create_route_buffer

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

def process_pois(pois: Dict[str, gpd.GeoDataFrame], route: LineString) -> List[Dict]:
    """Process POIs into a standardized format"""
    processed = []
    
    # Process shops
    for idx, row in pois['shops'].iterrows():
        try:
            poi = process_single_poi(row, 'shop', route)
            if poi:
                processed.append(poi)
        except Exception as e:
            logger.warning(f"Error processing shop: {e}")
            continue
    
    # Process campsites
    for idx, row in pois['campsites'].iterrows():
        try:
            poi = process_single_poi(row, 'campsite', route)
            if poi:
                processed.append(poi)
        except Exception as e:
            logger.warning(f"Error processing campsite: {e}")
            continue
    
    # Sort by distance along route
    processed.sort(key=lambda x: x['distance_km'])
    
    return processed

def process_single_poi(row: pd.Series, poi_type: str, route: LineString) -> Dict:
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
            'distance_km': distance_km,
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

def find_pois_along_route(gpx_file: str, buffer_distance: float = 500) -> List[Dict]:
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
        processed_pois = process_pois(pois, route)
        
        if not processed_pois:
            logger.warning("No POIs found along route")
            return []
            
        logger.info(f"Found {len(processed_pois)} POIs")
        return processed_pois
    
    except Exception as e:
        logger.error(f"Error in main processing: {e}")
        raise

def save_results(pois: List[Dict], csv_file: str, html_file: str):
    """Save results to CSV and HTML files"""
    try:
        # Prepare data for DataFrame
        df_data = []
        for poi in pois:
            lat, lon = poi['coords']
            df_data.append({
                'Distance': poi['distance_km'],
                'Name': poi['name'],
                'Type': poi['specific_type'],
                'Category': poi['type'],
                'Coordinates': f"{lat}, {lon}",
                'Google Maps': poi['maps_link']
            })
        
        # Save to CSV
        df = pd.DataFrame(df_data)
        df.to_csv(csv_file, index=False)
        logger.info(f"Results saved to {csv_file}")
        
        # Create HTML
        html_content = create_html_report(df_data)
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"HTML report saved to {html_file}")
        
    except Exception as e:
        logger.error(f"Error saving results: {e}")
        raise

def create_html_report(data: List[Dict]) -> str:
    """Create HTML report from POI data"""
    html_template = """<!DOCTYPE html>
<html>
<head>
    <title>Route POI Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        .shop {{ color: #2c5282; }}
        .campsite {{ color: #276749; }}
        .distance {{ font-weight: bold; }}
    </style>
</head>
<body>
    <h1>POIs Along Route</h1>
    <table>
        <tr>
            <th>Distance (km)</th>
            <th>Name</th>
            <th>Type</th>
            <th>Location</th>
        </tr>
        {rows}
    </table>
</body>
</html>"""

    rows = []
    for item in data:
        category_class = 'shop' if item['Category'] == 'shop' else 'campsite'
        row = f"""
        <tr>
            <td class="distance">{item['Distance']} km</td>
            <td class="{category_class}">{item['Name']}</td>
            <td>{item['Type']}</td>
            <td><a href="{item['Google Maps']}" target="_blank">{item['Coordinates']}</a></td>
        </tr>"""
        rows.append(row)
    
    return html_template.format(rows=''.join(rows))

def main():
    try:
        gpx_file = "gpx_test.gpx"
        logger.info(f"Processing GPX file: {gpx_file}")
        
        # Find POIs
        pois = find_pois_along_route(gpx_file, buffer_distance=500)
        
        if pois:
            # Save results
            save_results(
                pois,
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
