
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

        return {"coords": coords, "time": None}


import streamlit as st

import streamlit as st
import requests
from itertools import permutations

@st.cache_data(show_spinner=False)
def postcode_to_coords(postcode):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": postcode + ", Netherlands", "format": "json", "limit": 1}
    headers = {"User-Agent": "TaxiPlanner/1.0"}
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    if data:
        return float(data[0]["lat"]), float(data[0]["lon"])
    return None

def get_osrm_route(coords_list):
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coords_list])
    url = f"http://localhost:5000/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return None

def find_optimal_route(start, via, end):
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

st.title("📍 Postcode Routeplanner met Optimalisatie")

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
        c = postcode_to_coords(p)
        if c:
            coord_map[p] = c

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
        best_order, best_coords, best_time = find_optimal_route(start, via_coords, end)
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

                            for i, (lat, lon) in enumerate(original_coords):
                            

    # Geoptimaliseerde route
    if optimize and data["optimized"]:
        best_coords = data["optimized"]["coords"]
        tijd_best = data["optimized"]["time"] / 60
        geojson_route = get_osrm_route(best_coords)
        afstand_best = geojson_route["routes"][0]["distance"] / 1000 if geojson_route else 0

        st.subheader("🟢 Geoptimaliseerde volgorde")
        st.write(f"🕒 Reistijd: {tijd_best:.1f} min — 📏 Afstand: {afstand_best:.2f} km")

        with st.container():
            m2 =             if geojson_route:

            for i, (lat, lon) in enumerate(best_coords):
                            



import pandas as pd
from io import BytesIO

def process_batch(uploaded_file):
    df = pd.read_excel(uploaded_file)
    results = []

    progress_bar = st.progress(0)
    log_rows = []
    for idx, row in df.iterrows():
        progress_bar.progress((idx + 1) / len(df))
        st.write(f"🔄 Verwerk rij {idx+1}/{len(df)}: {row['Van']} → {row['Naar']}")

        progress_bar.progress((idx + 1) / len(df))
        van = row["Van"]
        naar = row["Naar"]
        via = [p.strip() for p in str(row["Via"]).split(",")] if pd.notna(row["Via"]) else []

        full_list = [van] + via + [naar]
        coords = []
        for p in full_list:
            c = postcode_to_coords(p)
            if c:
                coords.append((c[0], c[1]))

                st.warning(f"Postcode {p} kon niet omgezet worden.")
                coords = []
                break

        if len(coords) >= 2:
            # Originele volgorde
            route_orig = get_osrm_route(coords)
            dist_orig = route_orig["routes"][0]["distance"] / 1000 if route_orig else None
            time_orig = route_orig["routes"][0]["duration"] / 60 if route_orig else None

            # Geoptimaliseerde volgorde
            opt_result = get_osrm_optimized_route(coords)
            best_coords = opt_result["coords"]
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
            log_rows.append({
                "Rij": idx + 1,
                "Input": f"{van} -> {', '.join(via)} -> {naar}",
                "Status": "✅ Gelukt"
            })
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
    log_df = pd.DataFrame(log_rows)
    return pd.DataFrame(results), log_df


st.title("📍 Routeplanner: Interactief of Batchverwerking")
mode = st.radio("Kies modus:", ["Interactieve planner", "Batch via Excel"])

if mode == "Batch via Excel":
    uploaded = st.file_uploader("Upload een Excel-bestand met kolommen: Van, Via, Naar", type=["xlsx"])
    if uploaded:
        df_result, df_log = process_batch(uploaded)
        st.subheader("📊 Resultaten")
        st.dataframe(df_result)

        st.subheader("🪵 Verwerkingslog")
        st.dataframe(df_log)

        buffer2 = BytesIO()
        with pd.ExcelWriter(buffer2, engine="openpyxl") as writer:
            df_result.to_excel(writer, index=False, sheet_name="Resultaten")
            df_log.to_excel(writer, index=False, sheet_name="Log")
        st.download_button("⬇️ Download resultaten + log als Excel", buffer2.getvalue(), file_name="batch_met_log.xlsx")

        # Download knop
        buffer = BytesIO()
        df_result.to_excel(buffer, index=False)
        st.download_button("⬇️ Download resultaten als Excel", buffer.getvalue(), file_name="batch_resultaten.xlsx")

    # Interactieve planner code is al aanwezig in base_script
    pass
