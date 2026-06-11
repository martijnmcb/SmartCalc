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
with shelve.open("segment_cache.db", writeback=False) as segment_cache:
    try:
        total_segments = segment_cache.get("_total_requests", 0)
        hits = segment_cache.get("_cache_hits", 0)
        if total_segments > 0:
            percentage = 100 * hits / total_segments
            st.sidebar.caption(f"💾 Segment cache: {hits} hits van {total_segments} ({percentage:.1f}%)")
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
    print(f"[DEBUG] Start postcode_to_coords({postcode})")
    global postcode_cache
    p = postcode.replace(" ", "").upper()
    if postcode_df is not None:
        match = postcode_df[postcode_df["postcode"].str.replace(" ", "").str.upper() == p]
        if not match.empty:
            lat = float(match.iloc[0]["lat"])
            lon = float(match.iloc[0]["lon"])
            postcode_cache[p] = (lat, lon)
            print(f"[DEBUG] Einde postcode_to_coords({postcode}) - duur: {time.time() - start_time:.2f}s")
            return lat, lon
    if postcode_dict and p in postcode_dict:
        print(f"[DEBUG] Einde postcode_to_coords({postcode}) - duur: {time.time() - start_time:.2f}s")
        return postcode_dict[p]
    if p in postcode_cache:
        print(f"[DEBUG] Einde postcode_to_coords({postcode}) - duur: {time.time() - start_time:.2f}s")
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
        print(f"[DEBUG] Einde postcode_to_coords({postcode}) - duur: {time.time() - start_time:.2f}s")
        return lat, lon
    print(f"[DEBUG] Einde postcode_to_coords({postcode}) - duur: {time.time() - start_time:.2f}s")
    return None

def get_osrm_route(coords_list):
    start_time = time.time()
    print(f"[DEBUG] Start get_osrm_route({coords_list})")
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
                url = f"http://localhost:5000/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
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
    print(f"[DEBUG] Einde get_osrm_route - duur: {time.time() - start_time:.2f}s")
    return result

def find_optimal_route(start, via, end, method=None):
    start_time = time.time()
    print(f"[DEBUG] Start find_optimal_route({start}, {via}, {end}, method={method})")
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
        print(f"[DEBUG] Einde find_optimal_route - duur: {time.time() - start_time:.2f}s")
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

        print(f"[DEBUG] Einde find_optimal_route - duur: {time.time() - start_time:.2f}s")
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
        print(f"[DEBUG] Einde find_optimal_route - duur: {time.time() - start_time:.2f}s")
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
        # Reset segment cache teller
        with shelve.open("segment_cache.db", writeback=True) as segment_cache:
            segment_cache["_total_requests"] = 0
            segment_cache["_cache_hits"] = 0
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
            # route_coords is altijd beschikbaar als best_coords of original_coords
            # en is een lijst van (lat, lon) tuples
            # Maar in deze code: route_coords = best_coords of original_coords
            # best_coords/original_coords: [(lat, lon), ...]
            # De instructie verwacht (lon, lat), dus moeten we corrigeren
            # We willen markers op (lat, lon) -> (lat, lon)
            # Maar Plotly verwacht lat=[lat], lon=[lon]
            # Dus:
            # route_coords[0][0] = lat, route_coords[0][1] = lon
            # We willen lat=[lat], lon=[lon]
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

            # Center map
            coords_all = route_opt["routes"][0]["geometry"]["coordinates"]
            mid_lat = sum(coord[1] for coord in coords_all) / len(coords_all)
            mid_lon = sum(coord[0] for coord in coords_all) / len(coords_all)

            fig.update_layout(
                mapbox_style="open-street-map",
                mapbox_zoom=9,
                mapbox_center={"lat": mid_lat, "lon": mid_lon},
                margin={"r": 0, "t": 0, "l": 0, "b": 0},
                height=500,
                legend=dict(x=0, y=1)
            )

            st.plotly_chart(fig, use_container_width=True)

def load_postcode_coordinates(csv_path):
    df = pd.read_csv(csv_path, dtype=str)
    df["postcode"] = df["postcode"].str.replace(" ", "").str.upper()
    return dict(zip(df["postcode"], zip(df["lat"].astype(float), df["lon"].astype(float))))

