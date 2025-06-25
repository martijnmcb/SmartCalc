
import streamlit as st
import requests
import folium
from streamlit_folium import st_folium
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
            m1 = folium.Map(location=original_coords[0], zoom_start=8)
            folium.PolyLine(locations=[(lat, lon) for lat, lon in original_coords], color="blue").add_to(m1)
            for i, (lat, lon) in enumerate(original_coords):
                folium.Marker([lat, lon], tooltip=f"Stop {i+1}").add_to(m1)
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
                folium.Marker([lat, lon], tooltip=f"Stop {i+1}").add_to(m2)
            st_folium(m2, width=700, height=400, key="map_opt")
