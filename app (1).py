import os
import re
from io import StringIO
from typing import TypedDict, List, Dict, Any

import pandas as pd
import requests
import streamlit as st
from geopy.distance import geodesic
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None


# ==============================================================================
# 1. CONFIG & DATA URLS
# ==============================================================================
st.set_page_config(page_title="Agentic Healthcare Recruiter Copilot", page_icon="🩺", layout="wide")

CITY_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOB_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"
LOCAL_JOB_FILE = "all job.csv"

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
ROLE_RE = re.compile(r"\b(Caregiver|HHA|CNA|STNA|PCA|LPN|RN)\b", re.IGNORECASE)
CITY_ALIASES = {
    "downtown los angeles": ("los angeles", "CA"),
    "west ashley": ("charleston", "SC"),
}

# Load API key & optional live sheet link from Streamlit Secrets if available
if hasattr(st, "secrets"):
    if "GEMINI_API_KEY" in st.secrets:
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
    if "GOOGLE_API_KEY" in st.secrets:
        os.environ["GOOGLE_API_KEY"] = st.secrets["GOOGLE_API_KEY"]


# ==============================================================================
# 2. DATA CLEANING HELPERS (ROLES, LANGUAGES, ZIP/CITY)
# ==============================================================================
def get_roles(notes: str, pay: str = "", client: str = "") -> List[str]:
    """Finds job titles in notes/pay; defaults to CAREGIVER if none are mentioned."""
    found = list(dict.fromkeys(m.upper() for m in ROLE_RE.findall(f"{notes} {pay} {client}")))
    return found if found else ["CAREGIVER"]


def parse_language(raw_lang: str) -> Dict[str, Any]:
    """Normalizes messy Language column: English is always required."""
    low = str(raw_lang or "").lower().replace("spanishh", "spanish")

    if "english or bilingual in spanish" in low:
        return {"all": ["English"], "any": [], "label": "English (Spanish bonus)"}
    if "cantonese" in low and "mandarin" in low:
        return {"all": ["English"], "any": ["Cantonese", "Mandarin"], "label": "English + (Cantonese or Mandarin)"}
    if "lao" in low and "hmong" in low:
        return {"all": ["English"], "any": ["Lao", "Hmong"], "label": "English + (Lao or Hmong)"}

    extra = [lang for lang in ["Spanish", "Cantonese", "Mandarin", "Korean"] if lang.lower() in low]
    if extra:
        req = ["English"] + extra
        return {"all": req, "any": [], "label": f"Bilingual: {' + '.join(req)}"}

    return {"all": ["English"], "any": [], "label": "English required"}


def match_language(cand_langs: List[str], rule: Dict[str, Any]) -> bool:
    """Returns True if candidate speaks English + required languages."""
    if not cand_langs:
        return True
    cand = {c.strip().title() for c in cand_langs}
    if not all(r in cand for r in rule["all"]):
        return False
    if rule["any"] and not any(opt in cand for opt in rule["any"]):
        return False
    return True


