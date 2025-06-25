
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

if st.button("🚗 Bereken route"):
    via_list = [p.strip() for p in postcodes_via.split(",") if p.strip()]
    all_postcodes = [postcode_start] + via_list + [postcode_end]

    st.info("🔍 Geocoderen van postcodes...")
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

    # Oorspronkelijke route
    original_coords = [start] + via_coords + [end]
    route_orig = get_osrm_route(original_coords)

    if route_orig and route_orig.get("code") == "Ok":
        tijd_orig = route_orig["routes"][0]["duration"] / 60
        afstand_orig = route_orig["routes"][0]["distance"] / 1000

        st.subheader("🔵 Oorspronkelijke volgorde")
        st.write(f"🕒 Reistijd: {tijd_orig:.1f} min — 📏 Afstand: {afstand_orig:.2f} km")

        with st.container():
            m1 = folium.Map(location=start, zoom_start=8)
            folium.PolyLine(locations=[(lat, lon) for lat, lon in original_coords], color="blue").add_to(m1)
            for i, (lat, lon) in enumerate(original_coords):
                folium.Marker([lat, lon], tooltip=f"Stop {i+1}").add_to(m1)
            st_folium(m1, width=700, height=400, key="map_orig")
    else:
        st.error("❌ Oorspronkelijke route niet gevonden")

    # Geoptimaliseerde route
    if optimize:
        best_order, best_coords, best_time = find_optimal_route(start, via_coords, end)
        if best_coords:
            tijd_best = best_time / 60

            st.subheader("🟢 Geoptimaliseerde volgorde")
            st.write(f"🕒 Reistijd: {tijd_best:.1f} min")

            with st.container():
                m2 = folium.Map(location=start, zoom_start=8)
                folium.PolyLine(locations=[(lat, lon) for lat, lon in best_coords], color="green").add_to(m2)
                for i, (lat, lon) in enumerate(best_coords):
                    folium.Marker([lat, lon], tooltip=f"Stop {i+1}").add_to(m2)
                st_folium(m2, width=700, height=400, key="map_opt")
        else:
            st.warning("⚠️ Geen geoptimaliseerde route gevonden.")
