import streamlit as st
import pandas as pd
import pydeck as pdk
import requests
import chardet
from io import StringIO
from math import radians, sin, cos, sqrt, atan2
import re

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

# ------------------ URLs ------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

# ------------------ Load Cities ------------------
@st.cache_data
def load_cities():
    df = pd.read_csv(CITIES_URL)
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    cities, zips = {}, {}
    for _, row in df.iterrows():
        city_name = str(row['city']).strip().lower()
        state_id = str(row['state_id']).strip().lower()
        lat, lng = row['lat'], row['lng']
        # key = (city, state)
        cities[(city_name, state_id)] = (lat, lng)
        # store ZIPs
        zips_field = row.get('zips', '')
        if pd.notna(zips_field):
            for z in str(zips_field).split():
                zips[z.strip()] = {"coords": (lat, lng), "city": city_name.title(), "state": state_id.upper()}
    return cities, zips

CA_CITIES, ZIP_COORDS = load_cities()

# ------------------ Load Jobs ------------------
@st.cache_data
def load_jobs():
    try:
        resp = requests.get(JOBS_URL)
        resp.raise_for_status()
        raw_data = resp.content
        encoding = chardet.detect(raw_data)["encoding"] or "utf-8"
        df = pd.read_csv(StringIO(raw_data.decode(encoding)), on_bad_lines="skip")
        df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
        # normalize columns
        if 'client_city' not in df.columns and 'clientcity' in df.columns:
            df['client_city'] = df['clientcity']
        if 'zip_code' not in df.columns and 'zip' in df.columns:
            df['zip_code'] = df['zip']
        if 'client_name' not in df.columns:
            client_col = next((c for c in df.columns if 'client' in c), None)
            df['client_name'] = df[client_col] if client_col else "Unknown Client"
        df['client_city'] = df['client_city'].astype(str).str.lower()
        return df
    except Exception as e:
        st.error(f"Error loading job data: {e}")
        return pd.DataFrame()

jobs_df = load_jobs()

# ------------------ Helper Functions ------------------
def get_coords_from_job(row):
    # 1) Try ZIP first
    for zc_col in ['zip_code', 'zip']:
        if zc_col in row.index:
            zval = str(row.get(zc_col, '')).strip()
            if zval in ZIP_COORDS:
                return ZIP_COORDS[zval]['coords']
    # 2) City + State
    city = str(row.get('client_city', '')).strip().lower()
    state = str(row.get('state', '')).strip().lower()
    key = (city, state)
    if key in CA_CITIES:
        return CA_CITIES[key]
    # fallback: match only city
    matches = [k for k in CA_CITIES.keys() if k[0] == city]
    if matches:
        return CA_CITIES[matches[0]]
    return None

def haversine_miles(c1, c2):
    if not c1 or not c2:
        return float('inf')
    R = 3958.8  # miles
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2-lat1, lon2-lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def resolve_user_coords(query, search_type):
    query = query.strip()
    if search_type == "ZIP Code":
        if query in ZIP_COORDS:
            return ZIP_COORDS[query]['coords']
        else:
            return None
    else:
        parts = [p.strip() for p in re.split(r',|\|', query) if p.strip()]
        city = parts[0].lower()
        state = parts[1].lower() if len(parts) > 1 else ''
        key = (city, state)
        if key in CA_CITIES:
            return CA_CITIES[key]
        # fallback: match only city
        matches = [k for k in CA_CITIES.keys() if k[0] == city]
        if matches:
            return CA_CITIES[matches[0]]
        return None

# ------------------ Main UI ------------------
st.title("😊 Keep Smiling")
st.write("Search caregiver job listings by city or ZIP code.")

if jobs_df.empty:
    st.warning("Job data could not be loaded.")
    st.stop()

# Search controls
col1, col2, col3 = st.columns([2,2,1])
with col1:
    search_type = st.radio("Search by", ["City, State", "ZIP Code"], horizontal=True)
with col2:
    query = st.text_input("Enter City,State or ZIP")
with col3:
    radius = st.slider("Radius (miles)", 1, 200, 40)

# Show detected city for ZIP
if search_type=="ZIP Code" and query.isdigit() and len(query)==5:
    if query in ZIP_COORDS:
        st.info(f"📍 ZIP {query} corresponds to **{ZIP_COORDS[query]['city']}, {ZIP_COORDS[query]['state']}**")
    else:
        st.warning("⚠️ ZIP not found in mapping.")

# Trigger search
search_clicked = st.button("🔎 Find Jobs")
if query and st.session_state.get("query_last") != query:
    st.session_state["query_last"] = query
    search_clicked = True

if search_clicked:
    user_coords = resolve_user_coords(query, search_type)
    if not user_coords:
        st.error("⚠️ City/ZIP not found in mapping.")
        st.stop()

    jobs_df['resolved_coords'] = jobs_df.apply(get_coords_from_job, axis=1)
    jobs_valid = jobs_df.dropna(subset=['resolved_coords']).copy()
    jobs_valid['distance_miles'] = jobs_valid['resolved_coords'].apply(lambda c: haversine_miles(user_coords, c))
    nearby = jobs_valid[jobs_valid['distance_miles'] <= radius].sort_values('distance_miles')

    st.markdown(f"### Results — {len(nearby)} job(s) within {radius} miles")
    if nearby.empty:
        st.warning("No jobs found nearby.")
    else:
        preview_cols = ['client_name','client_city','state','zip_code','pay_rate','gender','language','order_notes']
        preview_cols = [c for c in preview_cols if c in nearby.columns]
        st.dataframe(nearby[preview_cols + ['distance_miles']].reset_index(drop=True).round(2))

        # download CSV
        st.download_button("Download CSV", nearby.to_csv(index=False).encode('utf-8'), "nearby_jobs.csv")

        # Job cards
        for _, row in nearby.iterrows():
            client = row.get('client_name', 'Unknown Client')
            loc = f"{row.get('client_city','').title()}, {row.get('state','').upper()}" if pd.notna(row.get('client_city')) else f"ZIP {row.get('zip_code','')}"
            dist = row['distance_miles']
            with st.expander(f"🏥 {client} — {loc} ({dist:.1f} miles)"):
                st.markdown(f"""
                <div class='job-card'>
                    <h4>🏥 {client}</h4>
                    <p><b>📍 Location:</b> {loc}</p>
                    <p><b>📏 Distance:</b> {dist:.1f} miles</p>
                    <p><b>🗣️ Language:</b> {row.get('language','N/A')}</p>
                    <p><b>💰 Pay Rate:</b> {row.get('pay_rate','N/A')}</p>
                    <p><b>📝 Notes:</b> {row.get('order_notes','')}</p>
                </div>
                """, unsafe_allow_html=True)

        # Map
        st.subheader("🗺️ Job Locations")
        map_df = pd.DataFrame([{"lat":c[0], "lon":c[1]} for c in nearby['resolved_coords']])
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position='[lon, lat]',
            get_color='[37, 99, 235, 180]',
            get_radius=600
        )
        view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=8)
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
