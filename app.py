import pandas as pd
from geopy.distance import geodesic

# ---------- Load City ZIP Database ----------
city_df = pd.read_csv("uszips.csv", dtype=str)
city_df['lat'] = city_df['lat'].astype(float)
city_df['lng'] = city_df['lng'].astype(float)

# ---------- Load Job File ----------
jobs_df = pd.read_csv("jobs.csv", dtype=str)

# Standardize missing values
jobs_df = jobs_df.fillna("nan")

# Fix column names (if needed)
jobs_df.columns = [c.strip().lower().replace(" ", "_") for c in jobs_df.columns]

# Ensure required columns exist
required_cols = [
    "client_name", "client_city", "state", "zip_code",
    "pay_rate", "gender", "language", "order_notes"
]

for col in required_cols:
    if col not in jobs_df.columns:
        raise ValueError(f"Missing column in jobs.csv → {col}")

# --------- Function to get lat/lng by ZIP or City ----------
def get_coordinates(user_input):
    user_input = user_input.strip()

    # If numeric → ZIP code
    if user_input.isdigit():
        row = city_df[city_df['zips'].str.contains(user_input, na=False)]
        if not row.empty:
            return float(row.iloc[0]['lat']), float(row.iloc[0]['lng'])

    # Otherwise → City name search
    row = city_df[city_df['city_ascii'].str.lower() == user_input.lower()]
    if not row.empty:
        return float(row.iloc[0]['lat']), float(row.iloc[0]['lng'])

    return None

# --------- Function to compute distance ----------
def compute_distance(lat1, lng1, lat2, lng2):
    return geodesic((lat1, lng1), (lat2, lng2)).km

# --------- Main Search Function ----------
def find_nearby_jobs(user_location):
    coords = get_coordinates(user_location)
    if coords is None:
        return "❌ Location not found."

    user_lat, user_lng = coords

    results = []

    for _, row in jobs_df.iterrows():
        # Find job city in ZIP database
        city_match = city_df[city_df["city_ascii"].str.lower() == row["client_city"].lower()]

        if city_match.empty:
            continue

        job_lat = float(city_match.iloc[0]["lat"])
        job_lng = float(city_match.iloc[0]["lng"])

        distance = compute_distance(user_lat, user_lng, job_lat, job_lng)

        results.append({
            "client": row["client_name"],
            "city": row["client_city"],
            "distance": distance,
            "language": row["language"],
            "pay_rate": row["pay_rate"],
            "gender": row["gender"],   # ← FIXED: SHOW EXACT CSV VALUE
            "notes": row["order_notes"]
        })

    # Sort by distance
    results = sorted(results, key=lambda x: x["distance"])

    # Format output
    output = []
    for r in results[:10]:
        block = f"""
{r['client']}
📍 City: {r['city']}
🧭 Distance: {r['distance']:.1f} km
💬 Language: {r['language']}
💰 Pay Rate: {r['pay_rate']}
👤 Gender: {r['gender']}
📝 Notes: {r['notes']}
"""
        output.append(block)

    return "\n".join(output)


# Example:
print(find_nearby_jobs("60602"))
