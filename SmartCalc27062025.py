import streamlit as st
st.set_page_config(layout="wide")
import pandas as pd
import shelve
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import permutations
import plotly.express as px
import hashlib
import requests
import plotly.graph_objects as go
from io import BytesIO

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_BASE_URL = "http://100.71.38.105:5000"
REQUEST_TIMEOUT = 15
MAX_BRUTE_FORCE_STOPS = 8
SEGMENT_CACHE_FILE = "segment_cache.db"

_shelve_lock = threading.Lock()


# Batch route cache toevoegen aan session_state

postcode_cache = {}

# Optimalisatiemethode selectie naar de sidebar verplaatst

method = st.sidebar.radio("Optimalisatiemethode", ["Auto", "Brute-Force", "Nearest Neighbor", "Matrix"], index=0, key="global_method")

# Segment cache statistieken worden getoond na elke routeberekening via show_segment_cache_stats()

uploaded_postcode_file = st.sidebar.file_uploader("📄 Upload postcode-coördinaten CSV (kolommen: postcode, lat, lon)", type=["csv"])
postcode_df = None
if uploaded_postcode_file is not None:
    # Clear in-memory postcode cache when a new CSV is uploaded so stale
    # Nominatim results can't shadow CSV coordinates.
    if st.session_state.get("postcode_csv_file_id") != uploaded_postcode_file.file_id:
        st.session_state["postcode_csv_file_id"] = uploaded_postcode_file.file_id
        postcode_cache.clear()

    try:
        postcode_df = pd.read_csv(uploaded_postcode_file, dtype=str, sep=None, engine="python")
        column_mapping = {
            "postcode": "postcode", "zip": "postcode", "zipcode": "postcode",
            "lat": "lat", "latitude": "lat",
            "lon": "lon", "lng": "lon", "long": "lon", "longitude": "lon",
        }
        new_columns = {}
        for col in postcode_df.columns:
            key = col.strip().lower()
            if key in column_mapping:
                new_columns[col] = column_mapping[key]
        postcode_df = postcode_df.rename(columns=new_columns)
        postcode_df.columns = postcode_df.columns.str.strip().str.lower()
        if len(postcode_df.columns) == 1:
            only_column = postcode_df.columns[0]
            if ";" in only_column.strip().lower():
                uploaded_postcode_file.seek(0)
                postcode_df = pd.read_csv(uploaded_postcode_file, dtype=str, sep=";")
                postcode_df = postcode_df.rename(columns={
                    col: column_mapping.get(col.strip().lower(), col.strip().lower())
                    for col in postcode_df.columns
                })
                postcode_df.columns = postcode_df.columns.str.strip().str.lower()
        required_columns = {"postcode", "lat", "lon"}
        if not required_columns.issubset(postcode_df.columns):
            st.sidebar.error(f"❌ Vereiste kolommen niet gevonden. Gevonden: {list(postcode_df.columns)}")
            postcode_df = None
        else:
            st.sidebar.success(f"✅ Postcode-database geladen — {len(postcode_df):,} postcodes")
            # Show a few sample rows so the user can verify the data looks right
            with st.sidebar.expander("Bekijk eerste 5 rijen"):
                st.dataframe(postcode_df[["postcode", "lat", "lon"]].head())
    except Exception as e:
        st.sidebar.error(f"Fout bij inlezen CSV: {e}")
        postcode_df = None
else:
    st.sidebar.info("ℹ️ Geen postcode-database geladen — Nominatim wordt gebruikt als fallback")

def normalize_postcode(postcode):
    return str(postcode).strip().replace(" ", "").upper()


def format_postcode(postcode):
    """Canonical display form for a Dutch postcode: '6631 BJ'.

    Falls back to the stripped input when it doesn't match the 4-digit + 2-letter
    pattern, so non-standard values pass through unchanged.
    """
    p = normalize_postcode(postcode)
    if len(p) == 6 and p[:4].isdigit() and p[4:].isalpha():
        return f"{p[:4]} {p[4:]}"
    return str(postcode).strip()


