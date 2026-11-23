import os
import re
from datetime import datetime
from io import StringIO, BytesIO
from typing import TypedDict, List, Dict, Any

import pandas as pd
import requests
import streamlit as st
from geopy.distance import geodesic
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

try:
    import pymupdf as fitz
except ImportError:
    fitz = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None

st.set_page_config(page_title="Healthcare Recruiter Copilot", page_icon="🩺", layout="wide")

CITY_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOB_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"
LOCAL_JOB_FILE = "all job.csv"

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
ROLE_RE = re.compile(r"\b(Caregiver|HHA|CNA|STNA|PCA|LPN|RN)\b", re.IGNORECASE)
CITY_ALIASES = {"downtown los angeles": ("los angeles", "CA"), "west ashley": ("charleston", "SC")}

if hasattr(st, "secrets"):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if k in st.secrets:
            os.environ["GEMINI_API_KEY"] = st.secrets[k]


def to_csv_url(url: str) -> str:
    url = url.strip()
    if "docs.google.com/spreadsheets/d/" in url and "/export" not in url and "/pub" not in url:
        sheet_id = url.split("/d/")[1].split("/")[0]
        m = re.search(r"gid=(\d+)", url)
        gid = m.group(1) if m else "0"
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    return url


def fetch_sheet_safely(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    raw_bytes = resp.content

    # If the link points to an .xlsx file, read the first sheet directly
    if raw_bytes[:2] == b"PK":
        xls = pd.ExcelFile(BytesIO(raw_bytes))
        return pd.read_excel(xls, sheet_name=xls.sheet_names[0], dtype=str)

    text = resp.text.lstrip("\ufeff")
    if text.lstrip().startswith(("<!DOCTYPE html", "<html")):
        raise ValueError("Google Sheet link returned HTML instead of CSV. Use Publish to web -> CSV.")

    try:
        return pd.read_csv(StringIO(text), dtype=str)
    except Exception:
        # Fallback for messy live rows with unquoted commas or line breaks
        return pd.read_csv(StringIO(text), dtype=str, engine="python", on_bad_lines="skip")


def get_roles(notes: str, pay: str = "", client: str = "") -> List[str]:
    found = list(dict.fromkeys(m.upper() for m in ROLE_RE.findall(f"{notes} {pay} {client}")))
    return found if found else ["CAREGIVER"]


def parse_language(raw_lang: str) -> Dict[str, Any]:
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
    if not cand_langs:
        return True
    cand = {c.strip().title() for c in cand_langs}
    if not all(r in cand for r in rule["all"]):
        return False
    if rule["any"] and not any(opt in cand for opt in rule["any"]):
        return False
    return True


@st.cache_data(ttl=86400)  # Cached once per day (end-of-day sync)
def load_data(sheet_url: str = "", day_key: str = ""):
    _ = day_key
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

    clean_url = to_csv_url(sheet_url)
    jobs_df = None
    source = ""

    if clean_url:
        try:
            jobs_df = fetch_sheet_safely(clean_url)
            source = "Live Google Sheet (Daily Sync)"
        except Exception as exc:
            st.warning(f"Live sheet link could not be parsed ({exc}); falling back to backup CSV.")

    if jobs_df is None:
        if os.path.exists(LOCAL_JOB_FILE):
            jobs_df = pd.read_csv(LOCAL_JOB_FILE, dtype=str)
            source = "Local CSV Backup"
        else:
            jobs_df = pd.read_csv(StringIO(requests.get(JOB_URL, timeout=15).text), dtype=str)
            source = "GitHub CSV Backup"

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


class ExtractedIntent(BaseModel):
    location: str = Field(description="5-digit US ZIP code or City")
    languages: List[str] = Field(default_factory=lambda: ["English"])
    roles: List[str] = Field(default_factory=list, description="CAREGIVER, HHA, CNA, STNA, PCA")


class JobScore(BaseModel):
    job_idx: int
    score: int = Field(ge=0, le=100)
    pitch: str = Field(description="2-sentence call pitch")


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
                logs.append(f"Parsed: loc={loc}, langs={langs}, roles={roles or 'ALL'}")
            except Exception:
                logs.append("Used manual/rule filters.")
        elif not loc:
            m = ZIP_RE.search(f"{state['call_notes']} {state['resume_text']}")
            if m:
                loc = m.group(1)
        if langs and "English" not in langs:
            langs.insert(0, "English")
        return {"location": loc, "languages": langs, "roles": roles, "logs": logs}

    def filter_jobs(state: AgentState) -> Dict[str, Any]:
        loc = state["location"].strip()
        m = ZIP_RE.search(loc)
        coords = zip_map.get(m.group(1)) if m else city_map.get(loc.lower())
        if not coords and "," in loc:
            c, s = [x.strip() for x in loc.split(",", 1)]
            coords = city_state_map.get((c.lower(), s.upper()))
        if not coords:
            return {"nearby_jobs": [], "logs": state["logs"] + [f"Unknown location: {loc}"]}

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
        return {"nearby_jobs": hits[:15], "logs": state["logs"] + [f"Found {len(hits)} jobs within {state['radius']} mi."]}

    def check_results(state: AgentState) -> str:
        if not state["nearby_jobs"] and not state["expanded"] and state["location"]:
            return "expand_radius"
        return "rank_jobs" if state["nearby_jobs"] else END

    def expand_radius(state: AgentState) -> Dict[str, Any]:
        new_rad = min(500, int(state["radius"] * 1.5))
        return {"radius": new_rad, "expanded": True, "logs": state["logs"] + [f"Expanded radius to {new_rad} mi."]}

    def rank_jobs(state: AgentState) -> Dict[str, Any]:
        jobs = state["nearby_jobs"]
        if llm and (state["call_notes"].strip() or state["resume_text"].strip()):
            try:
                summary = [{"job_idx": i, "client": j["client_name"], "city": j["client_city"], "miles": j["miles"], "pay": j["pay_rate"], "notes": j["order_notes"][:300]} for i, j in enumerate(jobs)]
                res = llm.with_structured_output(BatchScores).invoke(
                    f"Score fit (0-100) and write a 2-sentence pitch.\nNotes: {state['call_notes']}\nResume: {state['resume_text'][:2000]}\nJobs: {summary}"
                )
                s_map = {item.job_idx: item for item in res.items}
                ranked = [{**j, "score": s_map[i].score, "pitch": s_map[i].pitch} if i in s_map else {**j, "score": 70, "pitch": ""} for i, j in enumerate(jobs)]
                ranked.sort(key=lambda x: (-x["score"], x["miles"]))
                return {"ranked_jobs": ranked, "logs": state["logs"] + ["Scored with Gemini."]}
            except Exception:
                pass

        cand_text = f"{state['call_notes']} {state['resume_text']}".lower()
        ranked = []
        for j in jobs:
            bonus = sum(8 for kw in ["hoyer", "dementia", "weekend", "vehicle"] if kw in j["order_notes"].lower() and kw in cand_text)
            score = max(0, min(100, 75 + bonus - int(j["miles"] * 0.3)))
            pitch = f"{j['client_name']} in {j['client_city']}, {j['state']} ({j['miles']} mi) pays {j['pay_rate']}."
            ranked.append({**j, "score": score, "pitch": pitch})
        ranked.sort(key=lambda x: (-x["score"], x["miles"]))
        return {"ranked_jobs": ranked, "logs": state["logs"] + ["Scored by distance & keywords."]}

    g = StateGraph(AgentState)
    g.add_node("parse_intent", parse_intent)
    g.add_node("filter_jobs", filter_jobs)
    g.add_node("expand_radius", expand_radius)
    g.add_node("rank_jobs", rank_jobs)
    g.add_edge(START, "parse_intent")
    g.add_edge("parse_intent", "filter_jobs")
    g.add_conditional_edges("filter_jobs", check_results, ["expand_radius", "rank_jobs", END])
    g.add_edge("expand_radius", "filter_jobs")
    g.add_edge("rank_jobs", END)
    return g.compile()


st.title("🩺 Healthcare Recruiter Copilot")

default_sheet = st.secrets.get("LIVE_JOB_CSV_URL", "") if hasattr(st, "secrets") else ""
sheet_url = st.text_input("Google Sheet / CSV Link (auto-loads from Secrets if set):", value=default_sheet)
day_key = datetime.now().strftime("%Y-%m-%d")

zip_map, city_state_map, city_map, valid_jobs, data_source = load_data(sheet_url, day_key)
st.caption(f"Source: {data_source} • Geocoded jobs: {len(valid_jobs)} • Daily cache key: {day_key}")

col1, col2 = st.columns([1.3, 1])
with col1:
    call_notes = st.text_area("Live Call Notes:", placeholder="e.g. Near 90012, speaks English & Spanish, CNA with Hoyer lift")
    resume_file = st.file_uploader("Candidate Resume (PDF/TXT):", type=["pdf", "txt"])
with col2:
    location = st.text_input("ZIP or City:", value="90012")
    radius = st.slider("Radius (miles):", 5, 250, 35, step=5)
    roles = st.multiselect("Roles (defaults to CAREGIVER):", ["CAREGIVER", "HHA", "CNA", "STNA", "PCA"])
    languages = st.multiselect("Languages (English required):", ["English", "Spanish", "Cantonese", "Mandarin", "Korean"], default=["English"])

if st.button("Find Matching Jobs", type="primary", use_container_width=True):
    resume_text = ""
    if resume_file:
        if fitz and resume_file.name.endswith(".pdf"):
            resume_text = "\n".join(p.get_text() for p in fitz.open(stream=resume_file.read(), filetype="pdf"))
        else:
            resume_text = resume_file.read().decode("utf-8", errors="ignore")

    out = build_graph(zip_map, city_state_map, city_map, valid_jobs.to_dict(orient="records")).invoke({
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

    for job in out["ranked_jobs"]:
        with st.expander(f"{job['client_name']} — {job['client_city']}, {job['state']} ({job['miles']} mi) | Fit: {job['score']}/100"):
            st.write(f"**Roles:** {', '.join(job['roles'])} | **Language:** {job['lang_rule']['label']} | **Pay:** {job['pay_rate']}")
            st.write(job["order_notes"])
            st.info(job["pitch"])
