
import streamlit as st
import json
import os
import tempfile
import zipfile
from io import BytesIO
import geopandas as gpd
from shapely.geometry import shape, box, LineString
from shapely.validation import explain_validity
from shapely.ops import split as shapely_split
from geojson_rewind import rewind

st.set_page_config(page_title="GeoJSON Splitter", layout="wide")
st.title("GeoJSON polygon splitting")

# ----------------------- Logic -----------------------
def count_vertices(geometry):
    if geometry.is_empty:
        return 0
    if geometry.geom_type == 'Polygon':
        return sum(len(ring.coords) for ring in [geometry.exterior] + list(geometry.interiors))
    elif geometry.geom_type == 'MultiPolygon':
        return sum(count_vertices(poly) for poly in geometry.geoms)
    else:
        return 0

def split_geometry(geometry):
    minx, miny, maxx, maxy = geometry.bounds
    dx = maxx - minx
    dy = maxy - miny

    if dx >= dy:
        split_line = LineString([((minx + maxx)/2, miny), ((minx + maxx)/2, maxy)])
    else:
        split_line = LineString([(minx, (miny + maxy)/2), (maxx, (miny + maxy)/2)])

    try:
        result = shapely_split(geometry, split_line)
    except Exception:
        return [geometry]
    return list(result.geoms) if hasattr(result, 'geoms') else [result]

def recursive_split(geometry, max_vertices=256):
    queue = [geometry]
    results = []

    while queue:
        geom = queue.pop()
        if count_vertices(geom) <= max_vertices:
            results.append(geom)
        else:
            parts = split_geometry(geom)
            if len(parts) == 1:
                results.append(geom)
            else:
                queue.extend(parts)
    return results

# ---------------------- UI ---------------------------
uploaded_file = st.file_uploader("Upload a GeoJSON file", type="geojson")
max_vertices = st.slider("Max vertices per part", min_value=50, max_value=1000, value=256, step=50)

if uploaded_file:
    st.success("File uploaded. Ready to process.")
    if st.button("Split & Download"):
        with st.spinner("Splitting polygons and correcting winding..."):
            with tempfile.TemporaryDirectory() as tmpdir:
                gdf = gpd.read_file(uploaded_file)
                base_name = os.path.splitext(uploaded_file.name)[0]
                part_counter = 1
                all_parts = []

                for idx, row in gdf.iterrows():
                    split_geoms = recursive_split(row.geometry, max_vertices=max_vertices)
                    for geom in split_geoms:
                        part = row.copy()
                        part.geometry = geom
                        geojson_part = json.loads(gpd.GeoSeries([geom], crs=gdf.crs).to_json())
                        rewound_geom = rewind(geojson_part['features'][0]['geometry'], rfc7946=True)
                        part.geometry = shape(rewound_geom)
                        all_parts.append(part)

                # Write parts to individual files
                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zf:
                    for i, part in enumerate(all_parts, 1):
                        out_gdf = gpd.GeoDataFrame([part], crs=gdf.crs)
                        out_path = f"{base_name}_part{i}.geojson"
                        tmp_file = os.path.join(tmpdir, out_path)
                        out_gdf.to_file(tmp_file, driver="GeoJSON")
                        with open(tmp_file, "rb") as f:
                            zf.writestr(out_path, f.read())

                zip_buffer.seek(0)

        st.success(f"✅ Done! {len(all_parts)} parts created.")
        st.download_button(
            label="Download ZIP of Split Files",
            data=zip_buffer,
            file_name=f"{base_name}_split_parts.zip",
            mime="application/zip"
        )

