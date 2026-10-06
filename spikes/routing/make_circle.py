# /// script
# requires-python = ">=3.12"
# ///
"""Write a GeoJSON circle polygon for osmium extract. Usage: uv run make_circle.py LAT LON RADIUS_KM OUT.geojson"""
import json, math, sys
lat0, lon0, r = map(float, sys.argv[1:4])
pts = [[round(lon0 + r * math.cos(a) / (111.32 * math.cos(math.radians(lat0))), 5), round(lat0 + r * math.sin(a) / 111.32, 5)]
       for a in (2 * math.pi * i / 64 for i in range(64))]
pts.append(pts[0])
json.dump({"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [pts]}}, open(sys.argv[4], "w"))
