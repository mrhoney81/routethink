import gpxpy
import osmnx as ox
import geopandas as gpd
from shapely.geometry import LineString, Point
import pandas as pd
from typing import List, Dict, Tuple
import time
from math import ceil
import logging
import requests

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('debug_log.txt', mode='w', encoding='utf-8'),  # Add UTF-8 encoding
        logging.StreamHandler()  # Keep console output
    ]
)
logger = logging.getLogger(__name__)

def load_gpx_route(gpx_file: str) -> LineString:
    """Load GPX file and convert to LineString"""
    try:
        with open(gpx_file, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.longitude, point.latitude))
        
        if not points:
            raise ValueError("No points found in GPX file")
            
        return LineString(points)
    except Exception as e:
        logger.error(f"Error loading GPX file: {e}")
        raise

def split_route(route: LineString, chunk_size: float = 50000) -> List[LineString]:
    """Split route into chunks of specified size in meters with overlap"""
    try:
        # Convert to UTM for accurate distance measurement
        route_gdf = gpd.GeoDataFrame(geometry=[route], crs="EPSG:4326")
        utm_crs = route_gdf.estimate_utm_crs()
        route_utm = route_gdf.to_crs(utm_crs)
        
        # Get total length
        total_length = route_utm.geometry.iloc[0].length
        
        # Calculate number of chunks
        n_chunks = ceil(total_length / chunk_size)
        
        # Add 1km overlap between chunks
        overlap = 1000
        
        chunks = []
        for i in range(n_chunks):
            start = max(0, i * chunk_size - overlap)
            end = min(total_length, (i + 1) * chunk_size + overlap)
            
            chunk = cut_line_at_distance(route_utm.geometry.iloc[0], start, end)
            # Convert chunk to GeoDataFrame before changing CRS
            chunk_gdf = gpd.GeoDataFrame(geometry=[chunk], crs=utm_crs)
            chunks.append(chunk_gdf.to_crs("EPSG:4326").geometry.iloc[0])
            
        return chunks
    except Exception as e:
        logger.error(f"Error splitting route: {e}")
        raise

def cut_line_at_distance(line: LineString, start_dist: float, end_dist: float) -> LineString:
    """Cut a line at specified distances"""
    if start_dist < 0:
        start_dist = 0
    if end_dist > line.length:
        end_dist = line.length
        
    coords = []
    current_dist = 0
    
    for i in range(len(line.coords) - 1):
        p1 = Point(line.coords[i])
        p2 = Point(line.coords[i + 1])
        segment = LineString([p1, p2])
        segment_length = segment.length
        
        if current_dist + segment_length < start_dist:
            current_dist += segment_length
            continue
            
        if current_dist > end_dist:
            break
            
        coords.append(line.coords[i])
        
        current_dist += segment_length
    
    if coords:
        coords.append(line.coords[-1])
    return LineString(coords)

def create_route_buffer(route: LineString, buffer_distance: float = 500) -> gpd.GeoDataFrame:
    """Create a buffer around the route in meters"""
    try:
        route_gdf = gpd.GeoDataFrame(geometry=[route], crs="EPSG:4326")
        route_utm = route_gdf.to_crs(route_gdf.estimate_utm_crs())
        buffered = route_utm.buffer(buffer_distance)
        return buffered.to_crs("EPSG:4326")
    except Exception as e:
        logger.error(f"Error creating route buffer: {e}")
        raise

def get_settlements_with_rate_limit(buffer_area: gpd.GeoDataFrame) -> List[Dict]:
    """Query OSM for settlements with rate limiting"""
    try:
        time.sleep(1)
        
        # First get settlements
        tags = {
            'place': ['city', 'town', 'village']
        }
        
        settlements = ox.features_from_polygon(
            buffer_area.geometry.iloc[0],
            tags=tags
        )
        
        # Then get administrative boundaries
        admin_tags = {
            'boundary': 'administrative',
            'admin_level': ['4', '6']  # 4 is typically county, 6 is typically district
        }
        
        admin_areas = ox.features_from_polygon(
            buffer_area.geometry.iloc[0],
            tags=admin_tags
        )
        
        return process_settlements(settlements, admin_areas)
    except Exception as e:
        logger.error(f"Error getting settlements: {e}")
        return []

