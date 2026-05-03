import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
import os
import json
import pickle
import shelve
from itertools import permutations
import matplotlib.pyplot as plt
import numpy as np
import datetime
import plotly.express as px
import hashlib
import time

# Route cache (blijft met pickle)
route_cache_file = "route_cache.pkl"
route_cache = {}
if os.path.exists(route_cache_file):
    with open(route_cache_file, "rb") as f:
        try:
            route_cache = pickle.load(f)
        except:
            route_cache = {}


# Batch route cache toevoegen aan session_state
if "batch_route_cache" not in st.session_state:
    st.session_state["batch_route_cache"] = {}
batch_route_cache = st.session_state["batch_route_cache"]

postcode_cache = {}

# Optimalisatiemethode selectie naar de sidebar verplaatst

method = st.sidebar.radio("Optimalisatiemethode", ["Brute-Force", "Nearest Neighbor", "Matrix"], key="global_method")

# Toon percentage cache-hits direct na methodeselectie
import shelve
# Segment cache statistieken met reset-vlag
if "segment_cache_reset" not in st.session_state:
    st.session_state["segment_cache_reset"] = True

if st.session_state["segment_cache_reset"]:
    with shelve.open("segment_cache.db", writeback=True) as segment_cache:
        segment_cache["_total_requests"] = 0
        segment_cache["_cache_hits"] = 0
    st.session_state["segment_cache_reset"] = False

# Toon segment cache statistieken altijd in de sidebar
try:
    with shelve.open("segment_cache.db", writeback=False) as segment_cache:
        total_segments = segment_cache.get("_total_requests", 0)
        hits = segment_cache.get("_cache_hits", 0)
    if total_segments > 0:
        percentage = 100 * hits / total_segments
        st.sidebar.caption(f"💾 Segment cache: {hits} hits van {total_segments} ({percentage:.1f}%)")
    else:
        st.sidebar.caption("💾 Segment cache: nog geen gegevens")
except Exception as e:
    st.sidebar.caption("⚠️ Kan cache-info niet lezen")

uploaded_postcode_file = st.sidebar.file_uploader("📄 Upload postcode-coördinaten CSV (kolommen: postcode, lat, lon)", type=["csv"])
postcode_df = None
if uploaded_postcode_file is not None:
    try:
        postcode_df = pd.read_csv(uploaded_postcode_file, dtype=str)
        st.sidebar.success("✅ Postcode-database geladen")
        # Mapping voor alternatieve kolomnamen
        column_mapping = {
            "postcode": "postcode",
            "zip": "postcode",
            "zipcode": "postcode",
            "lat": "lat",
            "latitude": "lat",
            "lon": "lon",
            "lng": "lon",
            "long": "lon",
            "longitude": "lon"
        }
        # Normaliseer en hernoem kolommen
        new_columns = {}
        for col in postcode_df.columns:
            key = col.strip().lower()
            if key in column_mapping:
                new_columns[col] = column_mapping[key]
        postcode_df = postcode_df.rename(columns=new_columns)
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
from itertools import permutations
import plotly.graph_objects as go


