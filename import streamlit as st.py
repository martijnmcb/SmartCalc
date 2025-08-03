import streamlit as st
import pandas as pd
import os
import json
import pickle
from itertools import permutations

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
    elif method == "Matrix":
        points = [start] + via + [end]
        n = len(points)
        matrix = [[0]*n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j:
                    res = get_osrm_route([points[i], points[j]])
                    if res and res.get("routes"):
                        matrix[i][j] = res["routes"][0]["duration"]
                    else:
                        matrix[i][j] = float("inf")

        best_time = float("inf")
        best_order = via
        best_coords = []

        for perm in permutations(range(1, n - 1)):
            route = [0] + list(perm) + [n - 1]
            time = sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))
            if time < best_time:
                best_time = time
                best_order = [via[i - 1] for i in perm]
                best_coords = [start] + [via[i - 1] for i in perm] + [end]

        return best_order, best_coords, best_time
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
        coord_map = {}
        for p in full_list:
            c = postcode_to_coords(p, postcode_df=postcode_df)
            if c:
                coords.append((c[0], c[1]))
                coord_map[p] = (c[0], c[1])
            else:
                st.warning(f"Postcode {p} kon niet omgezet worden.")
                coords = []
                break

        if len(coords) >= 2:
            route_orig = get_osrm_route(coords)
            dist_orig = route_orig["routes"][0]["distance"] / 1000 if route_orig else None
            time_orig = route_orig["routes"][0]["duration"] / 60 if route_orig else None

            opt_result = find_optimal_route(coords[0], coords[1:-1], coords[-1], method=st.session_state.get("global_method", "Brute-Force"))
            best_coords = opt_result[1]
            best_order_coords = opt_result[0]
            best_order_postcodes = [k for k, v in coord_map.items() if v in best_order_coords]

            route_opt = get_osrm_route(best_coords)
            dist_opt = route_opt["routes"][0]["distance"] / 1000 if route_opt else None
            time_opt = route_opt["routes"][0]["duration"] / 60 if route_opt else None

            if all(x is not None for x in [dist_orig, time_orig, dist_opt, time_opt]):
                results.append({
                    "RouteID": idx + 1,
                    "Van": van,
                    "Via Origineel": ", ".join(via),
                    "Via Geoptimaliseerd": ", ".join(best_order_postcodes),
                    "Naar": naar,
                    "Afstand Origineel (km)": round(dist_orig, 2),
                    "Tijd Origineel (min)": round(time_orig, 1),
                    "Afstand Optimaal (km)": round(dist_opt, 2),
                    "Tijd Optimaal (min)": round(time_opt, 1),
                    "Tijdswinst (min)": round(time_orig - time_opt, 1)
                })
    progress.empty()
    return pd.DataFrame(results)
#Commented out to avoid running on import
