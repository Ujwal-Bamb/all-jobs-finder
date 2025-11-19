import streamlit as st
import pandas as pd
import requests
import chardet
import re
from io import StringIO
from math import radians, sin, cos, sqrt, atan2
import pydeck as pdk

# --------------------------
# Config / URLs
# --------------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

ZIP_RE = re.compile(r"\b(\d{5})\b")

# --------------------------
# Helpers to load CSV from URL with encoding handling
# --------------------------
def read_csv_from_url(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    raw = r.content
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    text = raw.decode(enc, errors="replace")
    return pd.read_csv(StringIO(text))

# --------------------------
# Load and prepare data (cached)
# --------------------------
@st.cache_data(ttl=3600)
def load_cities_and_zips():
    df = read_csv_from_url(CITIES_URL)
    # normalize column names
    df.columns = [c.strip() for c in df.columns]

    # Required columns: city, city_ascii, state_id, state_name, lat, lng, zips
    for req in ("city", "city_ascii", "state_id", "state_name", "lat", "lng", "zips"):
        if req not in df.columns:
            st.error(f"Cities CSV missing required column: {req}")
            return pd.DataFrame(), {}

    # Build quick lookup structures
    # city index by (city_ascii.lower(), state_id.lower()) and (city_ascii.lower(), state_name.lower())
    city_by_name_state = {}  # keys: (city_lower, state_id_lower) or (city_lower, state_name_lower) -> (lat,lng)
    zip_coords = {}  # "60602" -> {"coords":(lat,lng),"city":"Chicago","state":"IL"}

    for _, row in df.iterrows():
        city = str(row["city_ascii"]).strip()
        city_lower = city.lower()
        state_id = str(row["state_id"]).strip()
        state_id_lower = state_id.lower()
        state_name = str(row["state_name"]).strip()
        state_name_lower = state_name.lower()
        try:
            lat = float(row["lat"])
            lng = float(row["lng"])
        except Exception:
            continue

        # index by both state_id and state_name
        city_by_name_state[(city_lower, state_id_lower)] = (lat, lng)
        city_by_name_state[(city_lower, state_name_lower)] = (lat, lng)

        # parse zips robustly (collapse whitespace and extract 5-digit sequences)
        raw_zips = str(row["zips"])
        clean = re.sub(r"\s+", " ", raw_zips)
        zips = re.findall(r"\d{5}", clean)
        for z in zips:
            zip_coords[z] = {"coords": (lat, lng), "city": city, "state": state_id}

    return df, {"city_by_name_state": city_by_name_state, "zip_coords": zip_coords}

@st.cache_data(ttl=1800)
def load_jobs():
    df = read_csv_from_url(JOBS_URL)
    # Normalize column names to lowercase underscore style for convenience
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    # Required job columns based on your earlier confirmation:
    required = ["client_name", "client_city", "state", "zip_code", "pay_rate", "gender", "language", "order_notes"]
    for req in required:
        if req not in df.columns:
            st.error(f"Job CSV missing required column: {req}")
            return pd.DataFrame()
    # fill NaNs for display
    df = df.fillna("")
    return df

cities_df, cities_index = load_cities_and_zips()
jobs_df = load_jobs()

ZIP_COORDS = cities_index.get("zip_coords", {})
CITY_BY_NAME_STATE = cities_index.get("city_by_name_state", {})

# --------------------------
# Distance function (miles)
# --------------------------
def haversine_miles(c1, c2):
    if not c1 or not c2:
        return float("inf")
    R = 3958.8  # miles
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# --------------------------
# City+state resolver
# --------------------------
def find_city_coords(city, state=None):
    """
    Attempt to find lat/lng for a city.
    If state provided, prefer matches for that state.
    state may be 'OH' or 'Ohio' (case-insensitive).
    """
    if not city:
        return None

    city_key = city.strip().lower()
    if state:
        state_key = state.strip().lower()
        # try matching by (city,state_abbr) or (city,state_name)
        v = CITY_BY_NAME_STATE.get((city_key, state_key))
        if v:
            return v
    # if state missing or not found, try a best-effort fallback:
    # try any state by searching keys with city_key
    for (c, s), coords in CITY_BY_NAME_STATE.items():
        if c == city_key:
            return coords
    return None

# --------------------------
# Resolve coordinates for a job row (prefer zip, then city+state)
# --------------------------
def get_job_coords(job_row):
    # prefer job zip if valid
    job_zip = str(job_row.get("zip_code", "")).strip()
    m = ZIP_RE.search(job_zip)
    if m:
        z = m.group(1)
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]
    # else try city + state from job row
    job_city = str(job_row.get("client_city", "")).strip()
    job_state = str(job_row.get("state", "")).strip()
    coords = find_city_coords(job_city, job_state)
    return coords