@st.cache_data(show_spinner=False)
def postcode_to_coords(postcode, postcode_dict=None, postcode_df=None):
    start_time = time.time()
    global postcode_cache
    p = str(postcode).strip().replace(" ", "").upper()
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
    def segment_key(p1, p2):
        return hashlib.sha256(f"{p1}-{p2}".encode()).hexdigest()

    total_distance = 0
    total_duration = 0
    full_coordinates = []

    # Segment cache (shelve)
    with shelve.open("segment_cache.db", writeback=False) as segment_cache:
        for i in range(len(coords_list) - 1):
            p1 = coords_list[i]
            p2 = coords_list[i + 1]
            key = segment_key(p1, p2)

            if key in segment_cache:
                seg_result = segment_cache[key]
                segment_cache["_cache_hits"] = segment_cache.get("_cache_hits", 0) + 1
            else:
                coords_str = f"{p1[1]},{p1[0]};{p2[1]},{p2[0]}"
                url = f"http://100.87.138.39:5000/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
                response = requests.get(url)
                if response.status_code == 200:
                    seg_result = response.json()
                    segment_cache[key] = seg_result
                    segment_cache.sync()
                else:
                    seg_result = None
            segment_cache["_total_requests"] = segment_cache.get("_total_requests", 0) + 1

            if seg_result and seg_result.get("routes"):
                total_distance += seg_result["routes"][0]["distance"]
                total_duration += seg_result["routes"][0]["duration"]
                geometry = seg_result["routes"][0]["geometry"]
                if geometry["type"] == "LineString":
                    full_coordinates.extend(geometry["coordinates"])

    result = {
        "routes": [{
            "distance": total_distance,
            "duration": total_duration,
            "geometry": {
                "type": "LineString",
                "coordinates": full_coordinates
            }
        }],
        "code": "Ok"
    }
    return result

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
        # Matrixbenadering met brute berekening van alle combinaties
        points = [start] + via + [end]
        n = len(points)
        matrix = [[0]*n for _ in range(n)]

        # Vul matrix met tijden tussen elk paar punten
        for i in range(n):
            for j in range(n):
                if i != j:
                    segment = [points[i], points[j]]
                    key_hash = hashlib.sha256("|".join([f"{lat:.5f},{lon:.5f}" for lat, lon in segment]).encode()).hexdigest()
                    with shelve.open("segment_cache.db", writeback=False) as segment_cache:
                        if key_hash in segment_cache:
                            res = segment_cache[key_hash]
                        else:
                            res = get_osrm_route(segment)
                            if res:
                                segment_cache[key_hash] = res
                                segment_cache.sync()
                    if res and res.get("routes"):
                        matrix[i][j] = res["routes"][0]["duration"]
                    else:
                        matrix[i][j] = float("inf")


        best_time = float("inf")
        best_order = via
        best_coords = []

        for perm in permutations(range(1, n - 1)):
            route = [0] + list(perm) + [n - 1]
            time_ = sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))
            if time_ < best_time:
                best_time = time_
                best_order = [via[i - 1] for i in perm]
                best_coords = [start] + [via[i - 1] for i in perm] + [end]

        return best_order, best_coords, best_time
    else:
        best_order = via
        best_time = float("inf")
        best_coords = []
        for perm in permutations(via):
            route_coords = [start] + list(perm) + [end]
            coords_hash = hashlib.sha256("|".join([f"{lat:.5f},{lon:.5f}" for lat, lon in route_coords]).encode()).hexdigest()
            result = batch_route_cache.get(coords_hash)
            if not result:
                result = get_osrm_route(route_coords)
                if result:
                    batch_route_cache[coords_hash] = result
            if result and result.get("code") == "Ok":
                time_ = result["routes"][0]["duration"]
                if time_ < best_time:
                    best_time = time_
                    best_order = list(perm)
                    best_coords = route_coords
        return best_order, best_coords, best_time

st.title("📍 Routeplanner")

tab1, tab2 = st.tabs(["🧭 Interactieve planner", "📑 Batch via Excel"])

