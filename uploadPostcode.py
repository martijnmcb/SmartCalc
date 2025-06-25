import pandas as pd
import requests
import streamlit as st

# Upload postcode-coördinaten CSV
st.sidebar.header("📍 Geocode bron")
uploaded_csv = st.sidebar.file_uploader("Upload postcode-coördinaten CSV", type="csv")

postcode_df = None
if uploaded_csv:
    try:
        postcode_df = pd.read_csv(uploaded_csv)
        postcode_df.columns = postcode_df.columns.str.strip().str.lower()
        st.sidebar.write("📋 Gevonden kolommen:", postcode_df.columns.tolist())
        vereiste_kolommen = {"postcode", "lat", "lon"}
        if not vereiste_kolommen.issubset(set(postcode_df.columns)):
            ontbrekend = vereiste_kolommen - set(postcode_df.columns)
            st.sidebar.error(f"❌ Ontbrekende kolommen in CSV: {', '.join(ontbrekend)}")
            postcode_df = None
        else:
            st.sidebar.success(f"✅ {len(postcode_df)} postcodes geladen.")
    except Exception as e:
        st.sidebar.error(f"❌ Fout bij laden CSV: {e}")

# Functie voor conversie van postcode naar coördinaten
def postcode_to_coords(postcode, postcode_df=None):
    postcode = postcode.strip().upper().replace(" ", "")

    # Check in lokale CSV
    if postcode_df is not None:
        match = postcode_df[postcode_df['postcode'].str.replace(" ", "").str.upper() == postcode]
        if not match.empty:
            lat = match.iloc[0]["lat"]
            lon = match.iloc[0]["lon"]
            return lat, lon

    # Fallback: via Nominatim
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": postcode + ", Netherlands",
        "format": "json",
        "limit": 1
    }
    headers = {
        "User-Agent": "TaxiPlanner/1.0"
    }
    response = requests.get(url, params=params, headers=headers)
    data = response.json()
    if data:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return lat, lon
    else:
        return None

# Testinterface
st.title("🗺️ Postcode naar Coördinaten")
postcode_input = st.text_input("Voer een Nederlandse postcode in", value="2011AA")

if postcode_input:
    result = postcode_to_coords(postcode_input, postcode_df=postcode_df)
    if result:
        st.success(f"Coördinaten voor {postcode_input}: {result[0]}, {result[1]}")
    else:
        st.error(f"Geen coördinaten gevonden voor postcode: {postcode_input}")