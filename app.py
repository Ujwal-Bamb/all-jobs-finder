import streamlit as st
import pandas as pd
import pydeck as pdk
import re
import requests
import chardet
from io import StringIO
from math import radians, sin, cos, sqrt, atan2
from difflib import get_close_matches

# ---------------------- Streamlit Setup ----------------------
st.set_page_config(page_title="😊 Keep Smiling (USA)", layout="wide")

# ---------------------- Custom CSS ---------------------------
st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #e0f2ff, #f5f7ff);
    font-family: 'Segoe UI', sans-serif;
}
.job-card {
    background: white;
    border-radius: 12px;
    padding: 16px;
    margin-top: 10px;
    margin-bottom: 10px;
    box-shadow: 0 4px 10px rgba(0,0,0,0.06);
}
.job-title {
    font-size: 18px;
    font-weight: bold;
    color: #1e3a8a;
    margin-bottom: 8px;
}
.job-field {
    font-size: 15px;
    margin-bottom: 4px;
}
</style>
""", unsafe_allow_html=True)


# ---------------------- Raw CSV URLs -------------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"


# ---------------------- CSV Loader ---------------------------
def load_csv(url):
    try:
        r = requests.get(url, timeout=10)
        raw = r.content
        enc = chardet.detect(raw)['encoding'] or "utf-8"
        text = raw.decode(enc, errors="replace")
        return pd.read_csv(StringIO(text))
    except Exception as e:
        st.error(f"Error loading CSV: {e}")
        return pd.DataFrame()


# ---------------------- Load Cities --------------------------
@st.cache_data(ttl=3600)
def load_city_data():
    df = load_csv(CITIES_URL)
    if df.empty:
        return {}, {}

    cities = {}
    zip_coords = {}

    for _, row in df.iterrows():
        try:
            city = str(row["city"]).strip().lower()
            lat = float(row["lat"])
            lng = float(row["lng"])
            cities[city] = (lat, lng)

            if pd.notna(row["zips"]):
                for z in str(row["zips"]).split():
                    zip_coords[z] = {"coords": (lat, lng), "city": city.title()}
        except:
            continue

    return cities, zip_coords


CITIES, ZIP_COORDS = load_city_data()


# ---------------------- Load Jobs ---------------------------
@st.cache_data(ttl=1800)
def load_jobs():
    df = load_csv(JOBS_URL)
    if df.empty:
        return df

    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_", regex=False)
    return df


jobs = load_jobs()


# ---------------------- Text Parser -------------------------
ZIP_RE = re.compile(r"\b(\d{5})\b")


def resolve_coords(text):
    """Returns (lat, lng) from ZIP or city."""
    if not text:
        return None

    # Try ZIP first
    m = ZIP_RE.search(text)
    if m:
        z = m.group(1)
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]

    # Try city name
    city = text.split(",")[0].strip().lower()
    if city in CITIES:
        return CITIES[city]

    # Fuzzy fallback
    m = get_close_matches(city, CITIES.keys(), n=1, cutoff=0.72)
    if m:
        return CITIES[m[0]]

    return None


# ---------------------- Haversine ---------------------------
def distance_miles(c1, c2):
    if not c1 or not c2:
        return float("inf")
    R = 3958.8
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ---------------------- UI ---------------------------
st.title("😊 Keep Smiling — USA Job Finder")
st.write("Search caregiver job listings by city or ZIP across the USA.")

if jobs.empty:
    st.error("Job data could not be loaded.")
    st.stop()

# Detect location columns
LOCATION_COL = "client_city"
ZIP_COL = "zip_code"

jobs[LOCATION_COL] = jobs[LOCATION_COL].astype(str).str.strip()
jobs["location_norm"] = jobs[LOCATION_COL].str.lower()


# Search Controls
col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    search_type = st.radio("Search by", ["City", "ZIP"], horizontal=True)

with col2:
    query = st.text_input("Enter City or ZIP")

with col3:
    radius = st.slider("Radius (miles)", 1, 300, 50)

search = st.button("🔎 Find Jobs", use_container_width=True)


# ---------------------- Search Action ------------------------
if search:

    # Resolve user coordinates
    if search_type == "ZIP":
        m = ZIP_RE.search(query)
        if not m:
            st.error("Please enter a valid 5-digit ZIP.")
            st.stop()
        z = m.group(1)
        user_coords = ZIP_COORDS.get(z, {}).get("coords")
    else:
        user_coords = resolve_coords(query)

    if not user_coords:
        st.error("Location not found.")
        st.stop()

    # Build job coordinates
    jobs["coords"] = jobs["location_norm"].apply(resolve_coords)
    valid_jobs = jobs.dropna(subset=["coords"]).copy()

    valid_jobs["distance"] = valid_jobs["coords"].apply(lambda c: distance_miles(user_coords, c))
    nearby = valid_jobs[valid_jobs["distance"] <= radius].sort_values("distance")

    if nearby.empty:
        st.warning("No jobs found in that radius.")
        st.stop()

    st.success(f"Found {len(nearby)} job(s) near {query}!")

    # ---------------------- Display Jobs ----------------------
    for _, r in nearby.iterrows():
        st.markdown(
            f"""
            <div class='job-card'>
                <div class='job-title'>🏥 {r['client_name']}</div>
                <div class='job-field'><b>📍 City:</b> {r['client_city']}</div>
                <div class='job-field'><b>🧭 Distance:</b> {r['distance']:.1f} miles</div>
                <div class='job-field'><b>💬 Language:</b> {r.get('language','N/A')}</div>
                <div class='job-field'><b>💰 Pay Rate:</b> {r.get('pay_rate','N/A')}</div>
                <div class='job-field'><b>👤 Gender:</b> {r.get('gender','N/A')}</div>
                <div class='job-field'><b>📝 Notes:</b> {r.get('order_notes','N/A')}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

    # ---------------------- Map ----------------------
    map_df = pd.DataFrame(
        [{"lat": c[0], "lon": c[1]} for c in nearby["coords"].tolist()]
    )

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position='[lon, lat]',
        get_radius=600,
        pickable=True,
    )

    view_state = pdk.ViewState(
        latitude=user_coords[0],
        longitude=user_coords[1],
        zoom=6,
    )

    st.subheader("🗺️ Job Locations Map")
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
    