def parse_coordinate(value):
    if pd.isna(value):
        raise ValueError("Lege coördinaatwaarde")
    text = str(value).strip()
    if not text:
        raise ValueError("Lege coördinaatwaarde")
    return float(text.replace(",", "."))


def segment_cache_key(p1, p2, overview="full"):
    return hashlib.sha256(f"{p1[0]:.6f},{p1[1]:.6f}|{p2[0]:.6f},{p2[1]:.6f}|{overview}".encode()).hexdigest()


def reset_segment_cache_stats():
    with _shelve_lock:
        with shelve.open(SEGMENT_CACHE_FILE, writeback=True) as cache:
            cache["_total_requests"] = 0
            cache["_cache_hits"] = 0


def show_segment_cache_stats():
    try:
        with _shelve_lock:
            with shelve.open(SEGMENT_CACHE_FILE, writeback=False) as cache:
                total_segments = cache.get("_total_requests", 0)
                hits = cache.get("_cache_hits", 0)
        if total_segments > 0:
            percentage = 100 * hits / total_segments
            st.sidebar.caption(f"💾 Segment cache: {hits} hits van {total_segments} ({percentage:.1f}%)")
        else:
            st.sidebar.caption("💾 Segment cache: nog geen gegevens")
    except Exception:
        st.sidebar.caption("⚠️ Kan cache-info niet lezen")


def get_segment_route(p1, p2, overview="full"):
    key = segment_cache_key(p1, p2, overview=overview)

    # Check cache first, then fetch, then write — lock only around shelve I/O
    with _shelve_lock:
        with shelve.open(SEGMENT_CACHE_FILE, writeback=True) as cache:
            cache["_total_requests"] = cache.get("_total_requests", 0) + 1
            if key in cache:
                cache["_cache_hits"] = cache.get("_cache_hits", 0) + 1
                return cache[key]

    # HTTP fetch outside the lock so parallel callers aren't serialized
    coords_str = f"{p1[1]},{p1[0]};{p2[1]},{p2[0]}"
    url = f"{OSRM_BASE_URL}/route/v1/driving/{coords_str}"
    params = {"overview": overview, "geometries": "geojson"}
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        seg_result = response.json()
    except requests.RequestException:
        return None

    if seg_result.get("code") == "Ok" and seg_result.get("routes"):
        with _shelve_lock:
            with shelve.open(SEGMENT_CACHE_FILE, writeback=True) as cache:
                cache[key] = seg_result
        return seg_result
    return None


def build_duration_matrix(points):
    n = len(points)
    matrix = [[0.0] * n for _ in range(n)]
    pairs = [(i, j) for i in range(n) for j in range(n) if i != j]

    def fetch_pair(i, j):
        res = get_segment_route(points[i], points[j], overview="false")
        duration = res["routes"][0]["duration"] if res and res.get("routes") else float("inf")
        return i, j, duration

    with ThreadPoolExecutor(max_workers=min(8, len(pairs))) as executor:
        for i, j, duration in executor.map(lambda p: fetch_pair(*p), pairs):
            matrix[i][j] = duration

    return matrix


def route_duration_from_matrix(route, matrix):
    return sum(matrix[route[i]][route[i + 1]] for i in range(len(route) - 1))


def two_opt_improve(route, matrix):
    best_route = route[:]
    best_time = route_duration_from_matrix(best_route, matrix)
    improved = True

    while improved:
        improved = False
        for i in range(1, len(best_route) - 2):
            for j in range(i + 1, len(best_route) - 1):
                candidate = best_route[:i] + best_route[i:j + 1][::-1] + best_route[j + 1:]
                candidate_time = route_duration_from_matrix(candidate, matrix)
                if candidate_time < best_time:
                    best_route = candidate
                    best_time = candidate_time
                    improved = True
                    break
            if improved:
                break

    return best_route, best_time


