import streamlit as st
import pandas as pd
import requests
import chardet
from io import StringIO
from math import radians, sin, cos, sqrt, atan2
import re

# -------------------------
# URLs for your CSV files
# -------------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

ZIP_RE = re.compile(r"\b(\d{5})\b")


# -------------------------
# Load CSV with encoding detection
# -------------------------
def read_csv_url(url):
    r = requests.get(url, timeout=20)
    raw = r.content
    enc = chardet.detect(raw)['encoding'] or "utf-8"
    return pd.read_csv(StringIO(raw.decode(enc, errors="replace")))


# -------------------------
# Preload city + ZIP database
# -------------------------
@st.cache_data
def load_city_database():
    df = read_csv_url(CITIES_URL)

    # Required columns:
    # city, city_ascii, state_id, state_name, lat, lng, zips
    df['city_ascii'] = df['city_ascii'].astype(str).str.strip()
    df['state_id'] = df['state_id'].astype(str).str.strip()
    df['state_name'] = df['state_name'].astype(str).str.strip()

    city_state_lookup = {}
    zip_lookup = {}

    for _, row in df.iterrows():
        city = row['city_ascii'].strip().lower()
        state_id = row['state_id'].strip().lower()
        state_name = row['state_name'].strip().lower()

        lat = float(row['lat'])
        lng = float(row['lng'])

        # City lookup keys
        city_state_lookup[(city, state_id)] = (lat, lng)
        city_state_lookup[(city, state_name)] = (lat, lng)

        # ZIP lookup
        zip_list = re.findall(r"\d{5}", str(row['zips']))
        for z in zip_list:
            zip_lookup[z] = (lat, lng)

    return city_state_lookup, zip_lookup


city_state_lookup, zip_lookup = load_city_database()


# -------------------------
# Load job file
# -------------------------
@st.cache_data
def load_jobs():
    df = read_csv_url(JOBS_URL)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df = df.fillna("")
    return df


jobs_df = load_jobs()


# -------------------------
# Distance calculation
# -------------------------
def haversine(coords1, coords2):
    lat1, lon1 = coords1
    lat2, lon2 = coords2
    R = 3958.8  # miles

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1

    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# -------------------------
# Resolve user input → coordinates
# -------------------------
def resolve_user_location(text):
    text = text.strip()

    # If ZIP
    if text.isdigit():
        if text in zip_lookup:
            return zip_lookup[text]
        return None

    # If "City, State"
    if "," in text:
        city, state = [p.strip().lower() for p in text.split(",", 1)]
        key = (city, state)
        return city_state_lookup.get(key)

    # City only
    city_lower = text.lower()
    for (c, s), coords in city_state_lookup.items():
        if c == city_lower:
            return coords

    return None


# -------------------------
# Resolve job row → coordinates
# -------------------------
def job_coordinates(row):
    # Try ZIP first
    zip_code = row["zip_code"]
    m = ZIP_RE.search(zip_code)
    if m:
        z = m.group(1)
        if z in zip_lookup:
            return zip_lookup[z]

    # Then try city + state
    city = row["client_city"].strip().lower()
    state = row["state"].strip().lower()

    # Try both forms (OH or Ohio)
    for key in [(city, state)]:
        if key in city_state_lookup:
            return city_state_lookup[key]

    return None


# -------------------------
# Streamlit UI
# -------------------------
st.title("Job Finder (Correct ZIP & State Matching)")

query = st.text_input("Enter ZIP or City (e.g., 60602 or Hilliard, OH)", "")
radius = st.slider("Radius (miles)", 5, 500, 50)

if st.button("Search"):
    if not query:
        st.warning("Enter a location.")
        st.stop()

    user_coords = resolve_user_location(query)
    if not user_coords:
        st.error("Location not found.")
        st.stop()

    st.success(f"Location matched at: {user_coords}")

    # Compute all job coords
    jobs = jobs_df.copy()
    jobs["coords"] = jobs.apply(job_coordinates, axis=1)
    jobs = jobs[jobs["coords"].notna()].copy()

    # Distance
    jobs["distance"] = jobs["coords"].apply(lambda c: haversine(user_coords, c))

    # Filter
    nearby = jobs[jobs["distance"] <= radius].sort_values("distance")

    if nearby.empty:
        st.warning("No jobs found in that radius.")
        st.stop()

    st.success(f"Found {len(nearby)} jobs")

    for _, r in nearby.iterrows():
        st.markdown(
            f"""
            ### 🏥 {r['client_name']}
            **📍 Location:** {r['client_city']}, {r['state']}  
            **🧭 Distance:** {r['distance']:.1f} miles  
            **💬 Language:** {r['language']}  
            **💰 Pay Rate:** {r['pay_rate']}  
            **👤 Gender:** {r['gender']}  
            **📝 Notes:** {r['order_notes']}  
            ---
            """
        )
        
