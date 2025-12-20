import streamlit as st
import pandas as pd
import requests
import re
from io import StringIO
from geopy.distance import geodesic

# -------------------------------------------------
# APP SETTINGS
# -------------------------------------------------
st.set_page_config(page_title="USA Job Finder", layout="wide")

st.title("🇺🇸 USA Job Finder")
st.write("Search caregiver jobs by ZIP code or City. Distance shown in **miles**.")

# -------------------------------------------------
# RAW CSV URLs
# -------------------------------------------------
CITY_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOB_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


# -------------------------------------------------
# SAFE CSV READER
# -------------------------------------------------
def load_csv(url):
    try:
        text = requests.get(url).text
        return pd.read_csv(StringIO(text), dtype=str)
    except:
        st.error(f"Failed to load: {url}")
        return pd.DataFrame()


# -------------------------------------------------
# LOAD CITY DATABASE
# -------------------------------------------------
city_df = load_csv(CITY_URL)
if city_df.empty:
    st.stop()

city_df.columns = city_df.columns.str.lower().str.replace(" ", "_")

# Required columns: city, lat, lng, zips
city_df["zips"] = city_df["zips"].fillna("")

# CREATE ZIP → COORDINATE LOOKUP
ZIP_COORDS = {}

for _, r in city_df.iterrows():
    lat = float(r["lat"])
    lng = float(r["lng"])
    city_name = r["city"].title()
    state = r["state_name"]

    for z in r["zips"].split():
        ZIP_COORDS[z] = {
            "coords": (lat, lng),
            "city": city_name,
            "state": state
        }


# -------------------------------------------------
# LOAD JOB DATABASE
# -------------------------------------------------
jobs = load_csv(JOB_URL)
if jobs.empty:
    st.stop()

jobs.columns = jobs.columns.str.lower().str.replace(" ", "_")


# -------------------------------------------------
# FUNCTION: Get job coordinates
# -------------------------------------------------
def job_coordinates(row):
    zip_code = str(row.get("zip_code", "")).strip()

    # ZIP MATCH
    if ZIP_RE.search(zip_code):
        z = ZIP_RE.search(zip_code).group(1)
        if z in ZIP_COORDS:
            return ZIP_COORDS[z]["coords"]

    # CITY MATCH
    city = str(row.get("client_city", "")).strip().lower()

    match = city_df[city_df["city"].str.lower() == city]
    if not match.empty:
        lat = float(match.iloc[0]["lat"])
        lng = float(match.iloc[0]["lng"])
        return (lat, lng)

    return None


# -------------------------------------------------
# USER INPUT
# -------------------------------------------------
st.subheader("🔍 Search Jobs")

# store search query into Streamlit session state
search_query = st.text_input(
    "Enter ZIP or City (e.g., 60602 or Boston)",
    key="query",
    on_change=lambda: st.session_state.__setitem__("run", True)
)

radius = st.slider("Radius (miles)", 5, 500, 50)

# run automatically when user presses Enter
search_clicked = st.session_state.get("run", False)



# -------------------------------------------------
# RUN SEARCH
# -------------------------------------------------
if search_clicked and search_query:

    # --- Resolve user coordinates ---
    user_coords = None

    # ZIP input
    m = ZIP_RE.search(search_query)
    if m:
        z = m.group(1)
        if z in ZIP_COORDS:
            user_coords = ZIP_COORDS[z]["coords"]
            st.info(f"Matched ZIP {z} → **{ZIP_COORDS[z]['city']}, {ZIP_COORDS[z]['state']}**")

    # CITY input
    if not user_coords:
        city = search_query.lower().strip()
        match = city_df[city_df["city"].str.lower() == city]
        if not match.empty:
            user_coords = (float(match.iloc[0]["lat"]), float(match.iloc[0]["lng"]))
            st.info(f"Matched city → **{match.iloc[0]['city']}, {match.iloc[0]['state_name']}**")

    if not user_coords:
        st.error("City or ZIP not found.")
        st.stop()

    # --- Compute job coordinates ---
    jobs["coords"] = jobs.apply(job_coordinates, axis=1)
    jobs_valid = jobs.dropna(subset=["coords"]).copy()

    if jobs_valid.empty:
        st.error("No jobs contain valid location mapping.")
        st.stop()

    # --- Compute distance in miles ---
    jobs_valid["distance"] = jobs_valid["coords"].apply(
        lambda c: geodesic(user_coords, c).miles
    )

    nearby = jobs_valid[jobs_valid["distance"] <= radius]
    nearby = nearby.sort_values("distance")

    if nearby.empty:
        st.warning("No jobs found within selected radius.")
        st.stop()

    st.success(f"Found {len(nearby)} job(s) within {radius} miles.")

    # -------------------------------------------------
    # SHOW RESULTS WITH EXPANDERS
    # -------------------------------------------------
    for _, r in nearby.iterrows():
        dist = r["distance"]

        with st.expander(f"🏥 {r['client_name']} — {r['client_city']} ({dist:.1f} miles)"):
            st.markdown(f"""
**📍 Location:** {r['client_city']}, {r['state']}  
**🧭 Distance:** {dist:.1f} miles  
**🗣 Language:** {r['language']}  
**💰 Pay Rate:** {r['pay_rate']}  
**👤 Gender:** {r['gender']}  
**📝 Notes:** {r['order_notes']}  
""")