@st.cache_data(ttl=300)
def load_data(sheet_url: str = ""):
    """Loads city coordinates and live/local jobs CSV, recovering missing ZIPs via City."""
    city_df = pd.read_csv(StringIO(requests.get(CITY_URL, timeout=15).text), dtype=str).fillna("")
    city_df.columns = city_df.columns.str.lower().str.strip().str.replace(" ", "_")

    zip_map, city_state_map, city_map = {}, {}, {}
    for _, r in city_df.iterrows():
        coords = (float(r["lat"]), float(r["lng"]))
        c_name = r["city"].lower().strip()
        st_id = r.get("state_id", "").upper().strip()
        city_state_map[(c_name, st_id)] = coords
        city_map.setdefault(c_name, coords)
        for z in r["zips"].split():
            zip_map[z] = coords

    if sheet_url.strip():
        jobs_df = pd.read_csv(StringIO(requests.get(sheet_url.strip(), timeout=15).text), dtype=str)
        source = "Live Google Sheet"
    elif os.path.exists(LOCAL_JOB_FILE):
        jobs_df = pd.read_csv(LOCAL_JOB_FILE, dtype=str)
        source = "Local CSV"
    else:
        jobs_df = pd.read_csv(StringIO(requests.get(JOB_URL, timeout=15).text), dtype=str)
        source = "GitHub CSV"

    jobs_df = jobs_df.fillna("")
    jobs_df.columns = jobs_df.columns.str.lower().str.strip().str.replace(" ", "_")

    def find_coords(row):
        m = ZIP_RE.search(f"{row.get('zip_code', '')} {row.get('order_notes', '')}")
        if m and m.group(1) in zip_map:
            return zip_map[m.group(1)]
        c = row.get("client_city", "").lower().strip()
        s = row.get("state", "").upper().strip()
        if c in CITY_ALIASES:
            return city_state_map.get(CITY_ALIASES[c])
        return city_state_map.get((c, s)) or city_map.get(c)

    jobs_df["coords"] = jobs_df.apply(find_coords, axis=1)
    jobs_df["roles"] = jobs_df.apply(lambda r: get_roles(r.get("order_notes", ""), r.get("pay_rate", ""), r.get("client_name", "")), axis=1)
    jobs_df["lang_rule"] = jobs_df["language"].apply(parse_language)

    return zip_map, city_state_map, city_map, jobs_df.dropna(subset=["coords"]), source


# ==============================================================================
# 3. PYDANTIC SCHEMAS & LANGGRAPH WORKFLOW (4 NODES)
# ==============================================================================
class ExtractedIntent(BaseModel):
    location: str = Field(description="5-digit US ZIP code or City")
    languages: List[str] = Field(default_factory=lambda: ["English"])
    roles: List[str] = Field(default_factory=list, description="CAREGIVER, HHA, CNA, STNA, PCA")

class JobScore(BaseModel):
    job_idx: int
    score: int = Field(ge=0, le=100)
    pitch: str = Field(description="Short 2-sentence phone pitch for the candidate")

class BatchScores(BaseModel):
    items: List[JobScore]

class AgentState(TypedDict):
    call_notes: str
    resume_text: str
    location: str
    radius: int
    languages: List[str]
    roles: List[str]
    nearby_jobs: List[Dict[str, Any]]
    ranked_jobs: List[Dict[str, Any]]
    expanded: bool
    logs: List[str]


def get_llm():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if ChatGoogleGenerativeAI and key:
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=key, temperature=0.1)
    return None


