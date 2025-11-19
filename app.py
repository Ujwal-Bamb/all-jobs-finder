import streamlit as st
import pandas as pd
import pydeck as pdk
import re
from io import StringIO
from math import radians, sin, cos, sqrt, atan2
from difflib import get_close_matches
import requests

# ------------------ Streamlit Setup ------------------
st.set_page_config(page_title="😊 Keep Smiling", layout="wide")

# ------------------ Custom CSS ------------------
st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #e0f2ff, #f5f7ff);
    font-family: 'Segoe UI', sans-serif;
}
.job-card {
    background: white;
    border-radius: 12px;
    padding: 18px;
    margin: 10px 0;
    box-shadow: 0 4px 10px rgba(37,99,235,0.1);
}
.job-card h4 {
    color: #1e3a8a;
    margin-bottom: 8px;
}
.job-card p {
    margin: 4px 0;
    font-size: 15px;
}
</style>
""", unsafe_allow_html=True)

# ------------------ Load Cities CSV ------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"

@st.cache_data
def load_cities():
    df = pd.read_csv(CITIES_URL)
    cities = {}
    zip_coords = {}
    for _, r in df.iterrows():
        city_name = str(r['city']).strip().lower()
        lat = r.get('lat')
        lng = r.get('lng')
        if pd.notna(lat) and pd.notna(lng):
            cities[city_name] = (lat, lng)
        zips_field = r.get('zips')
        if pd.notna(zips_field):
            for z in str(zips_field).split():
                zip_coords[z.strip()] = {"coords": (lat, lng), "city": city_name.title()}
    return cities, zip_coords

CITIES, ZIP_COORDS = load_cities()

# ------------------ Load Jobs CSV ------------------
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

@st.cache_data
def load_jobs():
    df = pd.read_csv(JOBS_URL)
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    df['location'] = df['client_city'].astype(str).str.strip().str.lower()
    client_col = next((c for c in df.columns if 'client' in c), None)
    df['client'] = df[client_col] if client_col else "Unknown Client"
    return df

jobs = load_jobs()

# ------------------ Coordinate Resolver ------------------
def get_coords(name):
    if not name:
        return None
    name = str(name).strip().lower()
    zip_match = re.search(r"\b\d{5}\b", name)
    if zip_match:
        z = zip_match.group()
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]
    base_name = name.split(",")[0].strip()
    if base_name in CITIES:
        return CITIES[base_name]
    match = get_close_matches(base_name, CITIES.keys(), n=1, cutoff=0.75)
    return CITIES[match[0]] if match else None

# ------------------ Haversine Distance ------------------
def haversine(c1, c2):
    if not c1 or not c2:
        return float('inf')
    R = 3958.8
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# ------------------ Main UI ------------------
st.title("😊 Keep Smiling")
st.write("Search caregiver job listings by city or ZIP across the US.")

if jobs.empty:
    st.error("Job data could not be loaded.")
    st.stop()

# ------------------ Search Controls ------------------
st.markdown("### 🔍 Search Jobs Near You")
col1, col2, col3 = st.columns([2,2,1])

with col1:
    search_type = st.radio("Search by", ["City", "ZIP Code"], horizontal=True)

with col2:
    query = st.text_input("Enter City or ZIP", "")

with col3:
    radius = st.slider("Radius (miles)", 1, 200, 40)

# Show detected city for ZIP
if search_type == "ZIP Code" and query.isdigit() and len(query) == 5:
    if query in ZIP_COORDS:
        city_name = ZIP_COORDS[query]["city"]
        st.info(f"📍 ZIP {query} corresponds to **{city_name}**.")
    else:
        st.warning("⚠️ ZIP code not found in mapping.")

# ------------------ Search Trigger ------------------
search_clicked = st.button("🔎 Find Jobs", use_container_width=True)
if query and st.session_state.get("query_entered") != query:
    st.session_state["query_entered"] = query
    search_clicked = True

if search_clicked:
    if not query.strip():
        st.warning("Please enter a city or ZIP code.")
        st.stop()

    if search_type == "ZIP Code":
        if not query.isdigit() or len(query) != 5:
            st.error("Please enter a valid 5-digit ZIP code.")
            st.stop()
        zip_info = ZIP_COORDS.get(query)
        user_coords = zip_info["coords"] if zip_info else None
    else:
        user_coords = get_coords(query)

    if not user_coords:
        st.error("⚠️ Could not find that city or ZIP in mapping.")
        st.stop()

    # Resolve coordinates for all jobs
    jobs["coords"] = jobs["location"].apply(get_coords)
    jobs = jobs.dropna(subset=["coords"])
    jobs["distance"] = jobs["coords"].apply(lambda c: haversine(user_coords, c))
    nearby = jobs[jobs["distance"] <= radius].sort_values("distance")

    if nearby.empty:
        st.warning(f"No jobs found within {radius} miles of {query}.")
    else:
        st.success(f"🎯 Found {len(nearby)} job(s) within {radius} miles!")
        for _, row in nearby.iterrows():
            client = row.get("client", "Unknown Client")
            loc = row.get("location", "Unknown Location").title()
            dist = row["distance"]
            with st.expander(f"🏥 {client} — {loc} ({dist:.1f} miles)"):
                st.markdown(f"""
                <div class='job-card'>
                    <h4>🏥 {client}</h4>
                    <p><b>📍 Location:</b> {loc}</p>
                    <p><b>📏 Distance:</b> {dist:.1f} miles</p>
                    <p><b>🗣️ Language:</b> {row.get('language', 'N/A')}</p>
                    <p><b>💰 Pay Rate:</b> {row.get('pay_rate', 'N/A')}</p>
                    <p><b>📝 Notes:</b> {row.get('order_notes', 'N/A')}</p>
                    <p><b>Gender:</b> {row.get('gender', 'N/A')}</p>
                </div>
                """, unsafe_allow_html=True)

        # ------------------ Map ------------------
        st.subheader("🗺️ Job Locations")
        map_df = pd.DataFrame([{"lat": c[0], "lon": c[1]} for c in nearby["coords"]])
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position='[lon, lat]',
            get_color='[37, 99, 235, 180]',
            get_radius=700,
            pickable=True
        )
        view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=7)
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))

# ------------------ Download Button ------------------
if not nearby.empty:
    st.download_button(
        "📥 Download Nearby Jobs CSV",
        data=nearby.to_csv(index=False).encode('utf-8'),
        file_name="nearby_jobs.csv",
        mime="text/csv"
    )
