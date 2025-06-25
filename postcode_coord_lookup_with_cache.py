
import streamlit as st
import pandas as pd
import requests

# === Globale postcode-coördinaat cache ===
postcode_cache = {}

# === Streamlit sidebar voor CSV upload ===
st.sidebar.subheader("📍 Postcode-coördinaten")
csv_file = st.sidebar.file_uploader("Upload postcode-coördinaten CSV", type=["csv"])

# === Inlezen postcode-coördinatenbestand ===
if csv_file:
    df_coords = pd.read_csv(csv_file)
    for _, row in df_coords.iterrows():
        pc = str(row["postcode"]).replace(" ", "").upper()
        postcode_cache[pc] = (row["lat"], row["lon"])

# === Functie: Postcode naar coördinaten met fallback ===
def postcode_to_coords(postcode):
    pc = postcode.replace(" ", "").upper()
    if pc in postcode_cache:
        return postcode_cache[pc]

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": f"{postcode}, Netherlands",
        "format": "json",
        "limit": 1
    }
    headers = {
        "User-Agent": "TaxiPlanner/1.0"
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data:
                lat = float(data[0]["lat"])
                lon = float(data[0]["lon"])
                postcode_cache[pc] = (lat, lon)
                return lat, lon
    except Exception as e:
        st.warning(f"⚠️ Fout bij ophalen coördinaten voor {postcode}: {e}")
    return None

# === Demo UI ===
st.title("Postcode → Coördinaten")
postcode_input = st.text_input("Voer een postcode in (bijv. 2011AA):")
if postcode_input:
    coords = postcode_to_coords(postcode_input)
    if coords:
        st.success(f"Coördinaten voor {postcode_input}: {coords[0]}, {coords[1]}")
    else:
        st.error("❌ Geen coördinaten gevonden.")
