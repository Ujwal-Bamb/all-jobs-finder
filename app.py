import streamlit as st
import pandas as pd
import pydeck as pdk
import re
import requests
import chardet
from io import StringIO, BytesIO
from math import radians, sin, cos, sqrt, atan2
from difflib import get_close_matches

st.set_page_config(page_title="😊 Keep Smiling", layout="wide")

# ---------- CSS ----------
st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #e9f3ff, #f8fbff); font-family: 'Segoe UI', sans-serif; }
.job-card { background: white; border-radius: 12px; padding: 12px; margin: 8px 0; box-shadow: 0 3px 8px rgba(37,99,235,0.08); }
.job-card h4 { color: #1e3a8a; margin-bottom: 6px; }
.job-card p { margin: 4px 0; font-size: 14px; }
</style>
""", unsafe_allow_html=True)

# ---------- Helpers: robust csv loader ----------
def read_csv_from_bytes(content_bytes):
    # detect encoding
    detected = chardet.detect(content_bytes)
    enc = detected.get("encoding") or "utf-8"
    try:
        return pd.read_csv(StringIO(content_bytes.decode(enc)), on_bad_lines="skip")
    except Exception:
        # fallback latin1
        return pd.read_csv(StringIO(content_bytes.decode("latin1")), on_bad_lines="skip")

def read_csv_from_url(url):
    resp = requests.get(url)
    resp.raise_for_status()
    return read_csv_from_bytes(resp.content)

def normalize_cols(df):
    df = df.copy()
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
    return df

# ---------- Load city/zip mapping (all states) ----------
st.sidebar.header("Data sources (cities & jobs)")
st.sidebar.markdown("Upload a **cities/ZIP** CSV (contains columns like: city, state_id, state_name, lat, lng, zips).")
city_file = st.sidebar.file_uploader("Upload cities/ZIPs CSV (optional)", type=["csv"])
city_url = st.sidebar.text_input("Or paste raw URL for cities CSV (optional)", "")

CA_DATA = None
if city_file:
    try:
        content = city_file.read()
        CA_DATA = normalize_cols(read_csv_from_bytes(content))
        st.sidebar.success("Cities CSV loaded from upload.")
    except Exception as e:
        st.sidebar.error(f"Cities file error: {e}")
elif city_url.strip():
    try:
        CA_DATA = normalize_cols(read_csv_from_url(city_url.strip()))
        st.sidebar.success("Cities CSV loaded from URL.")
    except Exception as e:
        st.sidebar.error(f"Cities URL error: {e}")
else:
    st.sidebar.info("No cities CSV provided — please upload one for full US support.")

# ---------- Load jobs CSV ----------
st.sidebar.markdown("---")
st.sidebar.markdown("Upload your **jobs CSV** (columns: Client Name, Client City, State, Zip Code, Pay Rate, Language, Order Notes).")
jobs_file = st.sidebar.file_uploader("Upload jobs CSV", type=["csv"])
jobs_url = st.sidebar.text_input("Or paste raw URL for jobs CSV", "")

jobs_df = pd.DataFrame()
if jobs_file:
    try:
        content = jobs_file.read()
        jobs_df = normalize_cols(read_csv_from_bytes(content))
        st.sidebar.success("Jobs CSV loaded from upload.")
    except Exception as e:
        st.sidebar.error(f"Jobs file error: {e}")
elif jobs_url.strip():
    try:
        jobs_df = normalize_cols(read_csv_from_url(jobs_url.strip()))
        st.sidebar.success("Jobs CSV loaded from URL.")
    except Exception as e:
        st.sidebar.error(f"Jobs URL error: {e}")
else:
    st.sidebar.info("Upload job CSV to enable search.")

# ---------- Build mappings from cities CSV ----------
zip_to_info = {}   # '90210' => {'coords':(lat,lng), 'city': 'Los Angeles', 'state':'CA'}
citystate_to_coords = {}  # ('los angeles','ca') => (lat,lng)

if CA_DATA is not None:
    # expected columns: city, state_id/state_name, lat, lng, zips (space-separated)
    # normalize some common column names
    df = CA_DATA.copy()
    # ensure lat/lng exist
    if 'lat' in df.columns and 'lng' in df.columns:
        for _, r in df.iterrows():
            city_name = str(r.get('city', '')).strip()
            state_id = str(r.get('state_id', r.get('state', r.get('state_name', '')))).strip()
            lat = r.get('lat', None)
            lng = r.get('lng', None)
            zips_field = r.get('zips', '')
            if pd.notna(lat) and pd.notna(lng):
                key = (city_name.strip().lower(), str(state_id).strip().lower())
                citystate_to_coords[key] = (float(lat), float(lng))
                if pd.notna(zips_field):
                    for z in str(zips_field).split():
                        zc = z.strip()
                        if zc:
                            zip_to_info[zc] = {"coords": (float(lat), float(lng)), "city": city_name.title(), "state": state_id.upper()}
    else:
        st.sidebar.error("Cities CSV missing 'lat' or 'lng' columns.")

# ---------- Utility functions ----------
def get_coords_from_job_row(row):
    """
    Try to resolve coordinates for a job row in order:
    1) If job has explicit lat/lng columns
    2) If job has zip and zip present in zip_to_info
    3) If job has city & state that matches citystate_to_coords (or fuzzy match)
    Returns (lat, lng) or None
    """
    # 1) explicit latitude/longitude
    for lat_col in ['latitude', 'lat', 'job_lat', 'latitude_job']:
        for lon_col in ['longitude', 'lng', 'lon', 'job_lng', 'longitude_job']:
            if lat_col in row.index and lon_col in row.index:
                try:
                    la = float(row.get(lat_col))
                    lo = float(row.get(lon_col))
                    return (la, lo)
                except Exception:
                    pass

    # 2) zip code
    zip_cols = [c for c in row.index if 'zip' in c]
    for zc in zip_cols:
        zval = str(row.get(zc)).strip()
        if zval and re.search(r'\d{5}', zval):
            z = re.search(r'\d{5}', zval).group()
            if z in zip_to_info:
                return zip_to_info[z]['coords']

    # 3) city + state
    city_col = None
    state_col = None
    for c in row.index:
        if 'city' in c:
            city_col = c
        if 'state' in c:
            state_col = c
    if city_col:
        city = str(row.get(city_col)).strip().lower()
        state = str(row.get(state_col, '')).strip().lower() if state_col else ''
        key = (city, state)
        if key in citystate_to_coords:
            return citystate_to_coords[key]
        # fuzzy match on city name only (take first with same city)
        matches = [k for k in citystate_to_coords.keys() if k[0] == city]
        if matches:
            return citystate_to_coords[matches[0]]

    return None

def haversine_miles(c1, c2):
    if not c1 or not c2:
        return float('inf')
    R = 3958.8
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# ---------- Main UI ----------
st.title("😊 Keep Smiling")
st.write("Find nearby job listings across the US. Upload your cities/ZIPs CSV and jobs CSV (or paste raw URLs) in the sidebar.")

# show small previews
if CA_DATA is not None:
    st.sidebar.markdown(f"**Cities data:** {len(CA_DATA)} rows loaded.")
if not jobs_df.empty:
    st.sidebar.markdown(f"**Jobs data:** {len(jobs_df)} rows loaded.")

# Clean jobs columns
if not jobs_df.empty:
    jobs_df = normalize_cols(jobs_df)
    # rename common columns to standard names for convenience
    # We'll look for client name / client city / state / zip code
    # Keep original columns too but add normalized aliases
    # nothing else required here

# ---------- Search controls ----------
st.markdown("### 🔍 Search Jobs Near You")
col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    search_type = st.radio("Search by", ["City, State", "ZIP Code"], horizontal=True)
with col2:
    query = st.text_input("Enter City,State or ZIP (press Enter or click Find Jobs)", st.session_state.get("query", ""))
with col3:
    radius = st.slider("Radius (miles)", 1, 200, 40)

# show detected city when ZIP entered (if mapping available)
if search_type == "ZIP Code" and query and re.search(r'\d{5}', query):
    z = re.search(r'\d{5}', query).group()
    if z in zip_to_info:
        st.info(f"📍 ZIP {z} corresponds to **{zip_to_info[z]['city']}, {zip_to_info[z]['state']}** (from mapping).")
    else:
        st.info("ZIP not found in mapping; geocoding may be required.")

# ---------- Trigger search: button or Enter ----------
find_clicked = st.button("🔎 Find Jobs", use_container_width=True)
# detect Enter by storing last query
if query and st.session_state.get("last_query") != query:
    st.session_state["last_query"] = query
    find_clicked = True

if find_clicked:
    if jobs_df.empty:
        st.warning("Please upload a jobs CSV in the sidebar first.")
    else:
        q = query.strip()
        user_coords = None
        if search_type == "ZIP Code":
            m = re.search(r'\d{5}', q)
            if not m:
                st.error("Please enter a valid 5-digit ZIP code.")
                st.stop()
            z = m.group()
            if z in zip_to_info:
                user_coords = zip_to_info[z]['coords']
            else:
                st.warning("ZIP not found in mapping. Attempting to match city/state instead (may be less accurate).")
        else:
            # expect "City, State" e.g., "Dublin, OH" or "Dublin OH"
            parts = [p.strip() for p in re.split(r',|\|', q) if p.strip()]
            if len(parts) == 1:
                city = parts[0].lower()
                state = ''
            else:
                city = parts[0].lower()
                state = parts[1].lower()
            key = (city, state)
            if key in citystate_to_coords:
                user_coords = citystate_to_coords[key]
            else:
                # try fuzzy match on city
                possible = [k for k in citystate_to_coords.keys() if k[0] == city]
                if possible:
                    user_coords = citystate_to_coords[possible[0]]
                else:
                    st.error("City+State not found in mapping. Provide exact 'City, State' or upload a mapping that contains it.")
                    st.stop()

        if not user_coords:
            st.error("Could not resolve the search coordinates. Check your mapping or query.")
            st.stop()

        # Resolve coords for all job rows and compute distance
        jobs = jobs_df.copy()
        # Add normalized helper columns
        if 'zip_code' not in jobs.columns and 'zip' not in jobs.columns and 'zipcode' not in jobs.columns:
            # maybe column named 'zip_code' or 'zip code' - nothing to do, we already normalized
            pass

        resolved_coords = []
        for i, row in jobs.iterrows():
            coords = get_coords_from_job_row(row)
            resolved_coords.append(coords)
        jobs['resolved_coords'] = resolved_coords
        missing_count = jobs['resolved_coords'].isna().sum()
        if missing_count > 0:
            st.warning(f"{missing_count} job(s) could not be matched to coordinates and will be excluded from distance search.")

        # drop unmatched
        jobs_valid = jobs.dropna(subset=['resolved_coords']).copy()
        jobs_valid['distance_miles'] = jobs_valid['resolved_coords'].apply(lambda c: haversine_miles(user_coords, c))
        nearby = jobs_valid[jobs_valid['distance_miles'] <= radius].sort_values('distance_miles')

        st.markdown(f"### Results — {len(nearby)} job(s) within {radius} miles")
        if nearby.empty:
            st.warning("No nearby jobs found.")
        else:
            # show summary table first (select columns to show)
            preview_cols = [c for c in ['client_name','client_city','state','zip_code','pay_rate','gender','language','order_notes'] if c in nearby.columns]
            preview_cols = preview_cols or list(nearby.columns[:8])
            st.dataframe(nearby[preview_cols + ['distance_miles']].reset_index(drop=True).round(2))

            # download button
            csvbytes = nearby.to_csv(index=False).encode('utf-8')
            st.download_button("Download results CSV", data=csvbytes, file_name="nearby_jobs.csv", mime="text/csv")

            # display job cards
            for _, row in nearby.iterrows():
                client = row.get('client_name') or row.get('client') or row.get('clientname') or 'Unknown Client'
                loc_text = ""
                if 'client_city' in row.index and pd.notna(row.get('client_city')):
                    loc_text = f"{row.get('client_city')}, {row.get('state', '')}"
                elif 'zip_code' in row.index and pd.notna(row.get('zip_code')):
                    loc_text = f"ZIP {row.get('zip_code')}"
                else:
                    loc_text = "Location unknown"

                dist = row['distance_miles']
                with st.expander(f"🏥 {client} — {loc_text} ({dist:.2f} miles)"):
                    st.markdown(f"""
                    <div class='job-card'>
                        <h4>🏥 {client}</h4>
                        <p><b>📍 Location:</b> {loc_text}</p>
                        <p><b>📏 Distance:</b> {dist:.2f} miles</p>
                        <p><b>🗣️ Language:</b> {row.get('language', 'N/A')}</p>
                        <p><b>💰 Pay Rate:</b> {row.get('pay_rate', 'N/A')}</p>
                        <p><b>📝 Notes:</b> {row.get('order_notes', row.get('order_note', ''))}</p>
                    </div>
                    """, unsafe_allow_html=True)

            # map plotting
            st.subheader("🗺️ Job Locations")
            map_df = pd.DataFrame([
                {"lat": c[0], "lon": c[1]} for c in nearby['resolved_coords']
            ])
            # show user location as first center
            layer = pdk.Layer(
                "ScatterplotLayer",
                data=map_df,
                get_position='[lon, lat]',
                get_radius=700,
                pickable=True
            )
            view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=8)
            st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))