def nearest_neighbor_route(start, via, end):
    original_coords = [start] + via + [end]
    original_route = get_osrm_route(original_coords)
    original_time = original_route["routes"][0]["duration"] if original_route and original_route.get("code") == "Ok" else None

    points = [start] + via + [end]
    matrix = build_duration_matrix(points)

    current_index = 0
    remaining_indices = list(range(1, len(points) - 1))
    route_indices = [0]

    while remaining_indices:
        next_index = min(remaining_indices, key=lambda idx: matrix[current_index][idx])
        if matrix[current_index][next_index] == float("inf"):
            return via.copy(), original_coords, original_time
        route_indices.append(next_index)
        remaining_indices.remove(next_index)
        current_index = next_index

    route_indices.append(len(points) - 1)
    improved_indices, _ = two_opt_improve(route_indices, matrix)
    best_coords = [points[i] for i in improved_indices]
    route = get_osrm_route(best_coords)
    best_time = route["routes"][0]["duration"] if route and route.get("code") == "Ok" else None

    if original_time is not None and (best_time is None or best_time >= original_time):
        return via.copy(), original_coords, original_time

    return best_coords[1:-1], best_coords, best_time


def lookup_postcode_coordinates(postcodes, postcode_df=None):
    coord_map = {}
    source_map = {}
    missing_postcodes = []

    for postcode in postcodes:
        coords, source = postcode_to_coords(postcode, postcode_df=postcode_df)
        if coords:
            coord_map[postcode] = coords
            source_map[postcode] = source
        else:
            missing_postcodes.append(postcode)

    return coord_map, missing_postcodes, source_map


def build_route_entry(coords):
    route = get_osrm_route(coords)
    if not route or route.get("code") != "Ok":
        return None

    route_data = route["routes"][0]
    return {
        "coords": coords,
        "route": route,
        "time_minutes": route_data["duration"] / 60,
        "distance_km": route_data["distance"] / 1000,
    }


def optimize_route_with_details(start, via, end, method=None):
    _, best_coords, best_time = find_optimal_route(start, via, end, method=method)
    if not best_coords or best_time is None:
        return None
    return build_route_entry(best_coords)


def normalize_route_entry(route_entry):
    if not route_entry or "route" not in route_entry:
        return None

    route = route_entry.get("route")
    if not route or route.get("code") != "Ok" or not route.get("routes"):
        return None

    route_data = route["routes"][0]
    normalized = dict(route_entry)
    normalized.setdefault("coords", route_entry.get("coords", []))
    normalized["time_minutes"] = normalized.get("time_minutes", route_data["duration"] / 60)
    normalized["distance_km"] = normalized.get("distance_km", route_data["distance"] / 1000)
    return normalized


def postcode_to_coords(postcode, postcode_df=None):
    """Returns ((lat, lon), source) where source is 'csv', 'cache', or 'nominatim'."""
    p = normalize_postcode(postcode)

    # 1. Uploaded CSV has priority
    if postcode_df is not None:
        match = postcode_df[postcode_df["postcode"].str.replace(" ", "").str.upper() == p]
        if not match.empty:
            try:
                lat = parse_coordinate(match.iloc[0]["lat"])
                lon = parse_coordinate(match.iloc[0]["lon"])
                postcode_cache[p] = (lat, lon)
                return (lat, lon), "csv"
            except ValueError:
                return None, "error"

    # 2. In-memory cache (Nominatim results from earlier this session)
    if p in postcode_cache:
        return postcode_cache[p], "cache"

    # 3. Nominatim fallback
    params = {"q": f"{p}, Netherlands", "format": "json", "limit": 1}
    headers = {"User-Agent": "SmartCalc/1.0"}
    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return None, "error"
    if data:
        lat = parse_coordinate(data[0]["lat"])
        lon = parse_coordinate(data[0]["lon"])
        postcode_cache[p] = (lat, lon)
        return (lat, lon), "nominatim"
    return None, "not_found"

