
import streamlit as st
import json
import os
import tempfile
import zipfile
from io import BytesIO
import geopandas as gpd
from shapely.geometry import shape, LineString
from shapely.ops import split as shapely_split
from geojson_rewind import rewind
import folium
from streamlit_folium import st_folium

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

def plot_geojson(features, label, color):
    fmap = folium.Map(location=[0, 0], zoom_start=2)
    for idx, feature in enumerate(features):
        geom = shape(feature["geometry"])
        folium.GeoJson(
            feature,
            name=f"{label} {idx+1}",
            style_function=lambda x, col=color: {
                "color": col,
                "weight": 2,
                "fillOpacity": 0.3
            }
        ).add_to(fmap)
    return fmap

# ---------------------- UI ---------------------------
uploaded_file = st.file_uploader("Upload a GeoJSON file", type="geojson")
max_vertices = st.slider("Max vertices per part", min_value=50, max_value=1000, value=256, step=50)

if uploaded_file:
    gdf = gpd.read_file(uploaded_file)
    st.success("File uploaded. Ready to process.")

    # Initial map preview
    st.markdown("### 🌍 Original Geometry Preview")
    geojson_dict = json.loads(gdf.to_json())
    with st.expander("View Uploaded GeoJSON"):
        original_map = plot_geojson(geojson_dict["features"], "Original", "gray")
        st_folium(original_map, width="100%", height=500)

    if st.button("Split & Download"):
        progress = st.progress(0, text="Splitting polygons and correcting winding...")
        with tempfile.TemporaryDirectory() as tmpdir:
            base_name = os.path.splitext(uploaded_file.name)[0]
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
                progress.progress((idx + 1) / len(gdf), text=f"Processing feature {idx+1} of {len(gdf)}")

            # Write and package output
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

            # Preview result
            st.markdown("### 🧩 Split Parts Preview")
            part_features = [
                json.loads(gpd.GeoSeries([p.geometry], crs=gdf.crs).to_json())["features"][0]
                for p in all_parts
            ]
            split_map = plot_geojson(part_features, "Split", "green")
            st_folium(split_map, width="100%", height=500)

            st.success(f"✅ Done! {len(all_parts)} parts created.")
            st.download_button(
                label="Download ZIP of Split Files",
                data=zip_buffer,
                file_name=f"{base_name}_split_parts.zip",
                mime="application/zip"
            )