def build_graph(zip_map, city_state_map, city_map, job_records):
    llm = get_llm()

    # Node 1: Parse candidate requirements from call notes / resume
    def parse_intent(state: AgentState) -> Dict[str, Any]:
        loc, langs, roles = state["location"].strip(), list(state["languages"]), list(state["roles"])
        logs = list(state["logs"])

        if llm and (state["call_notes"].strip() or state["resume_text"].strip()):
            try:
                parsed = llm.with_structured_output(ExtractedIntent).invoke(
                    f"Extract ZIP/city, languages, and caregiver roles.\nNotes: {state['call_notes']}\nResume: {state['resume_text'][:2000]}"
                )
                if not loc and parsed.location:
                    loc = parsed.location
                langs = list(dict.fromkeys(["English"] + langs + parsed.languages))
                if not roles and parsed.roles:
                    roles = [r.upper() for r in parsed.roles]
                logs.append(f"Node 1 (LLM Parse): location={loc}, langs={langs}, roles={roles or 'ALL'}")
            except Exception:
                logs.append("Node 1 (Fallback): used manual filters.")
        else:
            if not loc:
                m = ZIP_RE.search(f"{state['call_notes']} {state['resume_text']}")
                if m:
                    loc = m.group(1)
            logs.append(f"Node 1 (Rule Parse): location={loc}, langs={langs}, roles={roles or 'ALL'}")

        if langs and "English" not in langs:
            langs.insert(0, "English")
        return {"location": loc, "languages": langs, "roles": roles, "logs": logs}

    # Node 2: Filter jobs by geodesic distance (miles), role, and language
    def filter_jobs(state: AgentState) -> Dict[str, Any]:
        loc = state["location"].strip()
        logs = list(state["logs"])

        m = ZIP_RE.search(loc)
        coords = zip_map.get(m.group(1)) if m else city_map.get(loc.lower())
        if not coords and "," in loc:
            c, s = [x.strip() for x in loc.split(",", 1)]
            coords = city_state_map.get((c.lower(), s.upper()))

        if not coords:
            logs.append(f"Node 2 (Geo Filter): Could not find coordinates for '{loc}'.")
            return {"nearby_jobs": [], "logs": logs}

        hits = []
        for job in job_records:
            dist = geodesic(coords, job["coords"]).miles
            if dist <= state["radius"]:
                if state["roles"] and not any(r in job["roles"] for r in state["roles"]):
                    continue
                if not match_language(state["languages"], job["lang_rule"]):
                    continue
                j = dict(job)
                j["miles"] = round(dist, 1)
                hits.append(j)

        hits.sort(key=lambda x: x["miles"])
        logs.append(f"Node 2 (Geo Filter): Found {len(hits)} jobs within {state['radius']} miles.")
        return {"nearby_jobs": hits[:15], "logs": logs}

    # Conditional Edge: Auto-expand radius if 0 jobs found
    def check_results(state: AgentState) -> str:
        if not state["nearby_jobs"] and not state["expanded"] and state["location"]:
            return "expand_radius"
        return "rank_jobs" if state["nearby_jobs"] else END

    # Node 3: Expand search radius by 1.5x
    def expand_radius(state: AgentState) -> Dict[str, Any]:
        new_rad = min(500, int(state["radius"] * 1.5))
        return {
            "radius": new_rad,
            "expanded": True,
            "logs": state["logs"] + [f"Node 3 (Auto-Expand): 0 hits -> expanded radius to {new_rad} miles."],
        }

    # Node 4: Score & rank matches (LLM or deterministic fallback)
    def rank_jobs(state: AgentState) -> Dict[str, Any]:
        jobs = state["nearby_jobs"]
        logs = list(state["logs"])

        if llm and (state["call_notes"].strip() or state["resume_text"].strip()):
            try:
                summary = [{"job_idx": i, "client": j["client_name"], "city": j["client_city"], "miles": j["miles"], "pay": j["pay_rate"], "notes": j["order_notes"][:300]} for i, j in enumerate(jobs)]
                res = llm.with_structured_output(BatchScores).invoke(
                    f"Score fit (0-100) and write a 2-sentence phone pitch.\nNotes: {state['call_notes']}\nResume: {state['resume_text'][:2000]}\nJobs: {summary}"
                )
                score_map = {item.job_idx: item for item in res.items}
                ranked = [{**j, "score": score_map[i].score, "pitch": score_map[i].pitch} if i in score_map else {**j, "score": 70, "pitch": ""} for i, j in enumerate(jobs)]
                ranked.sort(key=lambda x: (-x["score"], x["miles"]))
                logs.append("Node 4 (LLM Ranker): Scored matches with Gemini.")
                return {"ranked_jobs": ranked, "logs": logs}
            except Exception:
                pass

        # Simple offline fallback scoring
        cand_text = f"{state['call_notes']} {state['resume_text']}".lower()
        ranked = []
        for j in jobs:
            bonus = sum(8 for kw in ["hoyer", "dementia", "weekend", "vehicle"] if kw in j["order_notes"].lower() and kw in cand_text)
            score = max(0, min(100, 75 + bonus - int(j["miles"] * 0.3)))
            pitch = f"We have an opening with {j['client_name']} in {j['client_city']}, {j['state']} ({j['miles']} mi away) paying {j['pay_rate']}."
            ranked.append({**j, "score": score, "pitch": pitch})

        ranked.sort(key=lambda x: (-x["score"], x["miles"]))
        logs.append("Node 4 (Rule Ranker): Ranked matches by skill & distance.")
        return {"ranked_jobs": ranked, "logs": logs}

    graph = StateGraph(AgentState)
    graph.add_node("parse_intent", parse_intent)
    graph.add_node("filter_jobs", filter_jobs)
    graph.add_node("expand_radius", expand_radius)
    graph.add_node("rank_jobs", rank_jobs)

    graph.add_edge(START, "parse_intent")
    graph.add_edge("parse_intent", "filter_jobs")
    graph.add_conditional_edges("filter_jobs", check_results, ["expand_radius", "rank_jobs", END])
    graph.add_edge("expand_radius", "filter_jobs")
    graph.add_edge("rank_jobs", END)
    return graph.compile()


