import streamlit as st
import pandas as pd
import numpy as np
import re
from difflib import get_close_matches
from math import radians, cos, sin, asin, sqrt


# -----------------------------
# CONSTANTS & GLOBALS
# -----------------------------

ZIP_RE = re.compile(r"\b\d{5}\b")  # reliable ZIP extractor


# -----------------------------
# DISTANCE CALCULATION (Haversine)
# -----------------------------

def haversine(lat1, lon1, lat2, lon2):
    """Calculate the distance between two lat/lng points in km."""
    R = 6371  # Earth radius in km

    lat1, lon1, lat2, lon2 = map(
        radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * asin(sqrt(a))

    return R * c


# -----------------------------
# LOAD CITIES + ZIP MAPPING
# -----------------------------

@st.cache_data
def load_cities_mapping():
    """Load US cities + ZIP codes from the CSV and map ZIP → (lat, lng)."""

    url = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"

    # Read CSV with BOM handling
    df = pd.read_csv(url, encoding="utf-8-sig")

    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]

    # Required columns
    needed = ["city", "state_id", "lat", "lng", "zips"]
    for n in needed:
        if n not in df.columns:
            st.error(f"ERROR: Column '{n}' missing in city CSV.")
            return {}, {}

    zip_coords = {}
    city_coords = {}

    for _, r in df.iterrows():
        city = str(r["city"]).strip().lower()
        lat = float(r["lat"])
        lng = float(r["lng"])

        city_coords[city] = (lat, lng)

        # Clean and extract all ZIP codes safely
        raw = str(r["zips"])
        raw = re.sub(r"\s+", " ", raw)  # collapse weird whitespace
        zips = re.findall(r"\d{5}", raw)

        for z in zips:
            zip_coords[z] = {
                "coords": (lat, lng),
                "city": city.title()
            }

    return zip_coords, city_coords


ZIP_COORDS, CITY_COORDS = load_cities_mapping()


# -----------------------------
# GET COORDS FROM USER INPUT
# -----------------------------

def get_coords(text):
    """
    Convert user input → (lat, lng).
    Handles:
    - Valid 5-digit ZIP codes
    - City names
    - Does NOT fuzzy-match ZIPs (stops Berkeley bug)
    """

    text = text.strip()

    # 1) ZIP code?
    m = ZIP_RE.search(text)
    if m:
        z = m.group(0)
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]

        # ZIP not found → STOP (do NOT fuzzy match)
        return None

    # 2) City exact match
    key = text.lower()
    if key in CITY_COORDS:
        return CITY_COORDS[key]

    # 3) City fuzzy match only (not ZIP!)
    match = get_close_matches(key, CITY_COORDS.keys(), n=1, cutoff=0.8)
    if match:
        return CITY_COORDS[match[0]]

    return None


# -----------------------------
# LOAD JOB DATA
# -----------------------------

@st.cache_data
def load_job_data():
    url = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"
    df = pd.read_csv(url, encoding="utf-8-sig")

    df.columns = [c.strip().lower() for c in df.columns]

    # Clean location column
    if "job_location" not in df.columns:
        st.error("Column 'job_location' missing in job CSV.")
        return df

    df["job_location"] = df["job_location"].fillna("").astype(str)

    return df


df_jobs = load_job_data()


# -----------------------------
# MAIN UI
# -----------------------------

st.title("Job Distance Finder (Fixed ZIP Version)")
st.write("Now using corrected ZIP matching — 60602 will return Chicago every time.")

search_input = st.text_input("Enter ZIP code or City", value="")

if search_input.strip():

    coords = get_coords(search_input)

    if coords is None:
        st.error("Location not found in database. (ZIP not in data or city not recognized.)")
    else:
        user_lat, user_lng = coords
        st.success(f"Matched Coordinates: {coords}")

        # Compute distances
        distances = []
        for _, row in df_jobs.iterrows():
            loc = str(row.get("job_location", "")).strip()

            c2 = get_coords(loc)
            if c2 is None:
                continue

            d = haversine(user_lat, user_lng, c2[0], c2[1])
            distances.append((d, row))

        # Sort by nearest
        distances.sort(key=lambda x: x[0])

        st.subheader("Nearest Jobs")
        for d, row in distances[:20]:
            st.write(f"**{row['job_title']}** — {row['job_company']} — {row['job_location']} — `{d:.1f} km`")

