
import folium
import streamlit as st
import requests
import pandas as pd
from itertools import permutations
from io import BytesIO
import time

# ------------------------
# Postcode naar coördinaten
# ------------------------
def postcode_to_coords(postcode):
    postcode = postcode.strip()
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": postcode + ", Netherlands", "format": "json", "limit": 1}
    headers = {"User-Agent": "SmartCalcRouteplanner/1.0"}
    response = requests.get(url, params=params, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
    return None

# ------------------------
# OSRM route ophalen
# ------------------------
def get_osrm_route(coords_list):
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coords_list])
    url = f"http://localhost:5000/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return None

# ------------------------
# Optimaliseer route via tussenstops
# ------------------------
def get_osrm_optimized_route(coords):
    best_order = coords
    best_time = float("inf")

    if len(coords) <= 8:
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

# ------------------------
# Batchverwerking
# ------------------------
def process_batch(uploaded_file):
    df = pd.read_excel(uploaded_file)
    results = []
    progress_bar = st.progress(0)

    for idx, row in df.iterrows():
        van = row["Van"]
        naar = row["Naar"]
        via = str(row["Via"]) if pd.notna(row["Via"]) else ""

        via_list = [p.strip() for p in via.split(",") if p.strip()]

        coords = []
        error = None
        for label, pc in [("van", van)] + [(f"via{i+1}", v) for i, v in enumerate(via_list)] + [("naar", naar)]:
            c = postcode_to_coords(pc)
            if not c:
                error = f"❌ Geen coördinaten gevonden voor postcode: {pc}"
                break
            coords.append(c)

        if error:
            results.append({
                "RouteID": idx + 1,
                "Status": error,
                "Originele tijd (min)": None,
                "Originele afstand (km)": None,
                "Geoptimaliseerde tijd (min)": None,
                "Geoptimaliseerde afstand (km)": None,
                "Winst (min)": None
            })
            progress_bar.progress((idx + 1) / len(df))
            continue

        # Originele route
        route_orig = get_osrm_route(coords)
        orig_time = route_orig["routes"][0]["duration"] / 60 if route_orig else None
        orig_dist = route_orig["routes"][0]["distance"] / 1000 if route_orig else None

        # Geoptimaliseerde route
        result_opt = get_osrm_optimized_route(coords)
        route_opt = get_osrm_route(result_opt["coords"])
        opt_time = route_opt["routes"][0]["duration"] / 60 if route_opt else None
        opt_dist = route_opt["routes"][0]["distance"] / 1000 if route_opt else None

        winst = orig_time - opt_time if orig_time and opt_time else None

        results.append({
            "RouteID": idx + 1,
            "Status": "✅",
            "Originele tijd (min)": round(orig_time, 1) if orig_time else None,
            "Originele afstand (km)": round(orig_dist, 2) if orig_dist else None,
            "Geoptimaliseerde tijd (min)": round(opt_time, 1) if opt_time else None,
            "Geoptimaliseerde afstand (km)": round(opt_dist, 2) if opt_dist else None,
            "Winst (min)": round(winst, 1) if winst else None
        })

        progress_bar.progress((idx + 1) / len(df))

    return pd.DataFrame(results)

# ------------------------
# UI
# ------------------------
st.title("📊 Batch Routeoptimalisatie op basis van postcodes")
uploaded = st.file_uploader("Upload een Excel-bestand met kolommen: Van, Via, Naar")

if uploaded:
    df_result = process_batch(uploaded)
    st.subheader("✅ Resultaten")
    st.dataframe(df_result)

    # Download link
    buffer = BytesIO()
    df_result.to_excel(buffer, index=False, engine='openpyxl')
    st.download_button("📥 Download resultaten als Excel", data=buffer.getvalue(), file_name="batch_resultaten.xlsx")



else:
    st.subheader("🚏 Handmatige invoer")
    van = st.text_input("Postcode van", value="2011AA")
    via_input = st.text_input("Postcodes via (komma-gescheiden)", value="1033DW,1092EB")
    naar = st.text_input("Postcode naar", value="8076PM")

    if st.button("🔍 Bereken route"):
        via_postcodes = [p.strip() for p in via_input.split(",") if p.strip()]
        coords_van = postcode_to_coords(van)
        coords_naar = postcode_to_coords(naar)
        via_coords = [postcode_to_coords(p) for p in via_postcodes]

        if coords_van and coords_naar and all(via_coords):
            full_coords = [coords_van] + via_coords + [coords_naar]
            result = get_osrm_route(full_coords)
            if result:
        st.markdown(f"**Afstand**: {result['distance']:.1f} km  \n**Tijd**: {result['duration'] / 60:.1f} min")
**Tijd**: {result['duration'] / 60:.1f} min")

                m = folium.Map(location=coords_van, zoom_start=8)
                folium.Marker(location=coords_van, popup="Start", icon=folium.Icon(color="green")).add_to(m)
                folium.Marker(location=coords_naar, popup="Eind", icon=folium.Icon(color="red")).add_to(m)
                for coord in via_coords:
                    folium.Marker(location=coord, icon=folium.Icon(color="blue")).add_to(m)
                folium.PolyLine(locations=full_coords, color="blue", weight=5).add_to(m)
                from streamlit_folium import st_folium
                st_folium(m, width=700, height=500)
        else:
st.warning("❌ Eén of meerdere postcodes konden niet worden omgezet naar coördinaten.")