def get_osrm_route(coords_list):
    total_distance = 0
    total_duration = 0
    full_coordinates = []
    failed_segments = []

    for i in range(len(coords_list) - 1):
        p1 = coords_list[i]
        p2 = coords_list[i + 1]
        seg_result = get_segment_route(p1, p2, overview="full")

        if seg_result and seg_result.get("routes"):
            total_distance += seg_result["routes"][0]["distance"]
            total_duration += seg_result["routes"][0]["duration"]
            geometry = seg_result["routes"][0].get("geometry")
            if geometry and geometry.get("type") == "LineString":
                full_coordinates.extend(geometry["coordinates"])
        else:
            failed_segments.append((p1, p2))

    if failed_segments:
        return {
            "routes": [],
            "code": "Error",
            "message": f"{len(failed_segments)} segment(en) konden niet worden berekend.",
            "failed_segments": failed_segments,
        }

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
        method = st.session_state.get("global_method", "Auto")

    if len(via) <= 1:
        route_coords = [start] + via + [end]
        route = get_osrm_route(route_coords)
        route_time = route["routes"][0]["duration"] if route and route.get("code") == "Ok" else None
        return via.copy(), route_coords, route_time

    if method == "Auto":
        if len(via) <= MAX_BRUTE_FORCE_STOPS:
            method = "Matrix"
        else:
            method = "Nearest Neighbor"

    if len(via) > MAX_BRUTE_FORCE_STOPS and method in {"Brute-Force", "Matrix"}:
        return nearest_neighbor_route(start, via, end)
    if method == "Nearest Neighbor":
        return nearest_neighbor_route(start, via, end)
    elif method == "Matrix":
        points = [start] + via + [end]
        n = len(points)
        matrix = build_duration_matrix(points)


        best_time = float("inf")
        best_order = via
        best_coords = []

        for perm in permutations(range(1, n - 1)):
            route = [0] + list(perm) + [n - 1]
            time_ = route_duration_from_matrix(route, matrix)
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
            result = get_osrm_route(route_coords)
            if result and result.get("code") == "Ok":
                time_ = result["routes"][0]["duration"]
                if time_ < best_time:
                    best_time = time_
                    best_order = list(perm)
                    best_coords = route_coords
        return best_order, best_coords, best_time


_ZOOM_THRESHOLDS = [
    (0.002, 17), (0.004, 16), (0.008, 15), (0.015, 14), (0.03, 13),
    (0.07, 12), (0.15, 11), (0.35, 10), (0.7, 9), (1.5, 8),
]


def _map_center_zoom(coords):
    if not coords:
        return 52.1326, 5.2913, 7
    try:
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        center_lat = (min(lats) + max(lats)) / 2
        center_lon = (min(lons) + max(lons)) / 2
        if center_lat == 0 and center_lon == 0:
            return 52.1326, 5.2913, 7
        diagonal = math.sqrt((max(lats) - min(lats)) ** 2 + (max(lons) - min(lons)) ** 2)
        zoom = next((z for d, z in _ZOOM_THRESHOLDS if diagonal < d), 7)
        if len(coords) < 30 and zoom < 17:
            zoom = min(zoom + 1, 17)
        return center_lat, center_lon, zoom
    except Exception:
        return 52.1326, 5.2913, 7