def process_settlements(settlements: gpd.GeoDataFrame, admin_areas: gpd.GeoDataFrame) -> List[Dict]:
    """Process settlements into desired format with enhanced location info"""
    processed = []
    
    for idx, row in settlements.iterrows():
        try:
            name = row.get('name', 'Unknown')
            place_type = row.get('place', 'Unknown')
            
            # Get coordinates
            if isinstance(row.geometry, Point):
                coords = (row.geometry.x, row.geometry.y)
                point = row.geometry
            else:
                point = row.geometry.centroid
                coords = (point.x, point.y)
            
            # Find which admin areas contain this point
            locality = 'Unknown'
            country = 'Unknown'
            
            for _, admin_row in admin_areas.iterrows():
                if admin_row.geometry.contains(point):
                    admin_level = admin_row.get('admin_level')
                    if admin_level == '4':  # County level
                        locality = admin_row.get('name', locality)
                    elif admin_level == '2':  # Country level
                        country = admin_row.get('name', country)
            
            # Fallback to existing tags if admin areas didn't work
            if locality == 'Unknown':
                locality = (
                    row.get('addr:county') or
                    row.get('is_in:county') or
                    row.get('is_in') or
                    row.get('addr:state') or
                    row.get('is_in:state') or
                    row.get('addr:district') or
                    'Unknown'
                )
            
            if country == 'Unknown':
                country = (
                    row.get('addr:country') or
                    row.get('is_in:country') or
                    'United Kingdom'  # Default for UK locations
                )
            
            processed.append({
                'name': name,
                'type': place_type,
                'coords': coords,
                'locality': locality,
                'country': country,
                'full_name': f"{name}, {locality}, {country}",
                'all_tags': dict(row)
            })
        except Exception as e:
            logger.warning(f"Error processing settlement {name}: {e}")
            continue
    
    return processed

def find_settlements_along_route(gpx_file: str, 
                               buffer_distance: float = 500,
                               chunk_size: float = 50000) -> List[Dict]:
    """Main function to find settlements along a GPX route"""
    try:
        # Load and process the route
        route = load_gpx_route(gpx_file)
        
        # Create a single buffer for the entire route
        logger.info("Creating buffer for entire route...")
        buffer_area = create_route_buffer(route, buffer_distance)
        
        # Get all settlements in one API call
        logger.info("Fetching settlements...")
        settlements = get_settlements_with_rate_limit(buffer_area)
        
        if not settlements:
            logger.warning("No settlements found along route")
            return []
            
        logger.info(f"Found {len(settlements)} settlements")
        return settlements
    
    except Exception as e:
        logger.error(f"Error in main processing: {e}")
        raise

def get_elevation(lat: float, lon: float) -> float:
    """Fetch elevation data using alternative sources"""
    try:
        # Try multiple elevation APIs in order
        apis = [
            f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}",
            f"https://api.opentopodata.org/v1/srtm90m?locations={lat},{lon}"
        ]
        
        for url in apis:
            try:
                response = requests.get(url, timeout=5)  # 5 second timeout
                if response.status_code == 200:
                    data = response.json()
                    # Handle different API response formats
                    if 'elevation' in data:  # open-meteo format
                        return data['elevation']
                    elif 'results' in data:  # opentopodata format
                        return data['results'][0]['elevation']
            except Exception as e:
                logger.debug(f"API attempt failed: {e}")
                continue
                
        logger.warning("All elevation APIs failed")
        return None
        
    except Exception as e:
        logger.warning(f"Error getting elevation: {e}")
        return None

def main():
    try:
        gpx_file = "gpx_test.gpx"
        logger.debug(f"Starting processing for GPX file: {gpx_file}")
        
        settlements = find_settlements_along_route(
            gpx_file,
            buffer_distance=500,
            chunk_size=50000
        )
        
        if not settlements:
            logger.warning("No settlements found along route")
            return
            
        logger.info(f"Found {len(settlements)} unique settlements")
        logger.debug("Settlement details:")
        
        # Sort by settlement type
        type_order = {'city': 0, 'town': 1, 'village': 2}
        settlements.sort(key=lambda x: type_order.get(x['type'], 999))
        
        # Create a list of dictionaries with just the fields we want
        output_data = []
        for s in settlements:
            # Swap longitude and latitude for Google Maps format
            lat, lon = s['coords'][1], s['coords'][0]
            
            # Get elevation data with delay between requests
            elevation = get_elevation(lat, lon)
            time.sleep(1)  # Add delay between elevation requests
            elevation_str = f"{elevation}m" if elevation is not None else "Unknown"
            
            # Create Google Maps link
            maps_link = f"https://www.google.com/maps?q={lat},{lon}"
            
            settlement_data = {
                'Name': s['name'],
                'Locality': s['locality'],
                'Country': s['country'],
                'Type': s['type'],
                'Coordinates': f"{lat}, {lon}",
                'Elevation': elevation_str,
                'Google Maps': maps_link
            }
            output_data.append(settlement_data)
            
            # Only log non-null values to reduce debug log noise
            logger.debug(f"Settlement: {settlement_data}")
            logger.debug(f"All tags for {s['name']}:")
            for key, value in s['all_tags'].items():
                if value is not None and value != 'nan' and key != 'geometry':
                    logger.debug(f"  {key}: {value}")
        
        # Convert to DataFrame and save to CSV
        df = pd.DataFrame(output_data)
        output_file = 'settlements.csv'
        df.to_csv(output_file, index=False)
        logger.info(f"Settlements saved to {output_file}")
        
        # Console output for quick review
        for settlement in settlements:
            lat, lon = settlement['coords'][1], settlement['coords'][0]  # Swap order
            print(f"Found: {settlement['full_name']}")
            print(f"Type: {settlement['type']}")
            print(f"Coordinates: ({lat}, {lon})")
            print("---")
            
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    main()