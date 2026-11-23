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

CAREGIVER_KEYWORDS = [
    "caregiver", "cna", "hha", "stna", "pca", "lpn", "rn",
    "hoyer", "dementia", "alzheimer", "assisted living", "home health",
    "bathing", "mobility", "adl", "adls", "patient care", "elderly", "senior care"
]

SKILL_CHECKS = [
    ("hoyer", "Hoyer lift"),
    ("dementia", "Dementia care"),
    ("weekend", "Weekend shift"),
    ("vehicle", "Reliable vehicle"),
]

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

    if raw_bytes[:2] == b"PK":
        xls = pd.ExcelFile(BytesIO(raw_bytes))
        return pd.read_excel(xls, sheet_name=xls.sheet_names[0], dtype=str)

    text = resp.text.lstrip("\ufeff")
    if text.lstrip().startswith(("<!DOCTYPE html", "<html")):
        raise ValueError("Link returned HTML instead of CSV.")

    try:
        return pd.read_csv(StringIO(text), dtype=str)
    except Exception:
        return pd.read_csv(StringIO(text), dtype=str, engine="python", on_bad_lines="skip")


def get_roles(notes: str, pay: str = "", client: str = "") -> List[str]:
    found = list(dict.fromkeys(m.upper() for m in ROLE_RE.findall(f"{notes} {pay} {client}")))
    return found if found else ["CAREGIVER"]


def parse_language(raw_lang: str) -> Dict[str, Any]:
    low = str(raw_lang or "").lower().replace("spanishh", "spanish")
    if "english or bilingual in spanish" in low:
        return {"all": ["English"], "any": [], "is_bilingual": False, "label": "English (Spanish bonus)"}
    if "cantonese" in low and "mandarin" in low:
        return {"all": ["English"], "any": ["Cantonese", "Mandarin"], "is_bilingual": True, "label": "Bilingual: English + (Cantonese or Mandarin)"}
    if "lao" in low and "hmong" in low:
        return {"all": ["English"], "any": ["Lao", "Hmong"], "is_bilingual": True, "label": "Bilingual: English + (Lao or Hmong)"}
    extra = [lang for lang in ["Spanish", "Cantonese", "Mandarin", "Korean"] if lang.lower() in low]
    if extra:
        req = ["English"] + extra
        return {"all": req, "any": [], "is_bilingual": True, "label": f"Bilingual: {' + '.join(req)}"}
    return {"all": ["English"], "any": [], "is_bilingual": False, "label": "English only / English required"}


def match_language(cand_langs: List[str], rule: Dict[str, Any]) -> bool:
    if not cand_langs:
        return True
    cand = {c.strip().title() for c in cand_langs}
    if not all(r in cand for r in rule["all"]):
        return False
    if rule["any"] and not any(opt in cand for opt in rule["any"]):
        return False
    return True


def parse_note_constraints(text: str) -> Dict[str, Any]:
    """Extracts explicit language/skill/bilingual rules from call notes or resume text."""
    low = (text or "").lower()
    no_spanish = bool(re.search(r"\b(no spanish|without spanish|dont speak spanish|don't speak spanish)\b", low))
    only_english = bool(re.search(r"\b(only english|english only|just english)\b", low))
    wants_bilingual = bool(re.search(r"\b(bilingual|bi-lingual)\b", low)) and not no_spanish and not only_english

    langs = ["English"]
    if not no_spanish and not only_english:
        for lang in ["Spanish", "Cantonese", "Mandarin", "Korean", "Lao", "Hmong"]:
            if re.search(rf"\b{lang.lower()}\b", low):
                langs.append(lang)

    required_skills = [kw for kw, _ in SKILL_CHECKS if kw in low]
    return {
        "no_spanish": no_spanish,
        "only_english": only_english,
        "wants_bilingual": wants_bilingual,
        "languages": list(dict.fromkeys(langs)),
        "required_skills": required_skills,
    }


def extract_location_from_text(text: str, zip_map: dict, city_state_map: dict, city_map: dict) -> str:
    if not text:
        return ""
    z_match = ZIP_RE.search(text)
    if z_match and z_match.group(1) in zip_map:
        return z_match.group(1)

    low_text = re.sub(r"[^a-z\s]", " ", text.lower())
    low_text = re.sub(r"\s+", " ", low_text).strip()

    for alias in CITY_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", low_text):
            return alias

    for city_name in sorted(city_map.keys(), key=len, reverse=True):
        if len(city_name) >= 3 and re.search(rf"\b{re.escape(city_name)}\b", low_text):
            return city_name

    return ""


@st.cache_data(ttl=86400)
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
            st.warning(f"Live sheet link could not be parsed ({exc}); using backup CSV.")

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
    location: str = Field(default="", description="Only a clean 5-digit US ZIP code or US City name, e.g. '90012' or 'Boston'")
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
    no_spanish: bool
    only_english: bool
    wants_bilingual: bool
    required_skills: List[str]
    nearby_jobs: List[Dict[str, Any]]
    ranked_jobs: List[Dict[str, Any]]
    expanded: bool
    logs: List[str]


