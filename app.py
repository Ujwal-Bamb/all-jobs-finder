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

# ------------------ Remote CSV URLs (from your message) ------------------
CITIES_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOBS_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

# ------------------ Utilities ------------------
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")  # captures 12345 and 12345-6789

def safe_read_csv_from_url(url, on_bad_lines="skip"):
    """
    Fetch a URL, detect encoding, return a pandas DataFrame or empty DF on error.
    """
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

    # Normalize column names (just in case) to find lat/lng/city/zips
    cols = {c.lower().strip(): c for c in df.columns}
    # Try to find commonly named columns
    city_col = cols.get("city") or cols.get("city_ascii") or list(cols.values())[0]
    lat_col = cols.get("lat") or list(cols.values())[5] if len(cols) > 5 else None
    lng_col = cols.get("lng") or list(cols.values())[6] if len(cols) > 6 else None
    zips_col = cols.get("zips") if "zips" in cols else None

    cities = {}
    zip_coords = {}

    for _, r in df.iterrows():
        try:
            city_name = str(r.get(city_col, "")).strip().lower()
            lat = r.get(lat_col)
            lng = r.get(lng_col)
            if pd.notna(lat) and pd.notna(lng) and city_name:
                # store as float tuple
                cities[city_name] = (float(lat), float(lng))
            # parse zips column if available
            if zips_col and pd.notna(r.get(zips_col)):
                for z in str(r.get(zips_col)).split():
                    z_clean = z.strip()
                    if z_clean:
                        zip_coords[z_clean] = {
                            "coords": (float(lat), float(lng)) if pd.notna(lat) and pd.notna(lng) else None,
                            "city": city_name.title(),
                        }
        except Exception:
            # skip malformed rows but continue
            continue

    return cities, zip_coords

CA_CITIES, ZIP_COORDS = load_cities_mapping()  # name kept for backward compatibility (now contains all USA)

# ------------------ Load Jobs CSV ------------------
@st.cache_data(ttl=1800, show_spinner=False)
def load_jobs_df():
    df = safe_read_csv_from_url(JOBS_URL)
    if df.empty:
        return df

    # Normalize column names to lowercase underscored names
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_", regex=False)
    return df

jobs = load_jobs_df()

# ------------------ Column mapping for job fields ------------------
# We try to detect the columns you described. Fall back to sensible defaults.
def map_job_columns(df):
    mapped = {}
    if df.empty:
        return mapped

    cols = df.columns.tolist()

    # location: prefer client_city or city or location
    loc_candidates = [c for c in cols if "client_city" in c or c == "client_city" or "city" == c or "location" == c or "client_city" in c]
    mapped["location"] = loc_candidates[0] if loc_candidates else next((c for c in cols if "city" in c), None)

    # client name
    mapped["client"] = next((c for c in cols if "client" in c and "city" not in c), None)
    if not mapped["client"]:
        mapped["client"] = next((c for c in cols if "name" in c and "client" in c) , None)
    # zip
    mapped["zip"] = next((c for c in cols if "zip" in c), None)
    # state
    mapped["state"] = next((c for c in cols if c in ("state", "state_id", "state_name")), None)
    # language, pay_rate, order_notes, gender
    mapped["language"] = next((c for c in cols if "language" in c), None)
    mapped["pay_rate"] = next((c for c in cols if "pay" in c), None)
    mapped["order_notes"] = next((c for c in cols if "order" in c or "notes" in c), None)
    mapped["gender"] = next((c for c in cols if "gender" in c), None)

    return mapped

mapped_cols = map_job_columns(jobs)

# ------------------ Coordinate resolver ------------------
def get_coords(name):
    """
    Accepts strings like:
     - 'Los Angeles, CA, 90018'
     - '90018'
     - 'Los Angeles'
    Returns (lat,lng) tuple or None.
    """
    if not name:
        return None

    text = str(name).strip()

    # Look for a ZIP first (12345 or 12345-6789)
    m = ZIP_RE.search(text)
    if m:
        z = m.group(1)
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]
        # sometimes zips in cities csv include 5-digit only; try direct match
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]

    # Remove state abbreviations and trailing commas
    base = re.split(r"[,-]", text)[0].strip().lower()

    # try direct city lookup
    if base in CA_CITIES:
        return CA_CITIES[base]

    # fuzzy match
    match = get_close_matches(base, CA_CITIES.keys(), n=1, cutoff=0.72)
    if match:
        return CA_CITIES[match[0]]

    return None

# ------------------ Haversine ------------------
def haversine(c1, c2):
    if not c1 or not c2:
        return float("inf")
    R = 3958.8  # miles
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