def build_route_map(route_orig, route_opt, coords_for_markers, height=600, stop_labels=None, stop_sources=None):
    """Return a Plotly figure with original (red) and optimised (green) route traces.

    stop_labels: list of postcode strings matching coords_for_markers (optional)
    stop_sources: list of source strings ('csv'/'cache'/'nominatim') matching coords_for_markers (optional)
    """
    fig = go.Figure()

    fig.add_trace(go.Scattermap(
        lat=[c[1] for c in route_orig["routes"][0]["geometry"]["coordinates"]],
        lon=[c[0] for c in route_orig["routes"][0]["geometry"]["coordinates"]],
        mode="lines",
        line=dict(width=4, color="red"),
        name="Originele route",
    ))
    fig.add_trace(go.Scattermap(
        lat=[c[1] for c in route_opt["routes"][0]["geometry"]["coordinates"]],
        lon=[c[0] for c in route_opt["routes"][0]["geometry"]["coordinates"]],
        mode="lines",
        line=dict(width=4, color="green"),
        name="Geoptimaliseerde route",
    ))

    # Markers for every stop with labels and source-based colouring
    n = len(coords_for_markers)
    for i, coord in enumerate(coords_for_markers):
        if i == 0:
            label = stop_labels[i] if stop_labels else "Start"
            color = "green"
        elif i == n - 1:
            label = stop_labels[i] if stop_labels else "Eind"
            color = "red"
        else:
            label = stop_labels[i] if stop_labels else f"Stop {i}"
            color = "orange"

        source = stop_sources[i] if stop_sources else ""
        tooltip = f"{label}" + (f" ({source})" if source and source != "csv" else "")

        fig.add_trace(go.Scattermap(
            lat=[coord[0]],
            lon=[coord[1]],
            mode="markers+text",
            marker=dict(size=12, color=color),
            text=[label],
            textposition="top right",
            hovertext=[tooltip],
            hoverinfo="text",
            showlegend=False,
        ))

    center_lat, center_lon, zoom = _map_center_zoom(
        route_opt["routes"][0]["geometry"]["coordinates"]
    )
    fig.update_layout(
        map=dict(style="open-street-map", center={"lat": center_lat, "lon": center_lon}, zoom=zoom),
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=height,
        legend=dict(x=0, y=1),
    )
    return fig


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
        reset_segment_cache_stats()
        via_list = [format_postcode(p) for p in postcodes_via.split(",") if p.strip()]
        postcode_start = format_postcode(postcode_start)
        postcode_end = format_postcode(postcode_end)
        all_postcodes = [postcode_start] + via_list + [postcode_end]

        coord_map, missing_postcodes, source_map = lookup_postcode_coordinates(all_postcodes, postcode_df=postcode_df)
        if missing_postcodes:
            st.error(f"❌ Geen coördinaten gevonden voor postcode(s): {', '.join(missing_postcodes)}")
            st.stop()

        nominatim_used = [p for p, s in source_map.items() if s == "nominatim"]
        if nominatim_used:
            st.warning(f"⚠️ Nominatim gebruikt (geen CSV-treffer) voor: {', '.join(nominatim_used)}")

        start = coord_map[postcode_start]
        end = coord_map[postcode_end]
        via_coords = [coord_map[p] for p in via_list]

        original_coords = [start] + via_coords + [end]
        original_entry = build_route_entry(original_coords)
        if not original_entry:
            st.error("Route kon niet worden berekend.")
            st.stop()

        result_data = {
            "original": original_entry,
            "optimized": None,
        }

        if optimize:
            optimized_entry = optimize_route_with_details(start, via_coords, end, method=st.session_state.get("global_method", "Auto"))
            if not optimized_entry:
                st.error("Geoptimaliseerde route kon niet worden berekend.")
                st.stop()
            result_data["optimized"] = optimized_entry

        show_segment_cache_stats()

        result_data["original"] = normalize_route_entry(result_data.get("original"))
        if result_data.get("optimized"):
            result_data["optimized"] = normalize_route_entry(result_data.get("optimized"))
        st.session_state["result"] = result_data

    # Toon kaarten als resultaat beschikbaar is
    if st.session_state["result"]:
        data = st.session_state["result"]

        if not data["original"]:
            st.warning("⚠️ De opgeslagen routegegevens zijn verouderd. Bereken de route opnieuw.")
            st.session_state["result"] = None
            st.stop()

        # Originele route
        if data["original"]["route"] and data["original"]["route"].get("code") == "Ok":
            route_orig = data["original"]["route"]
            original_coords = data["original"]["coords"]
            tijd_orig = data["original"]["time_minutes"]
            afstand_orig = data["original"]["distance_km"]

            st.subheader("🔵 Oorspronkelijke volgorde")
            st.write(f"🕒 Reistijd: {tijd_orig:.1f} min — 📏 Afstand: {afstand_orig:.2f} km")

        # Geoptimaliseerde route
        if optimize and data["optimized"]:
            tijd_best = data["optimized"]["time_minutes"]
            afstand_best = data["optimized"]["distance_km"]

            st.subheader("🟢 Geoptimaliseerde volgorde")
            st.write(f"🕒 Reistijd: {tijd_best:.1f} min — 📏 Afstand: {afstand_best:.2f} km")

        # Toon kaart
        if data["original"]["route"] and data["original"]["route"].get("code") == "Ok" and (not optimize or data["optimized"]):
            route_orig = data["original"]["route"]
            if optimize and data["optimized"]:
                route_opt = data["optimized"]["route"]
                route_coords = data["optimized"]["coords"]
            else:
                route_opt = route_orig
                route_coords = data["original"]["coords"]

            fig = build_route_map(route_orig, route_opt, route_coords, height=800)
            st.plotly_chart(fig, use_container_width=True)

