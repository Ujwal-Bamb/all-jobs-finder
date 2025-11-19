import streamlit as st
import pandas as pd
import pydeck as pdk
import re
from math import radians, sin, cos, sqrt, atan2

st.set_page_config(page_title="😊 Keep Smiling", layout="wide")

st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #e9f3ff, #f8fbff); font-family: 'Segoe UI', sans-serif; }
.job-card { background: white; border-radius: 12px; padding: 12px; margin: 8px 0; box-shadow: 0 3px 8px rgba(37,99,235,0.08); }
.job-card h4 { color: #1e3a8a; margin-bottom: 6px; }
.job-card p { margin: 4px 0; font-size: 14px; }
</style>
""", unsafe_allow_html=True)

CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

@st.cache_data
def load_csv(url):
    df = pd.read_csv(url)
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    return df

CA_DATA = load_csv(CITIES_URL)
jobs_df = load_csv(JOBS_URL)

st.sidebar.success(f"Cities loaded: {len(CA_DATA)} rows")
st.sidebar.success(f"Jobs loaded: {len(jobs_df)} rows")

zip_to_info = {}
citystate_to_coords = {}
for _, r in CA_DATA.iterrows():
    city_name = str(r['city']).strip()
    state_id = str(r.get('state_id', r.get('state', r.get('state_name', '')))).strip()
    lat = r.get('lat')
    lng = r.get('lng')
    zips_field = r.get('zips', '')
    if pd.notna(lat) and pd.notna(lng):
        key = (city_name.lower(), state_id.lower())
        citystate_to_coords[key] = (float(lat), float(lng))
        if pd.notna(zips_field):
            for z in str(zips_field).split():
                zip_to_info[z.strip()] = {"coords": (float(lat), float(lng)), "city": city_name.title(), "state": state_id.upper()}

def get_coords_from_job_row(row):
    for zc_col in [c for c in row.index if 'zip' in c]:
        z = str(row.get(zc_col, '')).strip()
        if z in zip_to_info:
            return zip_to_info[z]['coords']
    city = str(row.get('city', '')).strip().lower()
    state = str(row.get('state', '')).strip().lower()
    key = (city, state)
    if key in citystate_to_coords:
        return citystate_to_coords[key]
    matches = [k for k in citystate_to_coords.keys() if k[0]==city]
    if matches:
        return citystate_to_coords[matches[0]]
    return None

def haversine_miles(c1, c2):
    if not c1 or not c2:
        return float('inf')
    R = 3958.8
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2-lat1, lon2-lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

st.title("😊 Keep Smiling")
st.write("Find nearby job listings across the US.")

col1, col2, col3 = st.columns([2,2,1])
with col1:
    search_type = st.radio("Search by", ["City, State", "ZIP Code"], horizontal=True)
with col2:
    query = st.text_input("Enter City,State or ZIP", st.session_state.get("query",""))
with col3:
    radius = st.slider("Radius (miles)", 1, 200, 40)

find_clicked = st.button("🔎 Find Jobs", use_container_width=True)
if query and st.session_state.get("last_query") != query:
    st.session_state["last_query"] = query
    find_clicked = True

if find_clicked:
    user_coords = None
    q = query.strip()
    if search_type=="ZIP Code":
        if q in zip_to_info:
            user_coords = zip_to_info[q]['coords']
    else:
        parts = [p.strip() for p in re.split(r',|\|', q)]
        city = parts[0].lower()
        state = parts[1].lower() if len(parts)>1 else ''
        key = (city,state)
        if key in citystate_to_coords:
            user_coords = citystate_to_coords[key]
        else:
            possible = [k for k in citystate_to_coords.keys() if k[0]==city]
            if possible:
                user_coords = citystate_to_coords[possible[0]]

    if not user_coords:
        st.error("Could not resolve search coordinates.")
        st.stop()

    jobs_df['resolved_coords'] = jobs_df.apply(get_coords_from_job_row, axis=1)
    jobs_valid = jobs_df.dropna(subset=['resolved_coords']).copy()
    jobs_valid['distance_miles'] = jobs_valid['resolved_coords'].apply(lambda c: haversine_miles(user_coords,c))
    nearby = jobs_valid[jobs_valid['distance_miles']<=radius].sort_values('distance_miles')

    st.markdown(f"### Results — {len(nearby)} job(s) within {radius} miles")
    if nearby.empty:
        st.warning("No nearby jobs found.")
    else:
        preview_cols = [c for c in ['client_name','city','state','zip_code','pay_rate','gender','language','order_notes'] if c in nearby.columns]
        st.dataframe(nearby[preview_cols+['distance_miles']].reset_index(drop=True).round(2))
        st.download_button("Download results CSV", data=nearby.to_csv(index=False).encode('utf-8'), file_name="nearby_jobs.csv")
        for _, row in nearby.iterrows():
            client = row.get('client_name','Unknown Client')
            loc_text = f"{row.get('city','')} , {row.get('state','')}" if pd.notna(row.get('city')) else f"ZIP {row.get('zip_code','')}"
            dist = row['distance_miles']
            with st.expander(f"🏥 {client} — {loc_text} ({dist:.2f} miles)"):
                st.markdown(f"""
                <div class='job-card'>
                    <h4>🏥 {client}</h4>
                    <p><b>📍 Location:</b> {loc_text}</p>
                    <p><b>📏 Distance:</b> {dist:.2f} miles</p>
                    <p><b>🗣️ Language:</b> {row.get('language','N/A')}</p>
                    <p><b>💰 Pay Rate:</b> {row.get('pay_rate','N/A')}</p>
                    <p><b>📝 Notes:</b> {row.get('order_notes','')}</p>
                </div>
                """, unsafe_allow_html=True)

        st.subheader("🗺️ Job Locations")
        map_df = pd.DataFrame([{"lat":c[0],"lon":c[1]} for c in nearby['resolved_coords']])
        layer = pdk.Layer("ScatterplotLayer", data=map_df, get_position='[lon, lat]', get_radius=700, pickable=True)
        view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=8)
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
