import streamlit as st
import pandas as pd
import numpy as np
import re
from difflib import get_close_matches
from math import radians, cos, sin, asin, sqrt


# -----------------------------
# CONSTANTS
# -----------------------------

ZIP_RE = re.compile(r"\b\d{5}\b")  # strict ZIP extractor


# -----------------------------
# DISTANCE FUNCTION
# -----------------------------

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two lat/lng points (km)."""
    R = 6371  # Earth radius in km
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))


# -----------------------------
# LOAD CITIES CSV (ZIP → Lat/Lng)
# -----------------------------

@st.cache_data
def load_city_zip_mapping():
    url = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
    df = pd.read_csv(url, encoding="utf-8-sig")

    df.columns = [c.strip().lower() for c in df.columns]

    if not {"city", "lat", "lng", "zips"}.issubset(df.columns):
        st.error("City CSV missing required columns.")
        st.stop()

    zip_coords = {}
    city_coords = {}

    for _, row in df.iterrows():
        city = str(row["city"]).strip().lower()
        lat, lng = float(row["lat"]), float(row["lng"])
        city_coords[city] = (lat, lng)

        # Extract clean ZIP list
        raw = str(row["zips"])
        clean = re.sub(r"\s+", " ", raw)
        zips = re.findall(r"\d{5}", clean)

        for z in zips:
            zip_coords[z] = {
                "coords": (lat, lng),
                "city": city.title()
            }

    return zip_coords, city_coords


ZIP_COORDS, CITY_COORDS = load_city_zip_mapping()


# -----------------------------
# LOCATION → COORDS FUNCTION
# -----------------------------

def get_coords(user_input):
    """Convert user input into (lat, lng) without ZIP fuzzy matching."""
    user_input = user_input.strip()

    # ZIP?
    m = ZIP_RE.search(user_input)
    if m:
        zip_code = m.group(0)
        return ZIP_COORDS.get(zip_code, {}).get("coords")

    # City?
    city_key = user_input.lower()
    if city_key in CITY_COORDS:
        return CITY_COORDS[city_key]

    # Fuzzy match only for cities (not ZIP)
    match = get_close_matches(city_key, CITY_COORDS.keys(), n=1, cutoff=0.8)
    if match:
        return CITY_COORDS[match[0]]

    return None


# -----------------------------
# LOAD JOB CSV
# -----------------------------

@st.cache_data
def load_jobs():
    url = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"
    df = pd.read_csv(url, encoding="utf-8-sig")

    df.columns = [c.strip().lower() for c in df.columns]

    needed = ["client name", "client city", "state", "zip code"]
    for col in needed:
        if col not in df.columns:
            st.error(f"Missing column in job CSV: {col}")
            st.stop()

    df["client city"] = df["client city"].fillna("").astype(str)
    df["zip code"] = df["zip code"].fillna("").astype(str)

    return df


jobs_df = load_jobs()


# -----------------------------
# STREAMLIT UI
# -----------------------------

st.title("🔥 Fixed Job Distance Finder (ZIP-safe)")

user_query = st.text_input("Enter ZIP or City", "")

if user_query.strip():
    coords = get_coords(user_query)

    if coords is None:
        st.error("❌ Location not found. ZIP not in database or city not recognized.")
        st.stop()

    user_lat, user_lng = coords
    st.success(f"Location matched → {coords}")

    all_results = []

    for _, row in jobs_df.iterrows():
        # Primary: try ZIP → coords
        zip_code = str(row["zip code"]).strip()

        job_coords = None
        if re.fullmatch(r"\d{5}", zip_code) and zip_code in ZIP_COORDS:
            job_coords = ZIP_COORDS[zip_code]["coords"]

        # Secondary: use city name
        if job_coords is None:
            job_coords = get_coords(row["client city"])

        if job_coords is None:
            continue

        d = haversine(user_lat, user_lng, job_coords[0], job_coords[1])
        all_results.append((d, row))

    if not all_results:
        st.warning("No matching job locations found.")
        st.stop()

    all_results.sort(key=lambda x: x[0])

    st.subheader("📍 Closest Jobs")

    for dist, row in all_results[:50]:
        st.write(
            f"""
            **{row['client name']}**  
            📍 {row['client city']}  
            🧭 Distance: `{dist:.1f} km`  
            💬 Language: {row.get('language', '')}  
            💰 Pay Rate: {row.get('pay rate', '')}  
            👤 Gender: {row.get('gender', '')}  
            📝 Notes: {row.get('order notes', '')}
            """
        )
        