def process_batch(uploaded_file):
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

    # Reset cache only after user confirms column selection
    reset_segment_cache_stats()
    st.session_state["batch_result_cache"] = {}

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

    progress = st.progress(0, text="Bezig met batchverwerking...")
    total_rows = len(df)

    # Build row inputs list so the worker function is a plain callable with no closures
    row_inputs = []
    for idx, row in df.iterrows():
        van = format_postcode(row["van"])
        naar = format_postcode(row["naar"])
        via = [format_postcode(p) for p in str(row["via"]).split(",") if p.strip()] if pd.notna(row.get("via", "")) else []
        row_inputs.append((idx, van, naar, via, extra_kolommen, temp_df))

    global_method = st.session_state.get("global_method", "Auto")

    def process_row(args):
        idx, van, naar, via, extra_kolommen, temp_df = args
        full_list = [van] + via + [naar]
        coord_map, missing_postcodes, source_map = lookup_postcode_coordinates(full_list, postcode_df=postcode_df)
        coords = [coord_map[p] for p in full_list if p in coord_map]
        reverse_coord_map = {v: k for k, v in coord_map.items()}
        nominatim_used = [p for p, s in source_map.items() if s == "nominatim"]

        dist_orig = time_orig = dist_opt = time_opt = None
        best_coords = []
        route_orig = route_opt = None
        status = "OK"
        error_reason = ""

        if missing_postcodes:
            status = "FOUT"
            error_reason = f"Geen coördinaten gevonden voor: {', '.join(missing_postcodes)}"
        elif len(coords) < 2:
            status = "FOUT"
            error_reason = "Te weinig geldige stops om een route te berekenen."
        else:
            route_orig = get_osrm_route(coords)
            if route_orig and route_orig.get("code") == "Ok":
                dist_orig = route_orig["routes"][0]["distance"] / 1000
                time_orig = route_orig["routes"][0]["duration"] / 60
            else:
                status = "FOUT"
                error_reason = route_orig.get("message", "Originele route kon niet worden berekend.") if route_orig else "Originele route kon niet worden berekend."

            if status == "OK":
                optimized_entry = optimize_route_with_details(coords[0], coords[1:-1], coords[-1], method=global_method)
                if optimized_entry:
                    best_coords = optimized_entry["coords"]
                    route_opt = optimized_entry["route"]
                    dist_opt = optimized_entry["distance_km"]
                    time_opt = optimized_entry["time_minutes"]
                else:
                    status = "FOUT"
                    error_reason = "Geoptimaliseerde route kon niet worden berekend."

        result = {
            "RouteID": idx + 1,
            "Status": status,
            "Foutmelding": error_reason,
            "Van": van,
            "Via Origineel": ", ".join(via),
            "Via Geoptimaliseerd": ", ".join([reverse_coord_map.get(c, f"{c[0]:.5f},{c[1]:.5f}") for c in best_coords[1:-1]]) if best_coords else "",
            "Naar": naar,
            "Afstand Origineel (km)": round(dist_orig, 2) if dist_orig is not None else None,
            "Tijd Origineel (min)": round(time_orig, 1) if time_orig is not None else None,
            "Afstand Optimaal (km)": round(dist_opt, 2) if dist_opt is not None else None,
            "Tijd Optimaal (min)": round(time_opt, 1) if time_opt is not None else None,
            "Tijdswinst (min)": round(time_orig - time_opt, 1) if time_orig is not None and time_opt is not None else None,
            "Nominatim gebruikt": ", ".join(nominatim_used) if nominatim_used else "",
            **{col: temp_df.loc[idx, col] if col in temp_df.columns else "" for col in extra_kolommen},
        }
        coords_all_opt = route_opt["routes"][0]["geometry"]["coordinates"] if route_opt and route_opt.get("routes") else []
        cache_entry = {
            "status": status,
            "error_reason": error_reason,
            "route_orig": route_orig,
            "route_opt": route_opt,
            "coord_map": coord_map,
            "source_map": source_map,
            "full_list": full_list,
            "coords_orig": coords,
            "coords_opt": best_coords,
            "coords_all_opt": coords_all_opt,
        }
        return idx, result, cache_entry

    results_map = {}
    cache_map = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_idx = {executor.submit(process_row, args): args[0] for args in row_inputs}
        for future in as_completed(future_to_idx):
            idx, result, cache_entry = future.result()
            results_map[idx] = result
            cache_map[idx + 1] = cache_entry
            completed += 1
            progress.progress(completed / total_rows, text=f"Verwerkt {completed} van {total_rows}")

    # Write cache results from main thread; preserve original row order
    for route_id, entry in cache_map.items():
        st.session_state["batch_result_cache"][route_id] = entry

    progress.empty()
    st.success("✅ Kolomkeuze bevestigd, planner gestart...")

    ordered_results = [results_map[idx] for idx, *_ in row_inputs if idx in results_map]
    return pd.DataFrame(ordered_results)


