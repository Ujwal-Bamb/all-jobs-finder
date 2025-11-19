import streamlit as st
import pandas as pd
import pydeck as pdk
import re
from math import radians, sin, cos, sqrt, atan2
from difflib import get_close_matches

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

# ------------------ Load Data ------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

@st.cache_data
def load_cities():
    df = pd.read_csv(CITIES_URL)
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    cities, zips = {}, {}
    for _, row in df.iterrows():
        city_name = str(row['city']).strip().lower()
        lat, lng = row['lat'], row['lng']
        cities[city_name] = (lat, lng)
        zips_field = row.get('zips', '')
        if pd.notna(zips_field):
            for z in str(zips_field).split():
                zips[z.strip()] = {"coords": (lat, lng), "city": city_name.title()}
    return cities, zips

@st.cache_data
def load_jobs():
    df = pd.read_csv(JOBS_URL)
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    if 'client_name' not in df.columns:
        client_col = next((c for c in df.columns if 'client' in c), None)
        df['client_name'] = df[client_col] if client_col else 'Unknown Client'
    df['client_city'] = df['client_city'].astype(str).str.lower()
    df['state'] = df['state'].astype(str).str.lower()
    df['zip_code'] = df['zip_code'].astype(str)
    return df

CA_CITIES, ZIP_COORDS = load_cities()
jobs_df = load_jobs()

st.sidebar.success(f"✅ Cities loaded: {len(CA_CITIES)}")
st.sidebar.success(f"✅ Jobs loaded: {len(jobs_df)}")

# ------------------ Utility Functions ------------------
def get_coords(name, state=''):
    if not name:
        return None
    name = str(name).strip().lower()
    zip_match = re.search(r"\b\d{5}\b", name)
    if zip_match:
        z = zip_match.group()
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]
    key = (name, state.lower())
    if key in CA_CITIES:
        return CA_CITIES[key]
    match = get_close_matches(name, CA_CITIES.keys(), n=1, cutoff=0.75)
    return CA_CITIES[match[0]] if match else None

def haversine(c1, c2):
    if not c1 or not c2:
        return float('inf')
    R = 3958.8  # miles
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# ------------------ Main Interface ------------------
st.title("😊 Keep Smiling")
st.write("Search caregiver job listings by city or ZIP code in the US.")

# ------------------ Search UI ------------------
col1, col2, col3 = st.columns([2,2,1])
with col1:
    search_type = st.radio("Search by", ["City, State", "ZIP Code"], horizontal=True)
with col2:
    query = st.text_input("Enter City,State or ZIP", st.session_state.get("query",""))
with col3:
    radius = st.slider("Radius (miles)", 1, 200, 40)

# Initialize empty nearby DataFrame
nearby = pd.DataFrame()

# Trigger search
search_clicked = st.button("🔎 Find Jobs", use_container_width=True)
if query and st.session_state.get("query_entered") != query:
    st.session_state["query_entered"] = query
    search_clicked = True

if search_clicked:
    if not query.strip():
        st.warning("Please enter a city or ZIP code.")
        st.stop()

    # Determine user coordinates
    user_coords = None
    if search_type == "ZIP Code":
        if query in ZIP_COORDS:
            user_coords = ZIP_COORDS[query]["coords"]
            st.info(f"📍 ZIP {query} corresponds to **{ZIP_COORDS[query]['city']}**")
        else:
            st.warning("⚠️ ZIP code not found.")
            st.stop()
    else:
        parts = [p.strip() for p in re.split(r',|\|', query)]
        city = parts[0].lower()
        state = parts[1].lower() if len(parts) > 1 else ''
        key = (city, state)
        user_coords = CA_CITIES.get(key)
        if not user_coords:
            # fallback: fuzzy match on city only
            matches = [k for k in CA_CITIES.keys() if k[0]==city]
            if matches:
                user_coords = CA_CITIES[matches[0]]
            else:
                st.error("⚠️ City not found in mapping.")
                st.stop()

    # Resolve job coordinates
    def resolve_job_coords(row):
        if row['zip_code'] in ZIP_COORDS:
            return ZIP_COORDS[row['zip_code']]['coords']
        return get_coords(row['client_city'], row['state'])

    jobs_df['coords'] = jobs_df.apply(resolve_job_coords, axis=1)
    jobs_valid = jobs_df.dropna(subset=['coords']).copy()
    jobs_valid['distance'] = jobs_valid['coords'].apply(lambda c: haversine(user_coords, c))
    nearby = jobs_valid[jobs_valid['distance'] <= radius].sort_values('distance')

# ------------------ Display Results ------------------
if not nearby.empty:
    st.success(f"🎯 Found {len(nearby)} job(s) within {radius} miles!")
    for _, row in nearby.iterrows():
        client = row.get('client_name', 'Unknown Client')
        loc = f"{row.get('client_city','')} , {row.get('state','')}" if pd.notna(row.get('client_city')) else f"ZIP {row.get('zip_code','')}"
        dist = row['distance']
        with st.expander(f"🏥 {client} — {loc} ({dist:.1f} miles)"):
            st.markdown(f"""
            <div class='job-card'>
                <h4>🏥 {client}</h4>
                <p><b>📍 Location:</b> {loc}</p>
                <p><b>📏 Distance:</b> {dist:.1f} miles</p>
                <p><b>🗣️ Language:</b> {row.get('language', 'N/A')}</p>
                <p><b>💰 Pay Rate:</b> {row.get('pay_rate', 'N/A')}</p>
                <p><b>📝 Notes:</b> {row.get('order_notes', 'N/A')}</p>
            </div>
            """, unsafe_allow_html=True)

    # Download button
    st.download_button(
        "📥 Download Nearby Jobs CSV",
        data=nearby.to_csv(index=False).encode('utf-8'),
        file_name="nearby_jobs.csv",
        mime="text/csv"
    )

    # Map
    st.subheader("🗺️ Job Locations")
    map_df = pd.DataFrame([{"lat": c[0], "lon": c[1]} for c in nearby["coords"]])
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position='[lon, lat]',
        get_color='[37, 99, 235, 180]',
        get_radius=600,
    )
    view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=7)
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
else:
    st.info("No nearby jobs found. Enter a city or ZIP and click 'Find Jobs'.")
