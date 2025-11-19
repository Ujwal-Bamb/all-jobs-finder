import streamlit as st
import pandas as pd
import pydeck as pdk
import re
import requests
import chardet
from io import StringIO
from math import radians, sin, cos, sqrt, atan2
from difflib import get_close_matches

# ------------------ Streamlit Setup ------------------
st.set_page_config(page_title="😊 Keep Smiling (USA)", layout="wide")

# ------------------ Custom CSS ------------------
st.markdown(
    """
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
    box-shadow: 0 4px 10px rgba(37,99,235,0.08);
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
""",
    unsafe_allow_html=True,
)

# ------------------ Remote CSV URLs ------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

# ------------------ Utilities ------------------
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

def safe_read_csv_from_url(url, on_bad_lines="skip"):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        raw = r.content
        encoding = chardet.detect(raw).get("encoding") or "utf-8"
        text = raw.decode(encoding, errors="replace")
        return pd.read_csv(StringIO(text), on_bad_lines=on_bad_lines)
    except Exception as e:
        st.error(f"Error fetching {url}: {e}")
        return pd.DataFrame()

# ------------------ Load Cities CSV (USA) ------------------
@st.cache_data(ttl=3600, show_spinner=False)
def load_cities_mapping():
    df = safe_read_csv_from_url(CITIES_URL)
    if df.empty:
        return {}, {}

    cols = {c.lower().strip(): c for c in df.columns}

    city_col = cols.get("city") or cols.get("city_ascii") or list(cols.values())[0]
    lat_col = cols.get("lat")
    lng_col = cols.get("lng")
    zips_col = cols.get("zips")

    cities = {}
    zip_coords = {}

    for _, r in df.iterrows():
        try:
            city_name = str(r.get(city_col, "")).strip().lower()
            lat = r.get(lat_col)
            lng = r.get(lng_col)

            if pd.notna(lat) and pd.notna(lng) and city_name:
                cities[city_name] = (float(lat), float(lng))

            # ZIP extraction (clean and safe)
            if zips_col and pd.notna(r.get(zips_col)):
                zips = re.findall(r"\b\d{5}\b", str(r.get(zips_col)))
                for z in zips:
                    zip_coords[z] = {
                        "coords": (float(lat), float(lng)),
                        "city": city_name.title()
                    }

        except Exception:
            continue

    return cities, zip_coords

CA_CITIES, ZIP_COORDS = load_cities_mapping()

# ------------------ Load Jobs CSV ------------------
@st.cache_data(ttl=1800, show_spinner=False)
def load_jobs_df():
    df = safe_read_csv_from_url(JOBS_URL)
    if df.empty:
        return df
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_", regex=False)
    return df

jobs = load_jobs_df()

# ------------------ Column mapping ------------------
def map_job_columns(df):
    mapped = {}
    if df.empty:
        return mapped

    cols = df.columns.tolist()

    mapped["location"] = next(
        (c for c in cols if c in ("client_city", "city", "location")), None
    )

    mapped["client"] = next((c for c in cols if "client" in c and "city" not in c), None)
    mapped["zip"] = next((c for c in cols if "zip" in c), None)
    mapped["state"] = next((c for c in cols if c in ("state", "state_id", "state_name")), None)
    mapped["language"] = next((c for c in cols if "language" in c), None)
    mapped["pay_rate"] = next((c for c in cols if "pay" in c), None)
    mapped["order_notes"] = next((c for c in cols if "order" in c or "notes" in c), None)
    mapped["gender"] = next((c for c in cols if "gender" in c), None)

    return mapped

mapped_cols = map_job_columns(jobs)

# ------------------ Coordinate resolver ------------------
def get_coords(name):
    if not name:
        return None

    text = str(name).strip()

    # ZIP lookup
    m = ZIP_RE.search(text)
    if m:
        z = m.group(1)
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]
        # *** CRITICAL FIX ***
        # If the ZIP is not found, DO NOT fall back to fuzzy matching
        return None

    # city name lookup
    base = re.split(r"[,-]", text)[0].strip().lower()

    # exact city
    if base in CA_CITIES:
        return CA_CITIES[base]

    # fuzzy city match
    match = get_close_matches(base, CA_CITIES.keys(), n=1, cutoff=0.72)
    if match:
        return CA_CITIES[match[0]]

    return None