with tab2:
    uploaded = st.file_uploader("Upload een Excel-bestand met kolommen: Van, Via, Naar", type=["xlsx"])
    if uploaded:
        if st.session_state.get("batch_file_id") != uploaded.file_id:
            df_result = process_batch(uploaded)
            if not df_result.empty:
                # Only lock the file_id once we have real results — this allows
                # the form-submit rerun to still call process_batch.
                st.session_state["batch_file_id"] = uploaded.file_id
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

        # --- Per-route kaartviewer ---
        cache = st.session_state.get("batch_result_cache", {})
        ok_rows = df_result[df_result["Status"] == "OK"]
        if not ok_rows.empty and cache:
            st.subheader("🗺️ Routekaart")
            options = {
                int(row["RouteID"]): f"Route {int(row['RouteID'])}: {row['Van']} → {row['Naar']}"
                for _, row in ok_rows.iterrows()
            }
            selected_id = st.selectbox(
                "Selecteer een route om te bekijken",
                list(options.keys()),
                format_func=lambda k: options[k],
            )
            entry = cache.get(selected_id)
            if entry and entry["status"] == "OK" and entry.get("route_orig") and entry.get("route_opt"):
                row = ok_rows[ok_rows["RouteID"] == selected_id].iloc[0]
                col1, col2 = st.columns(2)
                with col1:
                    st.metric(
                        "Originele route",
                        f"{row['Tijd Origineel (min)']} min",
                        f"{row['Afstand Origineel (km)']} km",
                        delta_color="off",
                    )
                with col2:
                    tijdwinst = row.get("Tijdswinst (min)")
                    st.metric(
                        "Geoptimaliseerde route",
                        f"{row['Tijd Optimaal (min)']} min",
                        f"-{tijdwinst} min" if tijdwinst and tijdwinst > 0 else "geen winst",
                        delta_color="inverse",
                    )
                coords_display = entry["coords_opt"] or entry["coords_orig"]

                # Build stop labels and sources in the same order as coords_display
                coord_map = entry.get("coord_map", {})
                source_map = entry.get("source_map", {})
                full_list = entry.get("full_list", [])
                reverse_coord_map = {v: k for k, v in coord_map.items()}
                stop_labels = [reverse_coord_map.get(c, "?") for c in coords_display]
                stop_sources = [source_map.get(reverse_coord_map.get(c, ""), "") for c in coords_display]

                fig = build_route_map(
                    entry["route_orig"], entry["route_opt"], coords_display,
                    stop_labels=stop_labels, stop_sources=stop_sources,
                )
                st.plotly_chart(fig, use_container_width=True)

                # Coordinate detail table — shows exactly which source was used per stop
                if full_list and coord_map:
                    with st.expander("🔍 Coördinaten per stop"):
                        rows = []
                        for pc in full_list:
                            coords = coord_map.get(pc)
                            src = source_map.get(pc, "onbekend")
                            if coords:
                                rows.append({"Postcode": pc, "Lat": coords[0], "Lon": coords[1], "Bron": src})
                            else:
                                rows.append({"Postcode": pc, "Lat": None, "Lon": None, "Bron": "niet gevonden"})
                        st.dataframe(pd.DataFrame(rows), use_container_width=True)

    # --- Segmentenmatrix opbouwen tabblad ---
    with st.expander("🗂️ Segmentenmatrix opbouwen (optioneel, vooraf vullen van cache)"):
        matrix_file = st.file_uploader("Upload Excel met kolommen: Van, Via, Naar", type=["xlsx"], key="matrix")
        if matrix_file and st.button("🧱 Bouw segmentcache"):
            df_matrix = pd.read_excel(matrix_file)
            unique_postcodes = set()
            for _, row in df_matrix.iterrows():
                unique_postcodes.add(format_postcode(row["Van"]))
                unique_postcodes.add(format_postcode(row["Naar"]))
                if pd.notna(row["Via"]):
                    via_codes = [format_postcode(p) for p in str(row["Via"]).split(",") if p.strip()]
                    unique_postcodes.update(via_codes)

            coords_dict = {}
            for p in unique_postcodes:
                c, _ = postcode_to_coords(p, postcode_df=postcode_df)
                if c:
                    coords_dict[p] = c

            all_pairs = []
            keys_seen = set()
            for a in coords_dict.values():
                for b in coords_dict.values():
                    if a != b:
                        key = segment_cache_key(a, b, overview="false")
                        if key not in keys_seen:
                            keys_seen.add(key)
                            all_pairs.append((a, b))
            st.write(f"🔍 {len(all_pairs)} unieke segmenten te verwerken...")
            progress = st.progress(0, text="Bouwen van segmentcache...")
            added = 0

            def fetch_if_missing(pair):
                a, b = pair
                key = segment_cache_key(a, b, overview="false")
                with _shelve_lock:
                    with shelve.open(SEGMENT_CACHE_FILE, writeback=False) as cache:
                        already_cached = key in cache
                if already_cached:
                    return False
                res = get_segment_route(a, b, overview="false")
                return res is not None

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(fetch_if_missing, pair): i for i, pair in enumerate(all_pairs)}
                for count, future in enumerate(as_completed(futures), start=1):
                    if future.result():
                        added += 1
                    progress.progress(count / len(all_pairs), text=f"Verwerkt {count} van {len(all_pairs)} segmenten...")
            st.success(f"✅ Segmentcache aangevuld met {added} nieuwe routes.")
        
