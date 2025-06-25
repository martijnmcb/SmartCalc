import streamlit as st
import pandas as pd
import os
import json
import pickle

route_cache_file = "route_cache.pkl"
route_cache = {}
if os.path.exists(route_cache_file):
    with open(route_cache_file, "rb") as f:
        try:
            route_cache = pickle.load(f)
        except:
            route_cache = {}

postcode_cache = {}

uploaded_postcode_file = st.sidebar.file_uploader("📄 Upload postcode-coördinaten CSV (kolommen: postcode, lat, lon)", type=["csv"])
postcode_df = None
if uploaded_postcode_file is not None:
    try:
        postcode_df = pd.read_csv(uploaded_postcode_file, dtype=str)
        postcode_df.columns = postcode_df.columns.str.strip().str.lower()
        st.sidebar.write("Ingelezen kolommen:", postcode_df.columns.tolist())
        required_columns = {"postcode", "lat", "lon"}
        if not required_columns.issubset(postcode_df.columns):
            st.sidebar.error(f"❌ Vereiste kolommen niet gevonden: {required_columns}. Gevonden kolommen: {list(postcode_df.columns)}")
            postcode_df = None
    except Exception as e:
        st.sidebar.error(f"Fout bij inlezen CSV: {e}")
        postcode_df = None

def get_osrm_optimized_route(coords):
    from itertools import permutations

    best_order = coords
    best_time = float("inf")

    if len(coords) <= 8:  # Alleen brute-force bij beperkte aantallen
        for perm in permutations(coords[1:-1]):
            candidate = [coords[0]] + list(perm) + [coords[-1]]
            route = get_osrm_route(candidate)
            if route and route.get("routes"):
                duration = route["routes"][0]["duration"]
                if duration < best_time:
                    best_time = duration
                    best_order = candidate
        return {"coords": best_order, "time": best_time}
    else:
        return {"coords": coords, "time": None}


import requests
import folium
from streamlit_folium import st_folium
from itertools import permutations

global postcode_cache

@st.cache_data(show_spinner=False)
def postcode_to_coords(postcode, postcode_dict=None, postcode_df=None):
    global postcode_cache
    p = postcode.replace(" ", "").upper()
    if postcode_df is not None:
        match = postcode_df[postcode_df["postcode"].str.replace(" ", "").str.upper() == p]
        if not match.empty:
            lat = float(match.iloc[0]["lat"])
            lon = float(match.iloc[0]["lon"])
            postcode_cache[p] = (lat, lon)
            return lat, lon
    if postcode_dict and p in postcode_dict:
        return postcode_dict[p]
    if p in postcode_cache:
        return postcode_cache[p]

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{p}, Netherlands", "format": "json", "limit": 1}
    headers = {"User-Agent": "SmartCalc/1.0"}
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    if data:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        postcode_cache[p] = (lat, lon)
        return lat, lon
    return None

def get_osrm_route(coords_list):
    coords_key = "|".join([f"{lat:.5f},{lon:.5f}" for lat, lon in coords_list])
    if coords_key in route_cache:
        return route_cache[coords_key]
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coords_list])
    url = f"http://localhost:5000/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    response = requests.get(url)
    if response.status_code == 200:
        result = response.json()
        # Alleen afstand/tijd + geometry in cache
        if result.get("routes"):
            cache_data = {
                "routes": [{
                    "distance": result["routes"][0]["distance"],
                    "duration": result["routes"][0]["duration"],
                    "geometry": result["routes"][0]["geometry"]
                }],
                "code": result.get("code", "Ok")
            }
            route_cache[coords_key] = cache_data
            with open(route_cache_file, "wb") as f:
                pickle.dump(route_cache, f)
            return cache_data
    return None

