import pandas as pd
from geopy.distance import geodesic

# ---------- Load City ZIP Database ----------
city_df = pd.read_csv("uszips.csv", dtype=str)
city_df['lat'] = city_df['lat'].astype(float)
city_df['lng'] = city_df['lng'].astype(float)

# ---------- Load Job File ----------
jobs_df = pd.read_csv("jobs.csv", dtype=str)
jobs_df = jobs_df.fillna("nan")
jobs_df.columns = [c.strip().lower().replace(" ", "_") for c in jobs_df.columns]

# --------- Function to get coordinates by ZIP or City ----------
def get_coordinates(user_input):
    user_input = user_input.strip()

    # ZIP search
    if user_input.isdigit():
        row = city_df[city_df['zips'].str.contains(user_input, na=False)]
        if not row.empty:
            return float(row.iloc[0]['lat']), float(row.iloc[0]['lng'])

    # City search (no state)
    row = city_df[city_df['city_ascii'].str.lower() == user_input.lower()]
    if not row.empty:
        return float(row.iloc[0]['lat']), float(row.iloc[0]['lng'])

    return None

# --------- Main Function ----------
def find_nearby_jobs(user_location):
    coords = get_coordinates(user_location)
    if coords is None:
        return "❌ Location not found."

    user_lat, user_lng = coords
    results = []

    for _, row in jobs_df.iterrows():

        # ***** FIXED — NOW MATCHES BOTH CITY + STATE *****
        city_match = city_df[
            (city_df["city_ascii"].str.lower() == row["client_city"].lower()) &
            (city_df["state_name"].str.lower() == row["state"].lower())
        ]

        # If not found, skip
        if city_match.empty:
            continue

        job_lat = float(city_match.iloc[0]["lat"])
        job_lng = float(city_match.iloc[0]["lng"])

        distance = geodesic((user_lat, user_lng), (job_lat, job_lng)).km

        results.append({
            "client": row["client_name"],
            "city": row["client_city"],
            "state": row["state"],
            "distance": distance,
            "language": row["language"],
            "pay_rate": row["pay_rate"],
            "gender": row["gender"],
            "notes": row["order_notes"]
        })

    # Sort by distance
    results = sorted(results, key=lambda x: x["distance"])

    # Format output
    output = []
    for r in results[:10]:
        block = f"""
{r['client']}
📍 {r['city']}, {r['state']}
🧭 Distance: {r['distance']:.1f} km
💬 Language: {r['language']}
💰 Pay Rate: {r['pay_rate']}
👤 Gender: {r['gender']}
📝 Notes: {r['notes']}
"""
        output.append(block)

    return "\n".join(output)

# Test
print(find_nearby_jobs("43215"))   # Columbus, OH
