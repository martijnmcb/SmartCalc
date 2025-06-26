import pandas as pd
import requests
import shelve
import argparse
from hashlib import sha256
from tqdm import tqdm
#python3 cache_builder.py --excel batch.xlsx --postcode_csv postcode.csv --verbose 
def postcode_to_coords(postcode, df):
    p = postcode.replace(" ", "").upper()
    match = df[df["postcode"].str.replace(" ", "").str.upper() == p]
    if not match.empty:
        lat = float(match.iloc[0]["lat"])
        lon = float(match.iloc[0]["lon"])
        return lat, lon
    return None

def build_segment_cache(excel_path, postcode_csv, limit=None, verbose=False):
    print("📄 Laad Excel-bestand en postcode-coördinaten...")
    df_routes = pd.read_excel(excel_path)
    df_postcode = pd.read_csv(postcode_csv, dtype=str)
    df_postcode.columns = df_postcode.columns.str.strip().str.lower()

    print("🔍 Verzamel unieke postcodes...")
    unique_postcodes = set()
    for _, row in df_routes.iterrows():
        unique_postcodes.add(row["Van"])
        unique_postcodes.add(row["Naar"])
        if pd.notna(row["Via"]):
            via_codes = [p.strip() for p in str(row["Via"]).split(",")]
            unique_postcodes.update(via_codes)

    coords_dict = {}
    for p in unique_postcodes:
        c = postcode_to_coords(p, df_postcode)
        if c:
            coords_dict[p] = c
        else:
            print(f"❌ Geen coördinaat voor: {p}")

    print(f"📌 {len(coords_dict)} geldige postcodes, genereer segmenten...")
    all_pairs = []
    keys_seen = set()
    for a in coords_dict.values():
        for b in coords_dict.values():
            if a != b:
                key = sha256("|".join([f"{a[0]:.5f},{a[1]:.5f}", f"{b[0]:.5f},{b[1]:.5f}"]).encode()).hexdigest()
                if key not in keys_seen:
                    keys_seen.add(key)
                    all_pairs.append((a, b))
    if limit:
        all_pairs = all_pairs[:limit]
        print(f"✂️ Beperkt tot eerste {limit} segmenten.")

    print(f"🚀 Start ophalen via OSRM voor {len(all_pairs)} segmenten...")
    with shelve.open("segment_cache.db", writeback=True) as segment_cache:
        for a, b in tqdm(all_pairs, desc="Bezig"):
            key = sha256("|".join([f"{a[0]:.5f},{a[1]:.5f}", f"{b[0]:.5f},{b[1]:.5f}"]).encode()).hexdigest()
            if key not in segment_cache:
                coord_string = f"{a[1]},{a[0]};{b[1]},{b[0]}"
                url = f"http://localhost:5000/route/v1/driving/{coord_string}?overview=false"
                res = requests.get(url)
                if res.status_code == 200:
                    segment_cache[key] = res.json()
                    if verbose:
                        print(f"✅ Toegevoegd: {key}")
                else:
                    print(f"⚠️ Fout bij ophalen: {a} -> {b}")
    print("✅ Klaar! Segmentcache is bijgewerkt.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bouw OSRM segmentcache op basis van Excel en postcode CSV.")
    parser.add_argument("--excel", required=True, help="Pad naar Excelbestand met kolommen Van, Via, Naar")
    parser.add_argument("--postcode_csv", required=True, help="Pad naar CSV met kolommen postcode, lat, lon")
    parser.add_argument("--limit", type=int, help="Beperk tot eerste N segmenten (optioneel)")
    parser.add_argument("--verbose", action="store_true", help="Meer logging tonen")
    args = parser.parse_args()

    build_segment_cache(args.excel, args.postcode_csv, limit=args.limit, verbose=args.verbose)