from io import BytesIO

def process_batch(uploaded_file):
    start_time = time.time()
    print(f"[DEBUG] Start process_batch")
    df = pd.read_excel(uploaded_file)
    if df.empty:
        st.warning("⚠️ Geen gegevens gevonden in het Excel-bestand.")
        print(f"[DEBUG] Einde process_batch - duur: {time.time() - start_time:.2f}s")
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
                print(f"🔎 [DEBUG] get_osrm_route (origineel) wordt aangeroepen voor route {idx + 1}")
                route_orig = get_osrm_route(coords)
                batch_route_cache[coords_hash] = route_orig
            dist_orig = route_orig["routes"][0]["distance"] / 1000 if route_orig else None
            time_orig = route_orig["routes"][0]["duration"] / 60 if route_orig else None

            # Geoptimaliseerde volgorde
            print(f"🔎 [DEBUG] find_optimal_route wordt aangeroepen voor route {idx + 1}")
            opt_result = find_optimal_route(coords[0], coords[1:-1], coords[-1], method=st.session_state.get("global_method", "Brute-Force"))
            best_coords = opt_result[1]
            opt_hash = hashlib.sha256("|".join([f"{lat:.5f},{lon:.5f}" for lat, lon in best_coords]).encode()).hexdigest()
            route_opt = batch_route_cache.get(opt_hash)
            if not route_opt:
                print(f"🔎 [DEBUG] get_osrm_route (optimaal) wordt aangeroepen voor route {idx + 1}")
                route_opt = get_osrm_route(best_coords)
                batch_route_cache[opt_hash] = route_opt
                print(f"✅ Route {idx + 1}: origineel + optimaal berekend en opgeslagen.")
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
            "Tijdswinst (min)": round(time_orig - time_opt, 1) if time_orig is not None and time_opt is not None else None
        })
        # Sla de routes per batch op in session_state["batch_result_cache"]
        st.session_state["batch_result_cache"][idx + 1] = {
            "route_orig": route_orig,
            "route_opt": route_opt,
            "coord_map": coord_map,
            "coords_orig": coords,
            "coords_opt": best_coords
        }
    progress.empty()
    print(f"[DEBUG] Einde process_batch - duur: {time.time() - start_time:.2f}s")
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
    if uploaded and st.button("▶️ Start planner"):
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
        # Selecteer automatisch de eerste route indien nog geen selectie is gemaakt
        if not st.session_state.get("selected_id") and not df_result.empty:
            st.session_state["selected_id"] = df_result["RouteID"].iloc[0]

        # Selectiebox voor routekeuze
        selected_id = st.selectbox(
            "🗺️ Selecteer een route om te bekijken",
            df_result["RouteID"],
            index=df_result["RouteID"].tolist().index(st.session_state["selected_id"]) if st.session_state.get("selected_id") in df_result["RouteID"].tolist() else 0,
            key="selected_id"
        )

        # Direct routekaart weergeven na selectie/batch (zonder knop)
        if selected_id is not None:
            with st.spinner("Genereer kaarten..."):
                selected_row = df_result[df_result["RouteID"] == selected_id].iloc[0]
                van = selected_row["Van"]
                naar = selected_row["Naar"]
                via_orig = [p.strip() for p in selected_row["Via Origineel"].split(",")] if pd.notna(selected_row["Via Origineel"]) and selected_row["Via Origineel"] else []
                via_opt = [p.strip() for p in selected_row["Via Geoptimaliseerd"].split(",")] if pd.notna(selected_row["Via Geoptimaliseerd"]) and selected_row["Via Geoptimaliseerd"] else []

                full_orig = [van] + via_orig + [naar]
                full_opt = [van] + via_opt + [naar]

                # Haal de routes op uit session_state["batch_result_cache"] indien beschikbaar
                batch_result_cache = st.session_state.get("batch_result_cache", {})
                route_data = batch_result_cache.get(selected_id, {})
                route_orig = route_data.get("route_orig")
                route_opt = route_data.get("route_opt")
                coord_map = route_data.get("coord_map", {})

                # Nieuwe controle: alleen tonen als alle data aanwezig is
                if not coord_map or route_orig is None or route_opt is None:
                    st.warning("⚠️ Routedata incompleet voor deze selectie. Herbereken batch of controleer invoer.")
                    st.stop()

                coords_orig = route_data.get("coords_orig", [coord_map[p] for p in full_orig if p in coord_map])
                coords_opt = route_data.get("coords_opt", [coord_map[p] for p in full_opt if p in coord_map])

                # Toon gecombineerde kaart met beide routes (origineel = rood, geoptimaliseerd = groen)
                st.subheader("🗺️ Routevergelijking (origineel vs geoptimaliseerd)")
                tijd_orig = route_orig["routes"][0]["duration"] / 60
                afstand_orig = route_orig["routes"][0]["distance"] / 1000
                tijd_opt = route_opt["routes"][0]["duration"] / 60
                afstand_opt = route_opt["routes"][0]["distance"] / 1000
                st.write(f"🔴 Origineel: 🕒 {tijd_orig:.1f} min — 📏 {afstand_orig:.2f} km")
                st.write(f"🟢 Optimaal: 🕒 {tijd_opt:.1f} min — 📏 {afstand_opt:.2f} km")
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
                # Voeg markers toe voor start- en eindpunt volgens instructie
                if coords_opt and len(coords_opt) >= 2:
                    fig.add_trace(go.Scattermap(
                        lat=[coords_opt[0][0]],
                        lon=[coords_opt[0][1]],
                        mode='markers',
                        marker=dict(size=12, color='green'),
                        name='Startpunt'
                    ))
                    fig.add_trace(go.Scattermap(
                        lat=[coords_opt[-1][0]],
                        lon=[coords_opt[-1][1]],
                        mode='markers',
                        marker=dict(size=12, color='red'),
                        name='Eindpunt'
                    ))
                # Center map
                coords_all = route_opt["routes"][0]["geometry"]["coordinates"]
                mid_lat = sum(coord[1] for coord in coords_all) / len(coords_all)
                mid_lon = sum(coord[0] for coord in coords_all) / len(coords_all)
                fig.update_layout(
                    mapbox_style="open-street-map",
                    mapbox_zoom=10,
                    mapbox_center={"lat": mid_lat, "lon": mid_lon},
                    margin={"r": 0, "t": 0, "l": 0, "b": 0},
                    height=500,
                    legend=dict(x=0, y=1)
                )
                st.plotly_chart(fig, use_container_width=True)
                # --- Gantt chart en klokdiagram direct onder kaarten tonen ---
                # Gebruik route_opt als mogelijk
                full_osrm_opt = route_opt
                if full_osrm_opt and "legs" in full_osrm_opt["routes"][0]:
                    tijden = []
                    labels = []
                    tijd = datetime.datetime(2023, 1, 1, 8, 0)  # fictieve starttijd
                    tijden.append(tijd)
                    labels.append("Start")
                    for i, leg in enumerate(full_osrm_opt["routes"][0]["legs"]):
                        duur = datetime.timedelta(seconds=leg["duration"])
                        tijd += duur
                        tijden.append(tijd)
                        labels.append(f"Stop {i+1}")
                    import plotly.express as px
                    df_gantt = []
                    for i in range(len(tijden) - 1):
                        df_gantt.append({
                            "Stop": labels[i],
                            "Start": tijden[i],
                            "Einde": tijden[i + 1]
                        })
                    df_gantt.append({
                        "Stop": labels[-1],
                        "Start": tijden[-1],
                        "Einde": tijden[-1]
                    })
                    fig_gantt = px.timeline(df_gantt, x_start="Start", x_end="Einde", y="Stop", title="Gantt chart geoptimaliseerde route")
                    fig_gantt.update_yaxes(autorange="reversed")
                    kaart_label = "gantt"
                    st.plotly_chart(fig_gantt, use_container_width=True, key=f"plotly_{selected_id}_{kaart_label}")

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
                        url = f"http://localhost:5000/route/v1/driving/{coord_string}?overview=false"
                        res = requests.get(url)
                        if res.status_code == 200:
                            segment_cache[key] = res.json()
                            segment_cache.sync()
                            added += 1
                    count += 1
                    progress.progress(count / len(all_pairs), text=f"Verwerkt {count} van {len(all_pairs)} segmenten...")
            st.success(f"✅ Segmentcache aangevuld met {added} nieuwe routes.")
    