import streamlit as st
import pandas as pd
import math
import re

# -----------------------------
# Load CSV Files
# -----------------------------
@st.cache_data
def load_data():
    cities = pd.read_csv(
        "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
    )
    jobs = pd.read_csv(
        "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"
    )
    return cities, jobs

cities_df, jobs_df = load_data()

# -----------------------------
# Build ZIP → Coordinates map
# -----------------------------
ZIP_COORDS = {}

for _, row in cities_df.iterrows():
    lat, lng = row["lat"], row["lng"]
    zip_str = str(row["zips"])

    if pd.isna(zip_str):
        continue

    # Extract ZIPs
    zips = re.findall(r"\b\d{5}\b", zip_str)

    for z in zips:
        ZIP_COORDS[z] = {
            "coords": (lat, lng),
            "city": row["city_ascii"],
            "state": row["state_id"],
        }


# -----------------------------
# Haversine Distance
# -----------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


# -----------------------------
# Get coordinates from city name
# -----------------------------
def get_city_coords(city_name):
    rows = cities_df[cities_df["city_ascii"].str.lower() == city_name.lower()]
    if len(rows) == 0:
        return None
    row = rows.iloc[0]
    return row["lat"], row["lng"]


# -----------------------------
# Page UI
# -----------------------------
st.title("🔥 US Job Distance Finder (Fixed ZIP Resolver)")

user_input = st.text_input("Enter ZIP or City")

if user_input:
    ZIP_RE = r"\b\d{5}\b"
    zip_match = re.search(ZIP_RE, user_input)

    if zip_match:
        # User entered ZIP
        inp_zip = zip_match.group(0)

        if inp_zip in ZIP_COORDS:
            user_lat, user_lng = ZIP_COORDS[inp_zip]["coords"]
            st.success(f"Location matched → {ZIP_COORDS[inp_zip]['city']} ({user_lat}, {user_lng})")
        else:
            st.error("ZIP not found in city database.")
            st.stop()
    else:
        # User entered city
        coords = get_city_coords(user_input)
        if coords is None:
            st.error("City not found.")
            st.stop()
        user_lat, user_lng = coords
        st.success(f"Location matched → {user_input} ({user_lat}, {user_lng})")

    # -----------------------------
    # Compute Job Distances
    # -----------------------------
    results = []

    for _, row in jobs_df.iterrows():
        job_city = str(row["Client City"])
        job_zip = str(row["Zip Code"])

        # 1. Prefer ZIP if valid
        job_coords = None
        if re.fullmatch(r"\d{5}", job_zip) and job_zip in ZIP_COORDS:
            job_coords = ZIP_COORDS[job_zip]["coords"]
        else:
            # 2. Try city-based
            job_coords = get_city_coords(job_city)

        if job_coords is None:
            continue

        d = haversine(user_lat, user_lng, job_coords[0], job_coords[1])
        results.append((d, row))

    # Sort by distance
    results.sort(key=lambda x: x[0])

    # -----------------------------
    # Display Results
    # -----------------------------
    st.subheader("📍 Closest Jobs")

    for dist, row in results[:50]:
        st.markdown(f"""
        ### **{row['Client Name']}**
        **📍 City:** {row['Client City']}  
        **🧭 Distance:** {dist:.1f} km  
        **💬 Language:** {row['Language']}  
        **💰 Pay Rate:** {row['Pay Rate']}  
        **👤 Gender:** {row['Gender']}  
        **📝 Notes:** {row['Order Notes']}  
        ---
        """)

