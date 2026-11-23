import os
import re
from io import StringIO
from typing import TypedDict, List, Optional, Dict, Any

import pandas as pd
import requests
import streamlit as st
from geopy.distance import geodesic
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

# Optional LLM & PDF imports (app still works deterministically if API key is not set)
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None


# ==============================================================================
# 1. PAGE CONFIG & CLEAN UI STYLING
# ==============================================================================
st.set_page_config(
    page_title="Agentic Healthcare Recruiter Copilot",
    page_icon="🩺",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.4rem; max-width: 1220px; }
    .hero-box {
        background: linear-gradient(135deg, #0F172A 0%, #1E293B 60%, #0F766E 100%);
        color: #F8FAFC;
        padding: 1.3rem 1.7rem;
        border-radius: 14px;
        margin-bottom: 1.1rem;
    }
    .hero-title { font-size: 1.5rem; font-weight: 800; margin: 0; color: #FFFFFF; }
    .hero-sub { font-size: 0.9rem; color: #CBD5E1; margin-top: 0.25rem; }
    .pill {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-size: 0.76rem;
        font-weight: 700;
        margin-right: 0.35rem;
    }
    .pill-role { background: #CCFBF1; color: #0F766E; }
    .pill-lang { background: #E0E7FF; color: #3730A3; }
    .pill-score { background: #FEF3C7; color: #92400E; }
    .pitch-box {
        background: #F8FAFC;
        border-left: 4px solid #0D9488;
        padding: 0.75rem 1rem;
        border-radius: 8px;
        margin-top: 0.5rem;
        font-size: 0.92rem;
        color: #1E293B;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==============================================================================
# 2. DATA SOURCES & REGEX PATTERNS
# ==============================================================================
CITY_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job%20cities.csv"
JOB_URL = "https://raw.githubusercontent.com/Ujwal-Bamb/all-jobs-finder/refs/heads/main/all%20job.csv"
LOCAL_JOB_FILE = "all job.csv"

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
ROLE_RE = re.compile(r"\b(Caregiver|HHA|CNA|STNA|PCA|LPN|RN)\b", re.IGNORECASE)

# Neighborhood/alias fallback mapping when a sub-city isn't a standalone row in city_df
CITY_ALIASES = {
    "downtown los angeles": ("los angeles", "CA"),
    "west ashley": ("charleston", "SC"),
    "north charleston": ("north charleston", "SC"),
}


# ==============================================================================
# 3. DOMAIN NORMALIZERS (ROLES, MESSY LANGUAGES, ZIP/CITY GEOCODING)
# ==============================================================================
def extract_job_roles(order_notes: str, pay_rate: str = "", client_name: str = "") -> List[str]:
    """
    Extracts explicit clinical/caregiver titles (Caregiver, HHA, CNA, STNA, PCA).
    Rule: If none of these titles appear in Order Notes (or Pay Rate/Client Name),
    default to ['CAREGIVER'].
    """
    combined = f"{order_notes or ''} {pay_rate or ''} {client_name or ''}"
    found = list(dict.fromkeys(m.upper() for m in ROLE_RE.findall(combined)))
    return found if found else ["CAREGIVER"]


def normalize_language_requirement(raw_lang: str) -> Dict[str, Any]:
    """
    Normalizes messy Language column values from all job.csv:
    - 'Bilingual in Spanish' / 'bilingual in Spanishh' / 'Bi-Lingual ENG/SPAN'
        -> English is required + Spanish is required (BOTH).
    - 'English Or bilingual in Spanish'
        -> English is required; Spanish is optional bonus (EITHER English-only OR English+Spanish).
    - 'Cantonese, Mandarin' / 'bilingual in Cantonese, Mandarin'
        -> English is required + (Cantonese OR Mandarin).
    - 'bilingual in Korean'
        -> English is required + Korean.
    - 'Spanish' / 'Cantonese'
        -> English is always required in US healthcare + that target language.
    """
    text = str(raw_lang or "").strip()
    low = text.lower()

    # Typo cleanup
    low = low.replace("spanishh", "spanish").replace("cantones", "cantonese").replace("manderin", "mandarin")

    # Case 1: "English Or bilingual in Spanish" -> English alone qualifies, Spanish is a plus
    if "english or bilingual in spanish" in low:
        return {
            "raw": text,
            "english_required": True,
            "required_all": ["English"],
            "required_any": [],
            "preferred": ["Spanish"],
            "rule_summary": "English required (Bilingual Spanish preferred, not mandatory)",
        }

    # Case 2: Multi-language "Cantonese, Mandarin" (with or without 'bilingual')
    if "cantonese" in low and "mandarin" in low:
        return {
            "raw": text,
            "english_required": True,
            "required_all": ["English"],
            "required_any": ["Cantonese", "Mandarin"],
            "preferred": [],
            "rule_summary": "Bilingual: English (mandatory) + Cantonese OR Mandarin",
        }

    # Case 3: "Lao, Hmong"
    if "lao" in low and "hmong" in low:
        return {
            "raw": text,
            "english_required": True,
            "required_all": ["English"],
            "required_any": ["Lao", "Hmong"],
            "preferred": [],
            "rule_summary": "Bilingual: English (mandatory) + Lao OR Hmong",
        }

    # Case 4: Bilingual / specific non-English languages (English is always mandatory)
    detected_non_eng = []
    for lang_key, canonical in [
        ("spanish", "Spanish"),
        ("span", "Spanish"),
        ("cantonese", "Cantonese"),
        ("mandarin", "Mandarin"),
        ("korean", "Korean"),
        ("lao", "Lao"),
        ("hmong", "Hmong"),
    ]:
        if lang_key in low and canonical not in detected_non_eng:
            detected_non_eng.append(canonical)

    if detected_non_eng:
        req_all = ["English"] + detected_non_eng
        return {
            "raw": text,
            "english_required": True,
            "required_all": req_all,
            "required_any": [],
            "preferred": [],
            "rule_summary": f"Bilingual: {' + '.join(req_all)} required",
        }

    # Default: English only
    return {
        "raw": text or "English",
        "english_required": True,
        "required_all": ["English"],
        "required_any": [],
        "preferred": [],
        "rule_summary": "English required",
    }


def candidate_matches_language(candidate_langs: List[str], job_lang_rule: Dict[str, Any]) -> bool:
    """Checks whether candidate's spoken languages satisfy the normalized job language rule."""
    if not candidate_langs:
        return True  # No language filter applied

    cand_set = {c.strip().title() for c in candidate_langs if c.strip()}
    # Rule: English is mandatory across all jobs
    if job_lang_rule["english_required"] and "English" not in cand_set:
        return False

    for req in job_lang_rule["required_all"]:
        if req not in cand_set:
            return False

    if job_lang_rule["required_any"]:
        if not any(opt in cand_set for opt in job_lang_rule["required_any"]):
            return False

    return True


@st.cache_data(ttl=300)
def load_and_clean_datasets(custom_job_csv_url: str = ""):
    """Loads city coordinate lookup and live/local job CSV, normalizing messy columns."""
    city_resp = requests.get(CITY_URL, timeout=15)
    city_resp.raise_for_status()
    city_df = pd.read_csv(StringIO(city_resp.text), dtype=str)
    city_df.columns = city_df.columns.str.lower().str.strip().str.replace(" ", "_")
    city_df["zips"] = city_df["zips"].fillna("")
    city_df["city_norm"] = city_df["city"].str.lower().str.strip()
    city_df["state_norm"] = city_df.get("state_id", city_df.get("state_name", "")).str.upper().str.strip()

    zip_coords: Dict[str, Dict[str, Any]] = {}
    city_state_coords: Dict[tuple, tuple] = {}
    city_only_coords: Dict[str, tuple] = {}

    for _, r in city_df.iterrows():
        lat, lng = float(r["lat"]), float(r["lng"])
        c_norm = r["city_norm"]
        st_id = str(r.get("state_id", "")).upper().strip()
        st_name = str(r.get("state_name", "")).strip()

        if st_id and (c_norm, st_id) not in city_state_coords:
            city_state_coords[(c_norm, st_id)] = (lat, lng)
        if c_norm not in city_only_coords:
            city_only_coords[c_norm] = (lat, lng)

        for z in r["zips"].split():
            zip_coords[z] = {"coords": (lat, lng), "city": r["city"].title(), "state": st_id or st_name}

    # Load jobs from custom URL -> local file -> GitHub fallback
    jobs_df = None
    source_used = ""
    if custom_job_csv_url.strip():
        resp = requests.get(custom_job_csv_url.strip(), timeout=15)
        resp.raise_for_status()
        jobs_df = pd.read_csv(StringIO(resp.text), dtype=str)
        source_used = "Live Sheet URL"
    elif os.path.exists(LOCAL_JOB_FILE):
        jobs_df = pd.read_csv(LOCAL_JOB_FILE, dtype=str)
        source_used = f"Local File ({LOCAL_JOB_FILE})"
    else:
        resp = requests.get(JOB_URL, timeout=15)
        resp.raise_for_status()
        jobs_df = pd.read_csv(StringIO(resp.text), dtype=str)
        source_used = "GitHub Live CSV"

    jobs_df = jobs_df.fillna("")
    jobs_df.columns = jobs_df.columns.str.lower().str.strip().str.replace(" ", "_")

    # Resolve coordinates: 1) Valid 5-digit ZIP -> 2) City + State -> 3) City alias -> 4) City only
    def resolve_job_geo(row):
        zip_raw = str(row.get("zip_code", "")).strip()
        notes_raw = str(row.get("order_notes", "")).strip()
        for field in (zip_raw, notes_raw):
            m = ZIP_RE.search(field)
            if m and m.group(1) in zip_coords:
                return zip_coords[m.group(1)]["coords"], f"ZIP ({m.group(1)})"

        city_raw = str(row.get("client_city", "")).strip().lower()
        state_raw = str(row.get("state", "")).strip().upper()

        if city_raw in CITY_ALIASES:
            alias_city, alias_state = CITY_ALIASES[city_raw]
            if (alias_city, alias_state) in city_state_coords:
                return city_state_coords[(alias_city, alias_state)], f"City Alias ({alias_city.title()}, {alias_state})"

        if (city_raw, state_raw) in city_state_coords:
            return city_state_coords[(city_raw, state_raw)], f"City+State ({city_raw.title()}, {state_raw})"

        if city_raw in city_only_coords:
            return city_only_coords[city_raw], f"City Fallback ({city_raw.title()})"

        return None, "Unresolved"

    geo_results = jobs_df.apply(resolve_job_geo, axis=1)
    jobs_df["coords"] = [g[0] for g in geo_results]
    jobs_df["geo_source"] = [g[1] for g in geo_results]
    jobs_df["detected_roles"] = jobs_df.apply(
        lambda r: extract_job_roles(r.get("order_notes", ""), r.get("pay_rate", ""), r.get("client_name", "")),
        axis=1,
    )
    jobs_df["lang_rule"] = jobs_df["language"].apply(normalize_language_requirement)

    return city_df, zip_coords, city_state_coords, city_only_coords, jobs_df, source_used


# ==============================================================================
# 4. PYDANTIC SCHEMAS & LANGGRAPH STATE MACHINE
# ==============================================================================
class CandidateCallIntent(BaseModel):
    location_query: str = Field(description="5-digit US ZIP code or US City name")
    radius_miles: int = Field(default=50, description="Search radius in miles")
    spoken_languages: List[str] = Field(
        default_factory=lambda: ["English"],
        description="Languages the candidate speaks fluently (English is always included if candidate speaks English)",
    )
    preferred_roles: List[str] = Field(
        default_factory=list,
        description="Normalized roles from: CAREGIVER, HHA, CNA, STNA, PCA",
    )
    candidate_skills: List[str] = Field(
        default_factory=list,
        description="Clinical/caregiving skills e.g. Hoyer lift, dementia, weekend, reliable vehicle",
    )


class JobFitEvaluation(BaseModel):
    job_idx: int
    fit_score: int = Field(ge=0, le=100)
    matched_highlights: List[str]
    gaps_or_warnings: List[str]
    recruiter_call_pitch: str = Field(description="Concise 2-sentence live phone pitch for this job")


class BatchFitOutput(BaseModel):
    evaluations: List[JobFitEvaluation]


class CopilotGraphState(TypedDict):
    call_notes: str
    resume_text: str
    manual_location: str
    manual_radius: int
    manual_languages: List[str]
    manual_roles: List[str]
    resolved_location: str
    resolved_radius: int
    resolved_languages: List[str]
    resolved_roles: List[str]
    candidate_skills: List[str]
    nearby_jobs: List[Dict[str, Any]]
    ranked_jobs: List[Dict[str, Any]]
    radius_expanded: bool
    trace_log: List[str]


def get_llm():
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if ChatGoogleGenerativeAI and api_key:
        return ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=api_key, temperature=0.1)
    return None


def parse_pdf_resume(uploaded_file) -> str:
    if not uploaded_file:
        return ""
    if fitz is None:
        return uploaded_file.read().decode("utf-8", errors="ignore")
    doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def build_recruiter_graph(zip_coords, city_state_coords, city_only_coords, valid_jobs_records):
    llm = get_llm()

    def node_extract_intent(state: CopilotGraphState) -> Dict[str, Any]:
        loc = state["manual_location"].strip()
        rad = state["manual_radius"]
        langs = list(state["manual_languages"])
        roles = list(state["manual_roles"])
        skills: List[str] = []
        logs = list(state["trace_log"])

        # If live call notes or resume are provided and Gemini API key is active, extract structured intent
        if llm and (state["call_notes"].strip() or state["resume_text"].strip()):
            try:
                extractor = llm.with_structured_output(CandidateCallIntent)
                parsed: CandidateCallIntent = extractor.invoke(
                    f"Extract candidate job search details.\nCall Notes: {state['call_notes']}\nResume: {state['resume_text'][:2500]}"
                )
                if not loc and parsed.location_query:
                    loc = parsed.location_query
                if parsed.spoken_languages:
                    langs = list(dict.fromkeys(["English"] + parsed.spoken_languages))
                if not roles and parsed.preferred_roles:
                    roles = [r.upper() for r in parsed.preferred_roles]
                skills = parsed.candidate_skills
                logs.append(f"LLM Intent Parsed: loc={loc}, langs={langs}, roles={roles or 'ALL'}, skills={skills}")
            except Exception as exc:
                logs.append(f"LLM extraction skipped ({exc}); using direct filters.")
        else:
            # Deterministic fallback keyword extraction from call notes/resume
            combined_text = f"{state['call_notes']} {state['resume_text']}"
            if not loc:
                m = ZIP_RE.search(combined_text)
                if m:
                    loc = m.group(1)
            for lang_name in ["Spanish", "Cantonese", "Mandarin", "Korean", "Lao", "Hmong"]:
                if lang_name.lower() in combined_text.lower() and lang_name not in langs:
                    langs.append(lang_name)
            if not roles:
                found_r = [m.upper() for m in ROLE_RE.findall(combined_text)]
                if found_r:
                    roles = list(dict.fromkeys(found_r))
            for kw in ["hoyer lift", "dementia", "weekend", "vehicle", "driver", "female", "male"]:
                if kw in combined_text.lower():
                    skills.append(kw.title())
            logs.append(f"Rule Intent Parsed: loc={loc}, langs={langs}, roles={roles or 'ALL'}")

        if langs and "English" not in langs:
            langs.insert(0, "English")

        return {
            "resolved_location": loc,
            "resolved_radius": rad,
            "resolved_languages": langs,
            "resolved_roles": roles,
            "candidate_skills": skills,
            "trace_log": logs,
        }

    def node_geospatial_and_rule_filter(state: CopilotGraphState) -> Dict[str, Any]:
        loc = state["resolved_location"].strip()
        rad = state["resolved_radius"]
        langs = state["resolved_languages"]
        roles = [r.upper() for r in state["resolved_roles"]]
        logs = list(state["trace_log"])

        user_coords = None
        m = ZIP_RE.search(loc)
        if m and m.group(1) in zip_coords:
            user_coords = zip_coords[m.group(1)]["coords"]
        else:
            low_loc = loc.lower()
            if "," in low_loc:
                parts = [p.strip() for p in low_loc.split(",", 1)]
                user_coords = city_state_coords.get((parts[0], parts[1].upper()))
            if not user_coords:
                user_coords = city_only_coords.get(low_loc)

        if not user_coords:
            logs.append(f"Could not geocode '{loc}'. Enter a valid 5-digit US ZIP or City.")
            return {"nearby_jobs": [], "trace_log": logs}

        matched = []
        for job in valid_jobs_records:
            dist = geodesic(user_coords, job["coords"]).miles
            if dist > rad:
                continue
            if roles and not any(r in job["detected_roles"] for r in roles):
                continue
            if not candidate_matches_language(langs, job["lang_rule"]):
                continue

            j_copy = dict(job)
            j_copy["distance_miles"] = round(dist, 1)
            matched.append(j_copy)

        matched.sort(key=lambda x: x["distance_miles"])
        logs.append(f"Filtered to {len(matched)} jobs within {rad} miles matching role & language rules.")
        return {"nearby_jobs": matched[:20], "trace_log": logs}

    def route_after_filter(state: CopilotGraphState) -> str:
        if len(state["nearby_jobs"]) == 0 and not state.get("radius_expanded", False) and state["resolved_location"]:
            return "expand_radius"
        if len(state["nearby_jobs"]) == 0:
            return END
        return "score_and_rank"

    def node_expand_radius(state: CopilotGraphState) -> Dict[str, Any]:
        new_rad = min(500, int(state["resolved_radius"] * 1.5))
        logs = list(state["trace_log"]) + [f"0 jobs at {state['resolved_radius']} mi -> auto-expanding radius to {new_rad} mi."]
        return {"resolved_radius": new_rad, "radius_expanded": True, "trace_log": logs}

    def node_score_and_rank(state: CopilotGraphState) -> Dict[str, Any]:
        jobs = state["nearby_jobs"]
        logs = list(state["trace_log"])

        if llm and (state["call_notes"].strip() or state["resume_text"].strip()):
            try:
                scorer = llm.with_structured_output(BatchFitOutput)
                compact_jobs = [
                    {
                        "job_idx": i,
                        "client": j.get("client_name", ""),
                        "city": f"{j.get('client_city', '')}, {j.get('state', '')}",
                        "miles": j["distance_miles"],
                        "roles": j["detected_roles"],
                        "pay": j.get("pay_rate", ""),
                        "language_rule": j["lang_rule"]["rule_summary"],
                        "gender": j.get("gender", ""),
                        "notes": j.get("order_notes", "")[:350],
                    }
                    for i, j in enumerate(jobs)
                ]
                out: BatchFitOutput = scorer.invoke(
                    f"Score each job 0-100 for this candidate and draft a 2-sentence live phone pitch.\n"
                    f"Call Notes: {state['call_notes']}\nResume: {state['resume_text'][:2500]}\nJobs: {compact_jobs}"
                )
                score_map = {e.job_idx: e.model_dump() for e in out.evaluations}
                ranked = []
                for i, j in enumerate(jobs):
                    ev = score_map.get(
                        i,
                        {
                            "fit_score": 70,
                            "matched_highlights": ["Location & language verified"],
                            "gaps_or_warnings": [],
                            "recruiter_call_pitch": f"We have an opening with {j['client_name']} in {j['client_city']} ({j['distance_miles']} mi away) paying {j['pay_rate']}.",
                        },
                    )
                    ranked.append({**j, **ev})
                ranked.sort(key=lambda x: (-x["fit_score"], x["distance_miles"]))
                logs.append(f"LLM scored & ranked {len(ranked)} jobs.")
                return {"ranked_jobs": ranked, "trace_log": logs}
            except Exception as exc:
                logs.append(f"LLM scoring fallback ({exc}); using deterministic fit scoring.")

        # Deterministic JD-Resume / Skill Fit Scoring (works offline without API key)
        cand_blob = f"{state['call_notes']} {state['resume_text']} {' '.join(state['candidate_skills'])}".lower()
        ranked = []
        for j in jobs:
            score = 65
            highlights = [f"{j['distance_miles']} mi away", j["lang_rule"]["rule_summary"]]
            warnings = []

            if j["lang_rule"]["preferred"]:
                for pref in j["lang_rule"]["preferred"]:
                    if pref.lower() in cand_blob:
                        score += 10
                        highlights.append(f"Bonus {pref} match")

            for skill_kw, label in [
                ("hoyer", "Hoyer lift experience"),
                ("dementia", "Dementia care experience"),
                ("weekend", "Weekend availability"),
                ("vehicle", "Reliable vehicle"),
                ("driver", "Licensed driver"),
            ]:
                if skill_kw in j.get("order_notes", "").lower():
                    if skill_kw in cand_blob:
                        score += 8
                        highlights.append(f"Matched: {label}")
                    else:
                        warnings.append(f"Verify with candidate: {label}")

            score = max(0, min(100, score - int(j["distance_miles"] * 0.25)))
            roles_str = "/".join(j["detected_roles"])
            pitch = (
                f"Hi, we have an immediate {roles_str} opening in {j['client_city']}, {j['state']} "
                f"({j['distance_miles']} miles from you) paying {j.get('pay_rate', 'competitive hourly rates')}. "
                f"Language requirement: {j['lang_rule']['rule_summary']}."
            )
            ranked.append({
                **j,
                "fit_score": score,
                "matched_highlights": highlights,
                "gaps_or_warnings": warnings,
                "recruiter_call_pitch": pitch,
            })

        ranked.sort(key=lambda x: (-x["fit_score"], x["distance_miles"]))
        logs.append(f"Ranked {len(ranked)} matching jobs.")
        return {"ranked_jobs": ranked, "trace_log": logs}

    builder = StateGraph(CopilotGraphState)
    builder.add_node("extract_intent", node_extract_intent)
    builder.add_node("geo_filter", node_geospatial_and_rule_filter)
    builder.add_node("expand_radius", node_expand_radius)
    builder.add_node("score_and_rank", node_score_and_rank)

    builder.add_edge(START, "extract_intent")
    builder.add_edge("extract_intent", "geo_filter")
    builder.add_conditional_edges("geo_filter", route_after_filter, ["expand_radius", "score_and_rank", END])
    builder.add_edge("expand_radius", "geo_filter")
    builder.add_edge("score_and_rank", END)

    return builder.compile()


# ==============================================================================
# 5. STREAMLIT UI
# ==============================================================================
st.markdown(
    """
    <div class="hero-box">
        <div class="hero-title">🩺 Agentic Healthcare Recruiter & JD-Resume Matching Copilot</div>
        <div class="hero-sub">LangGraph Workflow • Live Sheet Sync • Free ZIP/City Geodesic Distance • Smart Role & Bilingual Normalization</div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("⚙️ Live Spreadsheet & Data Hygiene Status", expanded=False):
    custom_url = st.text_input(
        "Optional Live Google Sheet / Excel CSV Export URL (leave blank to use uploaded 'all job.csv'):",
        value="",
    )
    city_df, zip_coords, city_state_coords, city_only_coords, jobs_df, source_used = load_and_clean_datasets(custom_url)
    valid_jobs_df = jobs_df.dropna(subset=["coords"]).copy()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Data Source", source_used)
    c2.metric("Total Jobs Loaded", len(jobs_df))
    c3.metric("Geocoded (ZIP + City Fallback)", len(valid_jobs_df))
    c4.metric("Missing ZIP Recovered via City", int((jobs_df["zip_code"].str.strip() == "").sum()))

st.subheader("📞 Live Candidate Call & Resume Matcher")

col_left, col_right = st.columns([1.4, 1.0])
with col_left:
    call_notes = st.text_area(
        "Live Call Notes (paste what the candidate says—ZIP, city, languages, Hoyer lift, dementia, weekend, vehicle):",
        placeholder="Example: Candidate near 90012 or Alhambra, speaks English and Spanish, CNA with Hoyer lift and reliable vehicle, available weekends.",
        height=105,
    )
    resume_file = st.file_uploader("Optional Candidate Resume (PDF or TXT) for JD-Resume Fit Scoring", type=["pdf", "txt"])

with col_right:
    manual_location = st.text_input("ZIP Code or City (e.g., 90012, 43017, Charleston, Alhambra):", value="90012")
    manual_radius = st.slider("Max Commute Radius (miles)", 5, 250, 35, step=5)
    manual_roles = st.multiselect(
        "Role Filter (defaults to CAREGIVER when Order Notes omit titles):",
        options=["CAREGIVER", "HHA", "CNA", "STNA", "PCA"],
        default=[],
    )
    manual_languages = st.multiselect(
        "Candidate Spoken Languages (English is mandatory for all roles):",
        options=["English", "Spanish", "Cantonese", "Mandarin", "Korean", "Lao", "Hmong"],
        default=["English"],
    )

if st.button("🚀 Run LangGraph Matching Copilot", type="primary", use_container_width=True):
    resume_txt = parse_pdf_resume(resume_file)
    graph = build_recruiter_graph(
        zip_coords,
        city_state_coords,
        city_only_coords,
        valid_jobs_df.to_dict(orient="records"),
    )

    initial_state: CopilotGraphState = {
        "call_notes": call_notes,
        "resume_text": resume_txt,
        "manual_location": manual_location,
        "manual_radius": manual_radius,
        "manual_languages": manual_languages,
        "manual_roles": manual_roles,
        "resolved_location": "",
        "resolved_radius": manual_radius,
        "resolved_languages": manual_languages,
        "resolved_roles": manual_roles,
        "candidate_skills": [],
        "nearby_jobs": [],
        "ranked_jobs": [],
        "radius_expanded": False,
        "trace_log": [],
    }

    final_state = graph.invoke(initial_state)

    with st.expander("🧭 LangGraph Execution Trace", expanded=False):
        for step in final_state["trace_log"]:
            st.write(f"• {step}")

    results = final_state["ranked_jobs"]
    if not results:
        st.warning("No matching jobs found for that location, role, and language combination.")
    else:
        st.success(f"Found {len(results)} ranked match(es) within {final_state['resolved_radius']} miles.")
        for idx, job in enumerate(results, start=1):
            roles_badges = "".join([f"<span class='pill pill-role'>{r}</span>" for r in job["detected_roles"]])
            lang_badge = f"<span class='pill pill-lang'>{job['lang_rule']['rule_summary']}</span>"
            score_badge = f"<span class='pill pill-score'>Fit Score: {job['fit_score']}/100</span>"

            with st.expander(
                f"#{idx} | 🏥 {job['client_name']} — {job['client_city']}, {job['state']} ({job['distance_miles']} mi) | Fit: {job['fit_score']}/100",
                expanded=(idx <= 3),
            ):
                st.markdown(f"{score_badge}{roles_badges}{lang_badge}", unsafe_allow_html=True)
                st.markdown(
                    f"**📍 Location:** {job['client_city']}, {job['state']} ({job['geo_source']}) &nbsp;|&nbsp; "
                    f"**💰 Pay Rate:** {job.get('pay_rate', 'N/A')} &nbsp;|&nbsp; "
                    f"**👤 Gender:** {job.get('gender') or 'Any'}"
                )
                st.markdown(f"**📝 Order Notes:** {job.get('order_notes', '')}")
                if job["matched_highlights"]:
                    st.markdown("**✅ Matched:** " + " • ".join(job["matched_highlights"]))
                if job["gaps_or_warnings"]:
                    st.markdown("**⚠️ Check on Call:** " + " • ".join(job["gaps_or_warnings"]))
                st.markdown(f"<div class='pitch-box'><b>🎙️ Live Call Pitch:</b> {job['recruiter_call_pitch']}</div>", unsafe_allow_html=True)
