import streamlit as st
import pandas as pd
from geopy.distance import geodesic

st.title("🔥 Job Finder (Correct ZIP, City, State Matching)")

# -----------------------------
# Load Your Datasets
# -----------------------------
CITY_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOB_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"

city_df = pd.read_csv(CITY_URL, dtype=str)
job_df = pd.read_csv(JOB_URL, dtype=str)

# Convert latitude/longitude to float
city_df["lat"] = city_df["lat"].astype(float)
city_df["lng"] = city_df["lng"].astype(float)

# Fix ZIP column to list of ZIP codes
city_df["zip_list"] = city_df["zips"].fillna("").apply(lambda z: [x.strip() for x in z.split()])


# -----------------------------
# Helpers
# -----------------------------
def find_location(user_input):
    """Returns (lat, lng, city, state) for ZIP or city"""
    
    user_input = user_input.strip()

    # --- ZIP SEARCH ---
    if user_input.isdigit():
        row = city_df[city_df["zip_list"].apply(lambda z: user_input in z)]
        if not row.empty:
            r = row.iloc[0]
            return r["lat"], r["lng"], r["city"], r["state_name"]
    
    # --- CITY SEARCH ---
    city_only = user_input.lower()
    state_from_user = None

    # If user enters "Hilliard, OH"
    if "," in user_input:
        parts = [x.strip() for x in user_input.split(",")]
        city_only = parts[0].lower()
        state_from_user = parts[1].lower()

    rows = city_df[city_df["city_ascii"].str.lower() == city_only]

    # Filter by state if included
    if state_from_user:
        rows = rows[rows["state_id"].str.lower() == state_from_user]

    if not rows.empty:
        r = rows.iloc[0]
        return r["lat"], r["lng"], r["city"], r["state_name"]

    return None


def get_city_coords(city_name, state_name):
    """Gets job city coordinates from city_df"""
    rows = city_df[
        (city_df["city_ascii"].str.lower() == str(city_name).lower()) &
        (city_df["state_name"].str.lower() == str(state_name).lower())
    ]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return (r["lat"], r["lng"])


# -----------------------------
# Streamlit UI
# -----------------------------
st.subheader("Enter ZIP or City (Example: 60602 or Hilliard, OH)")

user_input = st.text_input("ZIP or City")
radius = st.slider("Radius (miles)", 5, 500, 50)

if user_input:
    result = find_location(user_input)

    if not result:
        st.error("❌ No matching ZIP/City found. Try a valid US ZIP or city name.")
    else:
        user_lat, user_lng, user_city, user_state = result
        
        st.success(f"📌 Location matched at: ({user_lat}, {user_lng}) — {user_city}, {user_state}")

        # -----------------------------
        # MATCH JOBS
        # -----------------------------
        job_results = []

        for _, job in job_df.iterrows():
            job_city = job.get("Client City", "")
            job_state = job.get("State", "")

            coords = get_city_coords(job_city, job_state)
            if not coords:
                continue

            job_lat, job_lng = coords
            dist = geodesic((user_lat, user_lng), (job_lat, job_lng)).miles

            if dist <= radius:
                job_results.append({
                    "client": job.get("Client Name", "Unknown"),
                    "city": job_city,
                    "state": job_state,
                    "distance": round(dist, 1),
                    "pay": job.get("Pay Rate", "N/A"),
                    "gender": job.get("Gender", "N/A"),
                    "language": job.get("Language", "N/A"),
                    "notes": job.get("Order Notes", "N/A")
                })

        # -----------------------------
        # OUTPUT
        # -----------------------------
        if not job_results:
            st.warning("No jobs found within selected radius.")
        else:
            st.subheader("📍 Closest Jobs Found:")

            for j in sorted(job_results, key=lambda x: x["distance"]):
                st.markdown(f"""
                **{j['client']}**
                - 📍 **{j['city']}, {j['state']}**
                - 🧭 Distance: **{j['distance']} miles**
                - 💬 Language: **{j['language']}**
                - 💰 Pay Rate: **{j['pay']}**
                - 👤 Gender: **{j['gender']}**
                - 📝 Notes: {j['notes']}
                """)

