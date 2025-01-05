import requests
import time
import csv
from typing import Dict, List, Tuple

def check_amenities_near_settlement(lat: float, lon: float, radius: int = 500) -> Dict:
    """
    Check for shops and campsites near a given coordinate using Overpass API.
    
    Args:
        lat: Latitude of the settlement
        lon: Longitude of the settlement
        radius: Search radius in meters (default 500m)
    
    Returns:
        Dictionary with shop and campsite locations
    """
    overpass_url = "https://overpass-api.de/api/interpreter"
    
    shop_types = (
        "supermarket|convenience|grocery|general|food|bakery|butcher"
        "|greengrocer|marketplace|mall|department_store"
    )
    
    overpass_query = f"""
    [out:json][timeout:25];
    (
      // Search for various types of shops
      way(around:{radius},{lat},{lon})["shop"~"{shop_types}"];
      node(around:{radius},{lat},{lon})["shop"~"{shop_types}"];
      
      // Search for campsites
      way(around:{radius},{lat},{lon})["tourism"="camp_site"];
      node(around:{radius},{lat},{lon})["tourism"="camp_site"];
    );
    out center body;
    >;
    out skel qt;
    """
    
    try:
        response = requests.post(overpass_url, data={"data": overpass_query})
        response.raise_for_status()
        data = response.json()
        
        elements = data.get("elements", [])
        
        shops = []
        campsites = []
        
        for element in elements:
            # Get coordinates - for ways, use center coordinates
            if element["type"] == "way" and "center" in element:
                coords = (element["center"]["lat"], element["center"]["lon"])
            elif element["type"] == "node":
                coords = (element["lat"], element["lon"])
            else:
                continue
                
            # Store name if available
            name = element.get("tags", {}).get("name", "Unnamed")
            
            # Check if it's a shop or campsite
            if "shop" in element.get("tags", {}):
                shop_type = element["tags"]["shop"]
                shops.append({
                    "name": name,
                    "type": shop_type,
                    "coords": coords,
                    "maps_link": f"https://www.google.com/maps?q={coords[0]},{coords[1]}"
                })
            
            if element.get("tags", {}).get("tourism") == "camp_site":
                campsites.append({
                    "name": name,
                    "coords": coords,
                    "maps_link": f"https://www.google.com/maps?q={coords[0]},{coords[1]}"
                })
        
        time.sleep(0.5)  # Reduced wait time to 0.5 seconds
        
        return {
            "shops": shops,
            "campsites": campsites
        }
        
    except Exception as e:
        print(f"Error querying Overpass API: {str(e)}")
        return {
            "shops": [],
            "campsites": []
        }

def create_html_report(results: List[Dict], output_file: str) -> None:
    """
    Create an HTML report from the amenities results.
    
    Args:
        results: List of dictionaries containing settlement and amenity data
        output_file: Path to the output HTML file
    """
    html_template = """<!DOCTYPE html>
<html>
<head>
    <title>Settlements POI Report</title>
    <style>
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid black; padding: 8px; text-align: left; }}
        th {{ background-color: #ddd; }}
        .found {{ color: green; }}
        .not-found {{ color: red; }}
    </style>
</head>
<body>
    <h1>Settlements POI Report</h1>
    <table>
        <tr>
            <th>Settlement</th>
            <th>Location</th>
            <th>Shops</th>
            <th>Campsites</th>
        </tr>
        {rows}
    </table>
</body>
</html>"""
    
    rows = []
    for result in results:
        # Create location link
        coords = result['Coordinates'].strip('"')
        location_link = f'<a href="https://www.google.com/maps?q={coords}" target="_blank">{coords}</a>'
        
        # Format shops information
        shops_cell = '<span class="not-found">No shops found</span>'
        if result['Has Shop']:
            shops = result['Shop Details'].split('; ')
            shop_links = result['Shop Links'].split('; ')
            shops_list = []
            for shop, link in zip(shops, shop_links):
                shops_list.append(f'<a href="{link}" target="_blank">{shop}</a>')
            shops_cell = f'<span class="found">{len(shops)} found:</span><br>' + '<br>'.join(shops_list)
        
        # Format campsites information
        campsites_cell = '<span class="not-found">No campsites found</span>'
        if result['Has Campsite']:
            campsites = result['Campsite Details'].split('; ')
            campsite_links = result['Campsite Links'].split('; ')
            campsites_list = []
            for campsite, link in zip(campsites, campsite_links):
                campsites_list.append(f'<a href="{link}" target="_blank">{campsite}</a>')
            campsites_cell = f'<span class="found">{len(campsites)} found:</span><br>' + '<br>'.join(campsites_list)
        
        locality = result.get('Locality', '').strip('"')
        locality_text = f'<br>{locality}' if locality else ''
        
        row = f'<tr><td>{result["Name"]}{locality_text}</td><td>{location_link}</td><td>{shops_cell}</td><td>{campsites_cell}</td></tr>'
        rows.append(row)
    
    # Create the complete HTML
    html_content = html_template.format(rows=''.join(rows))
    
    # Write the HTML file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"HTML report written to {output_file}")

def check_amenities_for_all_settlements(settlements_file: str, output_csv: str, output_html: str) -> None:
    """
    Process all settlements from a CSV file and check for nearby amenities.
    Write results to CSV and HTML files.
    
    Args:
        settlements_file: Path to the input CSV file containing settlements
        output_csv: Path to the output CSV file
        output_html: Path to the output HTML file
    """
    try:
        with open(settlements_file, 'r', newline='') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        results = []
        for row in rows:
            name = row['Name']
            coords_str = row['Coordinates'].strip('"')
            lat, lon = map(float, coords_str.split(','))
            
            print(f"Checking amenities for {name}...")
            amenities = check_amenities_near_settlement(lat, lon)
            
            new_row = dict(row)
            new_row['Has Shop'] = bool(amenities['shops'])
            new_row['Shop Details'] = '; '.join(
                f"{shop['name']} ({shop['type']}) at {shop['coords'][0]},{shop['coords'][1]}"
                for shop in amenities['shops']
            ) if amenities['shops'] else ''
            new_row['Shop Links'] = '; '.join(
                shop['maps_link'] for shop in amenities['shops']
            ) if amenities['shops'] else ''
            
            new_row['Has Campsite'] = bool(amenities['campsites'])
            new_row['Campsite Details'] = '; '.join(
                f"{camp['name']} at {camp['coords'][0]},{camp['coords'][1]}"
                for camp in amenities['campsites']
            ) if amenities['campsites'] else ''
            new_row['Campsite Links'] = '; '.join(
                camp['maps_link'] for camp in amenities['campsites']
            ) if amenities['campsites'] else ''
            
            results.append(new_row)
        
        # Write CSV file
        with open(output_csv, 'w', newline='') as f:
            base_fieldnames = [f for f in results[0].keys() if f not in [
                'Has Shop', 'Shop Details', 'Shop Links',
                'Has Campsite', 'Campsite Details', 'Campsite Links'
            ]]
            fieldnames = base_fieldnames + [
                'Has Shop', 'Shop Details', 'Shop Links',
                'Has Campsite', 'Campsite Details', 'Campsite Links'
            ]
            
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        
        # Create HTML report
        create_html_report(results, output_html)
                
        print(f"Results written to {output_csv} and {output_html}")
            
    except Exception as e:
        print(f"Error processing settlements file: {str(e)}")
        raise

if __name__ == "__main__":
    check_amenities_for_all_settlements(
        'settlements.csv',
        'settlements_poi.csv',
        'settlements_poi.html'
    )