def get_llm():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if ChatGoogleGenerativeAI and key:
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=key, temperature=0.1)
    return None


def resolve_user_coords(loc: str, zip_map: dict, city_state_map: dict, city_map: dict):
    loc = (loc or "").strip()
    if not loc:
        return None
    m = ZIP_RE.search(loc)
    if m and m.group(1) in zip_map:
        return zip_map[m.group(1)]
    low = loc.lower().strip()
    if low in CITY_ALIASES:
        return city_state_map.get(CITY_ALIASES[low])
    if "," in low:
        c, s = [x.strip() for x in low.split(",", 1)]
        if (c, s.upper()) in city_state_map:
            return city_state_map[(c, s.upper())]
        low = c
    return city_map.get(low)


def build_graph(zip_map, city_state_map, city_map, job_records):
    llm = get_llm()

    def parse_intent(state: AgentState) -> Dict[str, Any]:
        loc = state["location"].strip()
        langs = list(state["languages"])
        roles = list(state["roles"])
        logs = list(state["logs"])

        note_info = parse_note_constraints(f"{state['call_notes']} {state['resume_text']}")
        no_spanish = state["no_spanish"] or note_info["no_spanish"]
        only_english = state["only_english"] or note_info["only_english"]
        wants_bilingual = state["wants_bilingual"] or note_info["wants_bilingual"]
        req_skills = list(dict.fromkeys(state["required_skills"] + note_info["required_skills"]))

        if not loc and state["call_notes"].strip():
            loc = extract_location_from_text(state["call_notes"], zip_map, city_state_map, city_map)

        if llm and (state["call_notes"].strip() or state["resume_text"].strip()):
            try:
                parsed = llm.with_structured_output(ExtractedIntent).invoke(
                    f"Extract only a clean 5-digit US ZIP or US City name, spoken languages, and caregiver roles (CAREGIVER/HHA/CNA/STNA/PCA).\n"
                    f"Notes: {state['call_notes']}\nResume: {state['resume_text'][:2000]}"
                )
                if not loc and parsed.location:
                    candidate_loc = extract_location_from_text(parsed.location, zip_map, city_state_map, city_map) or parsed.location.strip()
                    if resolve_user_coords(candidate_loc, zip_map, city_state_map, city_map):
                        loc = candidate_loc
                if not only_english:
                    langs = list(dict.fromkeys(["English"] + langs + parsed.languages))
                if not roles and parsed.roles:
                    roles = [r.upper() for r in parsed.roles if r.upper() in {"CAREGIVER", "HHA", "CNA", "STNA", "PCA"}]
            except Exception:
                pass
        elif not loc:
            loc = extract_location_from_text(f"{state['call_notes']} {state['resume_text']}", zip_map, city_state_map, city_map)

        if only_english:
            langs = ["English"]
        elif no_spanish:
            langs = [l for l in langs if l.lower() != "spanish"]
            if "English" not in langs:
                langs.insert(0, "English")
        elif langs and "English" not in langs:
            langs.insert(0, "English")

        logs.append(f"Parsed: loc='{loc}', langs={langs}, no_spanish={no_spanish}, only_english={only_english}, bilingual={wants_bilingual}, skills={req_skills}")
        return {
            "location": loc,
            "languages": langs,
            "roles": roles,
            "no_spanish": no_spanish,
            "only_english": only_english,
            "wants_bilingual": wants_bilingual,
            "required_skills": req_skills,
            "logs": logs,
        }

    def filter_jobs(state: AgentState) -> Dict[str, Any]:
        loc = state["location"].strip()
        coords = resolve_user_coords(loc, zip_map, city_state_map, city_map)
        if not coords:
            return {
                "nearby_jobs": [],
                "logs": state["logs"] + [f"Could not match US ZIP/City from '{loc or state['call_notes']}'. Enter a US ZIP or US city."],
            }

        hits = []
        for job in job_records:
            dist = geodesic(coords, job["coords"]).miles
            if dist > state["radius"]:
                continue
            if state["roles"] and not any(r in job["roles"] for r in state["roles"]):
                continue

            # Check language rules (including "no spanish", "only english", or "bilingual")
            if state["only_english"] or state["no_spanish"]:
                if "Spanish" in job["lang_rule"]["all"] or "Spanish" in job["lang_rule"].get("preferred", []):
                    continue
            if state["wants_bilingual"] and not job["lang_rule"].get("is_bilingual", False):
                continue
            if not match_language(state["languages"], job["lang_rule"]):
                continue

            # Check required skills from call notes/resume against Order Notes
            notes_low = job.get("order_notes", "").lower()
            if state["required_skills"] and not all(sk in notes_low for sk in state["required_skills"]):
                continue

            j = dict(job)
            j["miles"] = round(dist, 1)
            hits.append(j)

        hits.sort(key=lambda x: x["miles"])
        return {"nearby_jobs": hits[:15], "logs": state["logs"] + [f"Found {len(hits)} jobs matching all filters within {state['radius']} mi."]}

    def check_results(state: AgentState) -> str:
        if not state["nearby_jobs"] and not state["expanded"] and state["location"]:
            return "expand_radius"
        return "rank_jobs" if state["nearby_jobs"] else END

    def expand_radius(state: AgentState) -> Dict[str, Any]:
        new_rad = min(500, int(state["radius"] * 1.5))
        return {"radius": new_rad, "expanded": True, "logs": state["logs"] + [f"Expanded radius to {new_rad} mi."]}

    def rank_jobs(state: AgentState) -> Dict[str, Any]:
        jobs = state["nearby_jobs"]
        resume_low = state["resume_text"].lower()
        notes_low = state["call_notes"].lower()
        combined_low = f"{notes_low} {resume_low}".strip()

        has_resume = bool(state["resume_text"].strip())
        caregiver_hits = [kw for kw in CAREGIVER_KEYWORDS if kw in combined_low] if combined_low else []

        if llm and (state["call_notes"].strip() or has_resume):
            try:
                summary = [{"job_idx": i, "client": j["client_name"], "city": j["client_city"], "miles": j["miles"], "pay": j["pay_rate"], "lang": j["lang_rule"]["label"], "notes": j["order_notes"][:300]} for i, j in enumerate(jobs)]
                res = llm.with_structured_output(BatchScores).invoke(
                    f"Score how well this candidate matches US healthcare/caregiver jobs (0-100). "
                    f"If a resume/notes are provided and have NO healthcare/caregiver/CNA/HHA/home-care background, give a low score (0-25).\n"
                    f"Notes: {state['call_notes']}\nResume: {state['resume_text'][:2000]}\nJobs: {summary}"
                )
                s_map = {item.job_idx: item for item in res.items}
                ranked = [{**j, "score": s_map[i].score, "pitch": s_map[i].pitch} if i in s_map else {**j, "score": 20, "pitch": ""} for i, j in enumerate(jobs)]
                ranked.sort(key=lambda x: (-x["score"], x["miles"]))
                return {"ranked_jobs": ranked, "logs": state["logs"] + ["Scored with Gemini."]}
            except Exception:
                pass

        ranked = []
        for j in jobs:
            if (has_resume or notes_low) and not caregiver_hits:
                score = max(5, 20 - int(j["miles"] * 0.2))
                pitch = f"Mismatch warning: Candidate profile shows no caregiver/CNA/HHA experience for {j['client_name']} in {j['client_city']}, {j['state']}."
            else:
                bonus = sum(10 for kw in ["hoyer", "dementia", "weekend", "vehicle"] if kw in j["order_notes"].lower() and kw in combined_low)
                score = max(0, min(100, 65 + bonus - int(j["miles"] * 0.3)))
                pitch = f"{j['client_name']} in {j['client_city']}, {j['state']} ({j['miles']} mi) pays {j['pay_rate']}."
            ranked.append({**j, "score": score, "pitch": pitch})

        ranked.sort(key=lambda x: (-x["score"], x["miles"]))
        return {"ranked_jobs": ranked, "logs": state["logs"] + [f"Ranked {len(ranked)} jobs."]}

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
    call_notes = st.text_area(
        "Live Call Notes:",
        placeholder="e.g. Near 90012, no spanish / only english / bilingual, has hoyer lift experience",
    )
    resume_file = st.file_uploader("Candidate Resume (PDF/TXT):", type=["pdf", "txt"])
with col2:
    location = st.text_input("ZIP or City (leave empty to read from Call Notes):", value="")
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
        "no_spanish": False,
        "only_english": False,
        "wants_bilingual": False,
        "required_skills": [],
        "nearby_jobs": [],
        "ranked_jobs": [],
        "expanded": False,
        "logs": [],
    })

    if resume_text.strip() and not any(kw in f"{call_notes} {resume_text}".lower() for kw in CAREGIVER_KEYWORDS):
        st.warning("This dataset is for **US healthcare/caregiver roles**, and your uploaded resume does not show caregiver/CNA/HHA/home-care experience—so fit scores are low.")

    with st.expander("🧭 Execution Steps"):
        for step in out["logs"]:
            st.write("•", step)

    for job in out["ranked_jobs"]:
        with st.expander(f"{job['client_name']} — {job['client_city']}, {job['state']} ({job['miles']} mi) | Fit: {job['score']}/100"):
            st.write(f"**Roles:** {', '.join(job['roles'])} | **Language:** {job['lang_rule']['label']} | **Pay:** {job['pay_rate']}")
            st.write(job["order_notes"])
            st.info(job["pitch"])