with tab1:
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
        # Reset segment cache teller vóór routeberekening
        with shelve.open("segment_cache.db", writeback=True) as segment_cache:
            segment_cache["_total_requests"] = 0
            segment_cache["_cache_hits"] = 0
        st.session_state["segment_cache_reset"] = False
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
        coords_all_orig = route_orig["routes"][0]["geometry"]["coordinates"] if route_orig and route_orig.get("routes") else []

        result_data = {
            "original": {
                "coords": original_coords,
                "route": route_orig,
                "coords_all": coords_all_orig
            },
            "optimized": None,
        }

        if optimize:
            best_order, best_coords, best_time = find_optimal_route(start, via_coords, end, method=st.session_state.get("global_method", "Brute-Force"))
            result_data["optimized"] = {
                "coords": best_coords,
                "time": best_time
            }

        # Toon segment cache statistieken na deze planslag
        try:
            with shelve.open("segment_cache.db", writeback=False) as segment_cache:
                total_segments = segment_cache.get("_total_requests", 0)
                hits = segment_cache.get("_cache_hits", 0)
            if total_segments > 0:
                percentage = 100 * hits / total_segments
                st.sidebar.caption(f"💾 Segment cache: {hits} hits van {total_segments} ({percentage:.1f}%)")
            else:
                st.sidebar.caption("💾 Segment cache: nog geen gegevens")
        except Exception as e:
            st.sidebar.caption("⚠️ Kan cache-info niet lezen")

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

        # Geoptimaliseerde route
        if optimize and data["optimized"]:
            best_coords = data["optimized"]["coords"]
            tijd_best = data["optimized"]["time"] / 60
            geojson_route = get_osrm_route(best_coords)
            afstand_best = geojson_route["routes"][0]["distance"] / 1000 if geojson_route else 0

            st.subheader("🟢 Geoptimaliseerde volgorde")
            st.write(f"🕒 Reistijd: {tijd_best:.1f} min — 📏 Afstand: {afstand_best:.2f} km")

        # Toon nieuwe Plotly kaart als beide aanwezig
        if data["original"]["route"] and data["original"]["route"].get("code") == "Ok" and (not optimize or (optimize and data["optimized"])):
            # Haal de juiste routes op
            route_orig = data["original"]["route"]
            route_opt = None
            if optimize and data["optimized"]:
                best_coords = data["optimized"]["coords"]
                route_opt = get_osrm_route(best_coords)
                route_coords = best_coords
                # Store optimized route geometry for map centering
                if route_opt and route_opt.get("routes"):
                    data["optimized"]["route_geometry"] = route_opt["routes"][0]["geometry"]["coordinates"]
            else:
                route_opt = route_orig
                route_coords = data["original"]["coords"]

            # Zet start/eindpunt variabelen (voor markers)
            start_latitude, start_longitude = route_coords[0][1], route_coords[0][0]
            end_latitude, end_longitude = route_coords[-1][1], route_coords[-1][0]

            # Nieuwe kaartcode toevoegen
            import plotly.graph_objects as go
            fig = go.Figure()

            # Originele route (rood)
            fig.add_trace(go.Scattermap(
                lat=[coord[1] for coord in route_orig["routes"][0]["geometry"]["coordinates"]],
                lon=[coord[0] for coord in route_orig["routes"][0]["geometry"]["coordinates"]],
                mode="lines",
                line=dict(width=4, color="red"),
                name="Originele route"
            ))

            # Geoptimaliseerde route (groen)
            fig.add_trace(go.Scattermap(
                lat=[coord[1] for coord in route_opt["routes"][0]["geometry"]["coordinates"]],
                lon=[coord[0] for coord in route_opt["routes"][0]["geometry"]["coordinates"]],
                mode="lines",
                line=dict(width=4, color="green"),
                name="Geoptimaliseerde route"
            ))

            # Voeg markers toe voor start- en eindpunt
            fig.add_trace(go.Scattermap(
                lat=[route_coords[0][0]],
                lon=[route_coords[0][1]],
                mode='markers',
                marker=dict(size=12, color='green'),
                name='Startpunt'
            ))

            fig.add_trace(go.Scattermap(
                lat=[route_coords[-1][0]],
                lon=[route_coords[-1][1]],
                mode='markers',
                marker=dict(size=12, color='red'),
                name='Eindpunt'
            ))

            # Center map properly based on route bounds
            coords_all = []
            
            if isinstance(route_orig, dict) and "routes" in route_orig and route_orig["routes"]:
                coords_all = route_orig["routes"][0]["geometry"]["coordinates"]
            if optimize and data.get("optimized") and "route_geometry" in data["optimized"]:
                coords_all = data["optimized"]["route_geometry"]
            if coords_all and len(coords_all) > 0:
                try:
                    lats = [coord[1] for coord in coords_all]
                    lons = [coord[0] for coord in coords_all]
                    min_lat, max_lat = min(lats), max(lats)
                    min_lon, max_lon = min(lons), max(lons)
                    center_lat = (min_lat + max_lat) / 2
                    center_lon = (min_lon + max_lon) / 2
                    import math
                    diagonal_distance = math.sqrt((max_lat - min_lat) ** 2 + (max_lon - min_lon) ** 2)
                    if diagonal_distance < 0.002:
                        zoom_level = 17
                    elif diagonal_distance < 0.004:
                        zoom_level = 16
                    elif diagonal_distance < 0.008:
                        zoom_level = 15
                    elif diagonal_distance < 0.015:
                        zoom_level = 14
                    elif diagonal_distance < 0.03:
                        zoom_level = 13
                    elif diagonal_distance < 0.07:
                        zoom_level = 12
                    elif diagonal_distance < 0.15:
                        zoom_level = 11
                    elif diagonal_distance < 0.35:
                        zoom_level = 10
                    elif diagonal_distance < 0.7:
                        zoom_level = 9
                    elif diagonal_distance < 1.5:
                        zoom_level = 8
                    else:
                        zoom_level = 7
                except Exception:
                    center_lat = 52.1326
                    center_lon = 5.2913
                    zoom_level = 7
            else:
                center_lat = 52.1326
                center_lon = 5.2913
                zoom_level = 7
            if center_lat == 0 and center_lon == 0:
                center_lat = 52.1326
                center_lon = 5.2913
                zoom_level = 7
            if coords_all and len(coords_all) < 30 and zoom_level < 17:
                zoom_level = min(zoom_level + 1, 17)
            fig.update_layout(
                map=dict(
                    style="open-street-map",
                    center={"lat": center_lat, "lon": center_lon},
                    zoom=zoom_level
                ),
                margin={"r": 0, "t": 0, "l": 0, "b": 0},
                height=800,
                legend=dict(x=0, y=1)
            )
            st.plotly_chart(fig, use_container_width=True)