def find_optimal_route(start, via, end, method=None):
    if method is None:
        method = st.session_state.get("global_method", "Brute-Force")
    if method == "Nearest Neighbor":
        from geopy.distance import geodesic
        current = start
        remaining = via.copy()
        ordered = []
        while remaining:
            next_stop = min(remaining, key=lambda p: geodesic(current, p).km)
            ordered.append(next_stop)
            remaining.remove(next_stop)
            current = next_stop
        best_coords = [start] + ordered + [end]
        best_time = get_osrm_route(best_coords)["routes"][0]["duration"]
        return ordered, best_coords, best_time
    else:
        best_order = via
        best_time = float("inf")
        best_coords = []
        for perm in permutations(via):
            route_coords = [start] + list(perm) + [end]
            result = get_osrm_route(route_coords)
            if result and result.get("code") == "Ok":
                time = result["routes"][0]["duration"]
                if time < best_time:
                    best_time = time
                    best_order = list(perm)
                    best_coords = route_coords
        return best_order, best_coords, best_time

st.title("📍 Routeplanner: Interactief of Batchverwerking")
method = st.radio("Optimalisatiemethode", ["Brute-Force", "Nearest Neighbor"], key="global_method")

col1, col2 = st.columns(2)
with col1:
    postcode_start = st.text_input("Postcode van (startpunt)", "2011AA")
with col2:
    postcode_end = st.text_input("Postcode naar (eindpunt)", "1012WX")

postcodes_via = st.text_input("Postcodes via (komma-gescheiden)", "3511CE,2628CD")
optimize = st.checkbox("Optimaliseer tussenstops", value=True)

if "result" not in st.session_state:
    st.session_state["result"] = None

if st.button("🚗 Bereken route"):
    via_list = [p.strip() for p in postcodes_via.split(",") if p.strip()]
    all_postcodes = [postcode_start] + via_list + [postcode_end]

    coord_map = {}
    for p in all_postcodes:
        c = postcode_to_coords(p, postcode_df=postcode_df)
        if c:
            coord_map[p] = c
        else:
            st.error(f"❌ Geen coördinaten gevonden voor postcode: {p}")
            st.stop()

    start = coord_map[postcode_start]
    end = coord_map[postcode_end]
    via_coords = [coord_map[p] for p in via_list]

    original_coords = [start] + via_coords + [end]
    route_orig = get_osrm_route(original_coords)

    result_data = {
        "original": {
            "coords": original_coords,
            "route": route_orig,
        },
        "optimized": None,
    }

    if optimize:
        best_order, best_coords, best_time = find_optimal_route(start, via_coords, end, method=st.session_state.get("global_method", "Brute-Force"))
        result_data["optimized"] = {
            "coords": best_coords,
            "time": best_time
        }

    st.session_state["result"] = result_data

# Toon kaarten als resultaat beschikbaar is
if st.session_state["result"]:
    data = st.session_state["result"]

    # Originele route
    if data["original"]["route"] and data["original"]["route"].get("code") == "Ok":
        route_orig = data["original"]["route"]
        original_coords = data["original"]["coords"]
        tijd_orig = route_orig["routes"][0]["duration"] / 60
        afstand_orig = route_orig["routes"][0]["distance"] / 1000

        st.subheader("🔵 Oorspronkelijke volgorde")
        st.write(f"🕒 Reistijd: {tijd_orig:.1f} min — 📏 Afstand: {afstand_orig:.2f} km")

        with st.container():
            m1 = folium.Map(location=original_coords[0], zoom_start=8)
            if route_orig.get("routes"):
                folium.GeoJson(route_orig["routes"][0]["geometry"], name="route").add_to(m1)
            else:
                folium.PolyLine(locations=[(lat, lon) for lat, lon in original_coords], color="blue").add_to(m1)
            for i, (lat, lon) in enumerate(original_coords):
                color = "green" if i == 0 else "red" if i == len(original_coords) - 1 else "blue"
                folium.Marker([lat, lon], popup=f"Stop {i+1}", tooltip=f"Stop {i+1}", icon=folium.Icon(color=color, icon="info-sign")).add_to(m1)
            st_folium(m1, width=700, height=400, key="map_orig")

    # Geoptimaliseerde route
    if optimize and data["optimized"]:
        best_coords = data["optimized"]["coords"]
        tijd_best = data["optimized"]["time"] / 60
        geojson_route = get_osrm_route(best_coords)
        afstand_best = geojson_route["routes"][0]["distance"] / 1000 if geojson_route else 0

        st.subheader("🟢 Geoptimaliseerde volgorde")
        st.write(f"🕒 Reistijd: {tijd_best:.1f} min — 📏 Afstand: {afstand_best:.2f} km")

        with st.container():
            m2 = folium.Map(location=best_coords[0], zoom_start=8)
            if geojson_route:
                folium.GeoJson(geojson_route["routes"][0]["geometry"], name="route").add_to(m2)
            else:
                folium.PolyLine(locations=[(lat, lon) for lat, lon in best_coords], color="green").add_to(m2)
            for i, (lat, lon) in enumerate(best_coords):
                color = "green" if i == 0 else "red" if i == len(best_coords) - 1 else "blue"
                folium.Marker([lat, lon], popup=f"Stop {i+1}", tooltip=f"Stop {i+1}", icon=folium.Icon(color=color, icon="info-sign")).add_to(m2)
            st_folium(m2, width=700, height=400, key="map_opt")