# ==============================================================================
# 4. STREAMLIT UI
# ==============================================================================
st.title("🩺 Agentic Healthcare Recruiter Copilot (LangGraph)")
st.caption("Live Google Sheet Sync • Free ZIP/City Distance • Smart Role & Bilingual Matching")

default_sheet = st.secrets.get("LIVE_JOB_CSV_URL", "") if hasattr(st, "secrets") else ""
sheet_url = st.text_input("Live Google Sheet CSV Link (auto-loads from Secrets if set):", value=default_sheet)

zip_map, city_state_map, city_map, valid_jobs, data_source = load_data(sheet_url)
st.info(f"Loaded **{len(valid_jobs)}** geocoded jobs from **{data_source}** (refreshes every 5 mins).")

col1, col2 = st.columns([1.3, 1])
with col1:
    call_notes = st.text_area("Live Call Notes:", placeholder="e.g. Candidate in 90012, speaks Spanish & English, CNA with Hoyer lift & car")
    resume_file = st.file_uploader("Upload Candidate Resume (PDF/TXT):", type=["pdf", "txt"])

with col2:
    location = st.text_input("ZIP Code or City:", value="90012")
    radius = st.slider("Radius (miles):", 5, 250, 35, step=5)
    roles = st.multiselect("Roles (defaults to CAREGIVER if unmentioned):", ["CAREGIVER", "HHA", "CNA", "STNA", "PCA"])
    languages = st.multiselect("Languages (English is mandatory):", ["English", "Spanish", "Cantonese", "Mandarin", "Korean"], default=["English"])

if st.button("🚀 Find & Score Matching Jobs", type="primary", use_container_width=True):
    resume_text = ""
    if resume_file:
        if fitz and resume_file.name.endswith(".pdf"):
            doc = fitz.open(stream=resume_file.read(), filetype="pdf")
            resume_text = "\n".join(p.get_text() for p in doc)
        else:
            resume_text = resume_file.read().decode("utf-8", errors="ignore")

    app = build_graph(zip_map, city_state_map, city_map, valid_jobs.to_dict(orient="records"))
    result = app.invoke({
        "call_notes": call_notes,
        "resume_text": resume_text,
        "location": location,
        "radius": radius,
        "languages": languages,
        "roles": roles,
        "nearby_jobs": [],
        "ranked_jobs": [],
        "expanded": False,
        "logs": [],
    })

    with st.expander("🧭 LangGraph Execution Steps"):
        for step in result["logs"]:
            st.write("•", step)

    if not result["ranked_jobs"]:
        st.warning("No matching jobs found.")
    else:
        st.success(f"Found {len(result['ranked_jobs'])} matches within {result['radius']} miles:")
        for i, job in enumerate(result["ranked_jobs"], 1):
            with st.expander(f"#{i} | {job['client_name']} — {job['client_city']}, {job['state']} ({job['miles']} mi) | Fit: {job['score']}/100", expanded=(i <= 3)):
                st.write(f"**Roles:** {', '.join(job['roles'])} | **Language:** {job['lang_rule']['label']} | **Pay:** {job['pay_rate']}")
                st.write(f"**Notes:** {job['order_notes']}")
                st.info(f"🎙️ **Call Pitch:** {job['pitch']}")