def load_postcode_coordinates(csv_path):
    df = pd.read_csv(csv_path, dtype=str)
    df["postcode"] = df["postcode"].str.replace(" ", "").str.upper()
    return dict(zip(df["postcode"], zip(df["lat"].astype(float), df["lon"].astype(float))))

from io import BytesIO

# Functie voor aanpassen van kolomnamen voor batchverwerking
def pas_kolomnamen_aan(df):
    mapping = {
        "van": "van",
        "from": "van",
        "start": "van",
        "startpunt": "van",
        "naar": "naar",
        "to": "naar",
        "eind": "naar",
        "eindpunt": "naar",
        "via": "via",
        "tussenstops": "via",
        "stops": "via",
        "datum": "datum",
        "date": "datum",
        "route id": "route_id",
        "id": "route_id",
    }
    new_columns = {}
    for col in df.columns:
        key = col.strip().lower()
        if key in mapping:
            new_columns[col] = mapping[key]
    return df.rename(columns=new_columns)

def process_batch(uploaded_file):
    # Reset segment cache teller
    with shelve.open("segment_cache.db", writeback=True) as segment_cache:
        segment_cache["_total_requests"] = 0
        segment_cache["_cache_hits"] = 0
    # --- Nieuwe code voor kolomselectie via Streamlit ---
    temp_df = pd.read_excel(uploaded_file)
    temp_df.columns = temp_df.columns.str.strip()
    kolommen = temp_df.columns.tolist()

    # Voeg suggestie-functie toe voor indexbepaling
    def suggest_index(kolomnamen, zoekwoorden):
        for i, naam in enumerate(kolomnamen):
            naam_lower = naam.lower().strip()
            for woord in zoekwoorden:
                if woord in naam_lower:
                    return i
        return 0

    index_van = suggest_index(kolommen, ["van", "start", "from", "startpunt", "vanpostcode", "Van"])
    index_naar = suggest_index(kolommen, ["naar", "eind", "to", "eindpunt","Bestemming"])
    index_via = suggest_index(kolommen, ["via", "tussenstops", "stops"])

    with st.form("kolomselectie_formulier"):
        kolom_van = st.selectbox("Selecteer kolom voor 'Van'", kolommen, index=index_van)
        kolom_naar = st.selectbox("Selecteer kolom voor 'Naar'", kolommen, index=index_naar)
        kolom_via = st.selectbox("Selecteer kolom voor 'Via' (optioneel)", [""] + kolommen, index=index_via + 1 if index_via >= 0 else 0)
        extra_kolommen = st.multiselect("Kolommen meenemen in output", kolommen)
        doorgaan = st.form_submit_button("✔️ Bevestig kolomkeuze")

    if not doorgaan:
        return pd.DataFrame()

    df = temp_df.rename(columns={
        kolom_van: "van",
        kolom_naar: "naar",
        kolom_via: "via" if kolom_via else None
    }).dropna(axis=1, how='all')
    df.columns = df.columns.str.strip().str.lower()

    # Check op vereiste kolommen na het aanpassen van de kolomnamen
    required_cols = ["van", "naar"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        st.error(f"❌ Vereiste kolommen niet gevonden in Excel-bestand: {missing}")
        return pd.DataFrame()

    if df.empty:
        st.warning("⚠️ Geen gegevens gevonden in het Excel-bestand.")
        return pd.DataFrame()
    results = []
    progress = st.progress(0, text="Bezig met batchverwerking...")

    # Om dubbele coördinaten te voorkomen
    reverse_coord_map = {}

    # Voeg batch_result_cache toe aan session_state indien niet aanwezig
    if "batch_result_cache" not in st.session_state:
        st.session_state["batch_result_cache"] = {}

    for idx, row in df.iterrows():
        progress.progress((idx + 1) / len(df), text=f"Verwerk rij {idx + 1} van {len(df)}")
        van = row["van"]
        naar = row["naar"]
        via = [p.strip() for p in str(row["via"]).split(",")] if pd.notna(row.get("via", "")) else []

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
        reverse_coord_map = {v: k for k, v in coord_map.items()}

        dist_orig = None
        time_orig = None
        dist_opt = None
        time_opt = None
        best_coords = []
        route_orig = None
        route_opt = None

        if len(coords) >= 2:
            # Originele volgorde
            coords_hash = hashlib.sha256("|".join([f"{lat:.5f},{lon:.5f}" for lat, lon in coords]).encode()).hexdigest()
            route_orig = batch_route_cache.get(coords_hash)
            if not route_orig:
                route_orig = get_osrm_route(coords)
                batch_route_cache[coords_hash] = route_orig
            dist_orig = route_orig["routes"][0]["distance"] / 1000 if route_orig else None
            time_orig = route_orig["routes"][0]["duration"] / 60 if route_orig else None

            # Geoptimaliseerde volgorde
            opt_result = find_optimal_route(coords[0], coords[1:-1], coords[-1], method=st.session_state.get("global_method", "Brute-Force"))
            best_coords = opt_result[1]
            opt_hash = hashlib.sha256("|".join([f"{lat:.5f},{lon:.5f}" for lat, lon in best_coords]).encode()).hexdigest()
            route_opt = batch_route_cache.get(opt_hash)
            if not route_opt:
                route_opt = get_osrm_route(best_coords)
                batch_route_cache[opt_hash] = route_opt
            dist_opt = route_opt["routes"][0]["distance"] / 1000 if route_opt else None
            time_opt = route_opt["routes"][0]["duration"] / 60 if route_opt else None

        results.append({
            "RouteID": idx + 1,
            "Van": van,
            "Via Origineel": ", ".join(via),
            "Via Geoptimaliseerd": ", ".join([reverse_coord_map.get(c, f"{c[0]:.5f},{c[1]:.5f}") for c in best_coords[1:-1]]) if best_coords else "",
            "Naar": naar,
            "Afstand Origineel (km)": round(dist_orig, 2) if dist_orig is not None else None,
            "Tijd Origineel (min)": round(time_orig, 1) if time_orig is not None else None,
            "Afstand Optimaal (km)": round(dist_opt, 2) if dist_opt is not None else None,
            "Tijd Optimaal (min)": round(time_opt, 1) if time_opt is not None else None,
            "Tijdswinst (min)": round(time_orig - time_opt, 1) if time_orig is not None and time_opt is not None else None,
            **{col: temp_df.loc[idx, col] if col in temp_df.columns else "" for col in extra_kolommen},
        })
        # Sla de routes per batch op in session_state["batch_result_cache"] en voeg coords_all_opt toe
        coords_all_opt = route_opt["routes"][0]["geometry"]["coordinates"] if route_opt and route_opt.get("routes") else []
        st.session_state["batch_result_cache"][idx + 1] = {
            "route_orig": route_orig,
            "route_opt": route_opt,
            "coord_map": coord_map,
            "coords_orig": coords,
            "coords_opt": best_coords,
            "coords_all_opt": coords_all_opt
        }
    progress.empty()
    st.success("✅ Kolomkeuze bevestigd, planner gestart...")
    return pd.DataFrame(results)


# Klokdiagram functie toevoegen
def plot_klokdiagram(tijden, labels, titel="Klokdiagram van rit"):
    hoeken = []
    totaal_minuten = (tijden[-1] - tijden[0]).total_seconds() / 60

    for t in tijden:
        delta = (t - tijden[0]).total_seconds() / 60
        hoek = (delta / totaal_minuten) * 2 * np.pi
        hoeken.append(hoek)

    fig, ax = plt.subplots(subplot_kw={'projection': 'polar'})
    ax.set_theta_direction(-1)
    ax.set_theta_offset(np.pi / 2.0)
    ax.plot(hoeken, [1]*len(hoeken), 'o', markersize=10)
    for hoek, label in zip(hoeken, labels):
        ax.text(hoek, 1.1, label, ha='center', va='center')

    ax.set_yticklabels([])
    ax.set_xticks(np.linspace(0, 2*np.pi, 12, endpoint=False))
    ax.set_xticklabels(['12u', '1u', '2u', '3u', '4u', '5u', '6u', '7u', '8u', '9u', '10u', '11u'])
    plt.title(titel)
    st.pyplot(fig)


# Gantt chart functie toevoegen
def plot_gantt_chart(tijden, labels, titel="Gantt chart van rit"):
    df = []
    for i in range(len(tijden) - 1):
        df.append({
            "Stop": labels[i],
            "Start": tijden[i],
            "Einde": tijden[i + 1]
        })
    df.append({
        "Stop": labels[-1],
        "Start": tijden[-1],
        "Einde": tijden[-1]
    })

    fig = px.timeline(df, x_start="Start", x_end="Einde", y="Stop", title=titel)
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig)