# --------------------------
# Streamlit UI
# --------------------------
st.set_page_config(page_title="Job Finder (State-aware)", layout="wide")
st.title("Job Finder — state-aware city lookup (no CA mistakes)")

if jobs_df.empty or cities_df.empty:
    st.error("Could not load data. Check CSV URLs or network.")
    st.stop()

col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    search_type = st.radio("Search by", ["ZIP", "City"], horizontal=True)
with col2:
    query = st.text_input("Enter ZIP or City (for city you may include state: e.g. 'Hilliard, OH' or 'Hilliard, Ohio')", "")
with col3:
    radius = st.slider("Radius (miles)", 1, 500, 100)

if st.button("Find jobs"):
    if not query.strip():
        st.warning("Enter a ZIP or City to search.")
        st.stop()

    # parse user input: see if it's a ZIP or city[, state]
    z_m = ZIP_RE.search(query)
    user_coords = None
    user_state = None
    if z_m and search_type == "ZIP":
        z = z_m.group(1)
        user_info = ZIP_COORDS.get(z)
        if not user_info:
            st.error(f"ZIP {z} not found in city database.")
            st.stop()
        user_coords = user_info["coords"]
        user_state = user_info.get("state", None)
        st.success(f"Matched ZIP {z} → {user_info.get('city')}, {user_state}  at {user_coords}")
    else:
        # city input — allow "City, ST" or "City, StateName"
        parts = [p.strip() for p in query.split(",")]
        city_part = parts[0]
        state_part = parts[1] if len(parts) > 1 else None
        user_coords = find_city_coords(city_part, state_part)
        if not user_coords:
            st.error("City (and optional state) not found in city DB.")
            st.stop()
        user_state = state_part
        st.success(f"Matched {city_part}{', '+state_part if state_part else ''} → {user_coords}")

    # compute coords for all jobs (cache-friendly)
    # (we compute once and store in dataframe copy)
    jobs = jobs_df.copy()
    jobs["job_coords"] = jobs.apply(get_job_coords, axis=1)
    jobs = jobs.dropna(subset=["job_coords"]).copy()

    if jobs.empty:
        st.warning("No jobs with resolvable coordinates found.")
        st.stop()

    jobs["distance_miles"] = jobs["job_coords"].apply(lambda c: haversine_miles(user_coords, c))

    # optional: filter by same state if user wants (keeps Ohio-only results)
    same_state_only = st.checkbox("Only show jobs in the same state as search (recommended for cities with names in multiple states)", value=True)
    if same_state_only and user_state:
        # Normalize both forms: compare against job 'state' column (could be 'IL' or 'Illinois')
        def job_in_same_state(job_state_value):
            if not job_state_value:
                return False
            a = str(job_state_value).strip().lower()
            b = str(user_state).strip().lower()
            # match either abbreviation or full name via cities_df lookup
            return a == b or a == b.lower() or a == b.upper()

        jobs = jobs[jobs["state"].astype(str).str.strip().str.lower() == str(user_state).strip().lower()]

    # filter by radius
    nearby = jobs[jobs["distance_miles"] <= radius].sort_values("distance_miles")

    if nearby.empty:
        st.warning("No jobs found within the given radius (and filters).")
        st.stop()

    st.success(f"Found {len(nearby)} job(s) within {radius} miles.")

    # Display job cards (clean)
    for _, r in nearby.iterrows():
        client = r.get("client_name", "")
        city = r.get("client_city", "")
        state = r.get("state", "")
        dist = r["distance_miles"]
        language = r.get("language", "")
        pay = r.get("pay_rate", "")
        gender = r.get("gender", "")
        notes = r.get("order_notes", "")

        st.markdown(
            f"""
            <div style="background:#fff;border-radius:10px;padding:14px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
              <div style="font-size:18px;font-weight:600;color:#1e3a8a;margin-bottom:6px;">🏥 {client}</div>
              <div style="font-size:14px;margin-bottom:4px;"><b>📍 City:</b> {city}, {state}</div>
              <div style="font-size:14px;margin-bottom:4px;"><b>🧭 Distance:</b> {dist:.1f} miles</div>
              <div style="font-size:14px;margin-bottom:4px;"><b>💬 Language:</b> {language}</div>
              <div style="font-size:14px;margin-bottom:4px;"><b>💰 Pay Rate:</b> {pay}</div>
              <div style="font-size:14px;margin-bottom:4px;"><b>👤 Gender:</b> {gender}</div>
              <div style="font-size:14px;"><b>📝 Notes:</b> {notes}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Map
    map_coords = [c for c in nearby["job_coords"].tolist() if c]
    if map_coords:
        map_df = pd.DataFrame([{"lat": float(c[0]), "lon": float(c[1])} for c in map_coords])
        layer = pdk.Layer("ScatterplotLayer", data=map_df, get_position='[lon, lat]', get_radius=600, pickable=True)
        view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=6)
        st.subheader("Job locations")
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
        