def load_postcode_coordinates(csv_path):
    df = pd.read_csv(csv_path, dtype=str)
    df["postcode"] = df["postcode"].str.replace(" ", "").str.upper()
    return dict(zip(df["postcode"], zip(df["lat"].astype(float), df["lon"].astype(float))))

from io import BytesIO

def process_batch(uploaded_file):
    df = pd.read_excel(uploaded_file)
    results = []
    progress = st.progress(0, text="Bezig met batchverwerking...")

    for idx, row in df.iterrows():
        progress.progress((idx + 1) / len(df), text=f"Verwerk rij {idx + 1} van {len(df)}")
        van = row["Van"]
        naar = row["Naar"]
        via = [p.strip() for p in str(row["Via"]).split(",")] if pd.notna(row["Via"]) else []

        full_list = [van] + via + [naar]
        coords = []
        for p in full_list:
            c = postcode_to_coords(p, postcode_df=postcode_df)
            if c:
                coords.append((c[0], c[1]))
            else:
                st.warning(f"Postcode {p} kon niet omgezet worden.")
                coords = []
                break

        if len(coords) >= 2:
            # Originele volgorde
            route_orig = get_osrm_route(coords)
            dist_orig = route_orig["routes"][0]["distance"] / 1000 if route_orig else None
            time_orig = route_orig["routes"][0]["duration"] / 60 if route_orig else None

            # Geoptimaliseerde volgorde
            opt_result = find_optimal_route(coords[0], coords[1:-1], coords[-1], method=st.session_state.get("global_method", "Brute-Force"))
            best_coords = opt_result[1]
            route_opt = get_osrm_route(best_coords)
            dist_opt = route_opt["routes"][0]["distance"] / 1000 if route_opt else None
            time_opt = route_opt["routes"][0]["duration"] / 60 if route_opt else None

            if all(x is not None for x in [dist_orig, time_orig, dist_opt, time_opt]):
                results.append({
                    "RouteID": idx + 1,
                    "Van": van,
                    "Naar": naar,
                    "Via": ", ".join(via),
                    "Afstand Origineel (km)": round(dist_orig, 2),
                    "Tijd Origineel (min)": round(time_orig, 1),
                    "Afstand Optimaal (km)": round(dist_opt, 2),
                    "Tijd Optimaal (min)": round(time_opt, 1),
                    "Tijdswinst (min)": round(time_orig - time_opt, 1)
                })
    progress.empty()
    return pd.DataFrame(results)


st.title("📍 Routeplanner: Interactief of Batchverwerking")
mode = st.radio("Kies modus:", ["Interactieve planner", "Batch via Excel"])

if mode == "Batch via Excel":
    uploaded = st.file_uploader("Upload een Excel-bestand met kolommen: Van, Via, Naar", type=["xlsx"])
    if uploaded:
        df_result = process_batch(uploaded)
        st.subheader("📊 Resultaten")
        st.dataframe(df_result)

        # Download knop
        buffer = BytesIO()
        df_result.to_excel(buffer, index=False)
        st.download_button("⬇️ Download resultaten als Excel", buffer.getvalue(), file_name="batch_resultaten.xlsx")
else:
    # Interactieve planner code is al aanwezig in base_script
    pass