with tab2:
    uploaded = st.file_uploader("Upload een Excel-bestand met kolommen: Van, Via, Naar", type=["xlsx"])
    if uploaded:
        st.session_state["segment_cache_reset"] = True
        df_result = process_batch(uploaded)
        st.session_state["batch_df_result"] = df_result

    # Toon resultaten en kaarten na verwerking
    if "batch_df_result" in st.session_state:
        df_result = st.session_state["batch_df_result"]
        st.subheader("📊 Resultaten")
        st.dataframe(df_result)

        buffer = BytesIO()
        df_result.to_excel(buffer, index=False)
        st.download_button("⬇️ Download resultaten als Excel", buffer.getvalue(), file_name="batch_resultaten.xlsx")

        if df_result.empty:
            st.warning("⚠️ Geen succesvolle routes berekend. Controleer de invoer.")
            st.stop()
        if "RouteID" not in df_result.columns:
            st.warning("⚠️ Geen RouteID kolom gevonden in de resultaten.")
            st.stop()

        # --- Automatisch selectie en tonen van routekaart na batchverwerking ---
        # (verwijderd: direct routekaart weergeven na selectie/batch)

    # --- Segmentenmatrix opbouwen tabblad ---
    with st.expander("🗂️ Segmentenmatrix opbouwen (optioneel, vooraf vullen van cache)"):
        matrix_file = st.file_uploader("Upload Excel met kolommen: Van, Via, Naar", type=["xlsx"], key="matrix")
        if matrix_file and st.button("🧱 Bouw segmentcache"):
            df_matrix = pd.read_excel(matrix_file)
            unique_postcodes = set()
            for _, row in df_matrix.iterrows():
                unique_postcodes.add(row["Van"])
                unique_postcodes.add(row["Naar"])
                if pd.notna(row["Via"]):
                    via_codes = [p.strip() for p in str(row["Via"]).split(",")]
                    unique_postcodes.update(via_codes)

            coords_dict = {}
            for p in unique_postcodes:
                c = postcode_to_coords(p, postcode_df=postcode_df)
                if c:
                    coords_dict[p] = c

            all_pairs = []
            keys_seen = set()
            for a in coords_dict.values():
                for b in coords_dict.values():
                    if a != b:
                        key = hashlib.sha256("|".join([f"{a[0]:.5f},{a[1]:.5f}", f"{b[0]:.5f},{b[1]:.5f}"]).encode()).hexdigest()
                        if key not in keys_seen:
                            keys_seen.add(key)
                            all_pairs.append((a, b))
            st.write(f"🔍 {len(all_pairs)} unieke segmenten te verwerken...")
            progress = st.progress(0, text="Bouwen van segmentcache...")
            count = 0
            added = 0
            with shelve.open("segment_cache.db", writeback=False) as segment_cache:
                for a, b in all_pairs:
                    key = hashlib.sha256("|".join([f"{a[0]:.5f},{a[1]:.5f}", f"{b[0]:.5f},{b[1]:.5f}"]).encode()).hexdigest()
                    if key not in segment_cache:
                        coord_string = f"{a[1]},{a[0]};{b[1]},{b[0]}"
                        url = f"http://100.87.138.39:5000/route/v1/driving/{coord_string}?overview=false"
                        res = requests.get(url)
                        if res.status_code == 200:
                            segment_cache[key] = res.json()
                            segment_cache.sync()
                            added += 1
                    count += 1
                    progress.progress(count / len(all_pairs), text=f"Verwerkt {count} van {len(all_pairs)} segmenten...")
            st.success(f"✅ Segmentcache aangevuld met {added} nieuwe routes.")
        