# ------------------ Haversine ------------------
def haversine(c1, c2):
    if not c1 or not c2:
        return float("inf")
    R = 3958.8
    lat1, lon1 = map(radians, c1)
    lat2, lon2 = map(radians, c2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

# ------------------ UI ------------------
st.title("😊 Keep Smiling — USA Job Finder")
st.write("Search caregiver job listings by city or ZIP across the USA.")

if jobs.empty:
    st.error("Job data could not be loaded.")
    st.stop()

if not mapped_cols.get("location"):
    st.error("❌ Could not find a location column in the jobs CSV.")
    st.stop()

loc_col = mapped_cols["location"]
jobs[loc_col] = jobs[loc_col].astype(str).str.strip()

client_col = mapped_cols.get("client")
jobs["client"] = jobs[client_col] if client_col else "Unknown Client"

jobs["_location_norm"] = jobs[loc_col].astype(str).str.strip().str.lower()

# ------------------ Search ------------------
st.markdown("### 🔍 Search Jobs Near You")
col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    search_type = st.radio("Search by", ["City", "ZIP Code"], horizontal=True)

with col2:
    query = st.text_input("Enter City or ZIP", "")

with col3:
    radius = st.slider("Radius (miles)", 1, 500, 50)

if search_type == "ZIP Code" and query and ZIP_RE.search(query):
    z = ZIP_RE.search(query).group(1)
    if z in ZIP_COORDS:
        st.info(f"📍 ZIP {z} = {ZIP_COORDS[z]['city']}")
    else:
        st.warning("⚠️ ZIP not found in our mapping.")

search_clicked = st.button("🔎 Find Jobs", use_container_width=True)

if query and st.session_state.get("query_entered") != query:
    st.session_state["query_entered"] = query
    search_clicked = True

nearby = None

if search_clicked:
    if not query.strip():
        st.warning("Please enter a city or ZIP code.")
        st.stop()

    # resolve coordinates
    if search_type == "ZIP Code":
        mz = ZIP_RE.search(query)
        if not mz:
            st.error("Invalid ZIP format.")
            st.stop()
        z = mz.group(1)
        user_coords = ZIP_COORDS[z]["coords"] if z in ZIP_COORDS else None
    else:
        user_coords = get_coords(query)

    if not user_coords:
        st.error("⚠️ Could not find that city or ZIP.")
        st.stop()

    jobs["coords"] = jobs["_location_norm"].apply(get_coords)
    jobs_with_coords = jobs.dropna(subset=["coords"]).copy()

    jobs_with_coords["distance"] = jobs_with_coords["coords"].apply(
        lambda c: haversine(user_coords, c)
    )

    nearby = jobs_with_coords[jobs_with_coords["distance"] <= radius].sort_values("distance")

    if nearby.empty:
        st.warning(f"No jobs found within {radius} miles.")
    else:
        st.success(f"🎯 {len(nearby)} job(s) found!")

        for _, row in nearby.iterrows():
            client = row.get("client", "Unknown")
            loc = row.get(loc_col, "Unknown")
            dist = row["distance"]
            lang = row.get(mapped_cols.get("language"), "N/A")
            pay = row.get(mapped_cols.get("pay_rate"), "N/A")
            notes = row.get(mapped_cols.get("order_notes"), "")
            gender = row.get(mapped_cols.get("gender"), "")

            with st.expander(f"🏥 {client} — {loc} ({dist:.1f} miles)"):
                st.markdown(
                    f"""
                    <div class='job-card'>
                        <h4>🏥 {client}</h4>
                        <p><b>📍 Location:</b> {loc}</p>
                        <p><b>📏 Distance:</b> {dist:.1f} miles</p>
                        <p><b>🗣️ Language:</b> {lang}</p>
                        <p><b>💰 Pay Rate:</b> {pay}</p>
                        <p><b>📝 Notes:</b> {notes}</p>
                        <p><b>👤 Gender:</b> {gender}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        map_coords = [c for c in nearby["coords"].tolist() if c]
        if map_coords:
            map_df = pd.DataFrame([{"lat": float(c[0]), "lon": float(c[1])} for c in map_coords])
            layer = pdk.Layer(
                "ScatterplotLayer",
                data=map_df,
                get_position='[lon, lat]',
                get_radius=600,
                pickable=True,
            )
            view_state = pdk.ViewState(latitude=user_coords[0], longitude=user_coords[1], zoom=7)
            st.subheader("🗺️ Job Locations")
            st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))

        # Download button
        csv_bytes = nearby.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download Nearby Jobs CSV",
            data=csv_bytes,
            file_name="nearby_jobs.csv",
            mime="text/csv",
                )
    