# Ensure a location column exists
if not mapped_cols.get("location"):
    st.error("❌ Could not detect a location column in the jobs CSV. Found columns: " + ", ".join(jobs.columns))
    st.stop()

# Normalize a few columns we will use
loc_col = mapped_cols.get("location")
jobs[loc_col] = jobs[loc_col].astype(str).str.strip()

client_col = mapped_cols.get("client", None)
if client_col and client_col in jobs.columns:
    jobs["client"] = jobs[client_col].astype(str).str.strip()
else:
    # try some fallback column names
    fallback_client = next((c for c in jobs.columns if "client" in c and "city" not in c), None)
    jobs["client"] = jobs[fallback_client] if fallback_client else "Unknown Client"

# Lowercase column names for mapping resolution (but keep originals)
jobs["_location_norm"] = jobs[loc_col].astype(str).str.strip().str.lower()

# ------------------ Search controls ------------------
st.markdown("### 🔍 Search Jobs Near You")
col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    search_type = st.radio("Search by", ["City", "ZIP Code"], horizontal=True)

with col2:
    query = st.text_input("Enter City or ZIP", "")

with col3:
    radius = st.slider("Radius (miles)", 1, 500, 50)

# show detected city if ZIP mapped
if search_type == "ZIP Code" and query and ZIP_RE.search(query):
    z = ZIP_RE.search(query).group(1)
    if z in ZIP_COORDS and ZIP_COORDS[z].get("city"):
        st.info(f"📍 ZIP {z} corresponds to **{ZIP_COORDS[z]['city']}**.")
    else:
        st.warning("⚠️ ZIP code not found in our city->ZIP mapping.")

# trigger: button / enter
search_clicked = st.button("🔎 Find Jobs", use_container_width=True)
if query and st.session_state.get("query_entered") != query:
    st.session_state["query_entered"] = query
    search_clicked = True

nearby = None  # ensure variable exists in this scope

if search_clicked:
    if not str(query).strip():
        st.warning("Please enter a city or ZIP code.")
        st.stop()

    # resolve user coords
    if search_type == "ZIP Code":
        mz = ZIP_RE.search(query)
        if not mz:
            st.error("Please enter a valid 5-digit ZIP code (or 12345-6789).")
            st.stop()
        z = mz.group(1)
        zip_info = ZIP_COORDS.get(z)
        user_coords = zip_info["coords"] if zip_info else None
    else:
        user_coords = get_coords(query)

    if not user_coords:
        st.error("⚠️ Could not find that city or ZIP in our mapping.")
        st.stop()

    # Resolve coords for all jobs (cache-friendly)
    jobs["coords"] = jobs["_location_norm"].apply(get_coords)
    # drop rows without coords
    jobs_with_coords = jobs.dropna(subset=["coords"]).copy()
    if jobs_with_coords.empty:
        st.warning("No job rows have coordinates available to compare.")
        st.stop()

    # compute distances
    jobs_with_coords["distance"] = jobs_with_coords["coords"].apply(lambda c: haversine(user_coords, c))
    nearby = jobs_with_coords[jobs_with_coords["distance"] <= radius].sort_values("distance")

    if nearby.empty:
        st.warning(f"No jobs found within {radius} miles of {query}.")
    else:
        st.success(f"🎯 Found {len(nearby)} job(s) within {radius} miles of {query}!")
        # present results
        for _, row in nearby.iterrows():
            client = row.get("client", "Unknown Client")
            loc = row.get(loc_col, "Unknown Location")
            dist = row["distance"]
            lang = row.get(mapped_cols.get("language", ""), "N/A") if mapped_cols.get("language") else row.get("language", "N/A")
            pay = row.get(mapped_cols.get("pay_rate", ""), "N/A") if mapped_cols.get("pay_rate") else row.get("pay_rate", "N/A")
            notes = row.get(mapped_cols.get("order_notes", ""), "") if mapped_cols.get("order_notes") else row.get("order_notes", "")
            gender = row.get(mapped_cols.get("gender", ""), "") if mapped_cols.get("gender") else row.get("gender", "")

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

        # ------------------ Map ------------------
        map_coords = [c for c in nearby["coords"].tolist() if c is not None]
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
        else:
            st.warning("No coordinates available to display on the map for nearby jobs.")

    # ------------------ Download Button (only after search) ------------------
    if nearby is not None and not nearby.empty:
        csv_bytes = nearby.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download Nearby Jobs CSV",
            data=csv_bytes,
            file_name="nearby_jobs.csv",
            mime="text/csv",
        )

# --- End of app ---
