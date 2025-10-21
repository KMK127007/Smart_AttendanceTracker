import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date
from transformers import pipeline
import random
import string
import requests
import json
import time
import os
from pathlib import Path
from typing import Tuple

# ------------------------------
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
# ------------------------------
try:
    HUGGINGFACE_API_KEY = st.secrets["HUGGINGFACE_API_KEY"]
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as e:
    st.error(f"Configuration error: Missing secret key '{e}'. Please ensure your secrets.toml has 'HUGGINGFACE_API_KEY' and 'admin_user.username', 'admin_user.password'.")
    st.stop()

# ------------------------------
# AI Model Configuration
# ------------------------------

# Original defaults preserved for remote HF usage;
DEFAULT_HF_INSTRUCTION_MODEL = "google/flan-t5-large"
DEFAULT_HF_SUMMARIZATION_MODEL = "sshleifer/distilbart-cnn-12-6"

# Lightweight local fallback models (safe to load on modest machines)
LOCAL_INSTRUCTION_FALLBACK = "google/flan-t5-large"
LOCAL_SUMMARIZATION_FALLBACK = "sshleifer/distilbart-cnn-12-6"

local_instruction_pipe = None
local_summarization_pipe = None

# --- Load Local Models Safely ---
try:
    # Try loading small local models only — avoids OOM on typical dev machines.
    local_instruction_pipe = pipeline("text2text-generation", model=LOCAL_INSTRUCTION_FALLBACK)
except Exception as e:
    local_instruction_pipe = None
    st.warning(f"Local instruction pipeline not available: {e}")

try:
    local_summarization_pipe = pipeline("summarization", model=LOCAL_SUMMARIZATION_FALLBACK)
except Exception as e:
    local_summarization_pipe = None
    st.warning(f"Local summarization pipeline not available: {e}")

# --- Local Fallback Functions ---
def local_fallback_instruction(prompt, max_tokens=200):
    if local_instruction_pipe:
        try:
            result = local_instruction_pipe(prompt, max_new_tokens=max_tokens, do_sample=True, temperature=0.5)
            if isinstance(result, list) and result and ("generated_text" in result[0] or "text" in result[0]):
                return result[0].get("generated_text") or result[0].get("text")
            return str(result)
        except Exception as e:
            return f"Local AI generation failed: {e}"
    return "Local instruction pipeline unavailable."

def local_fallback_summary(text, max_tokens=200):
    if local_summarization_pipe:
        try:
            result = local_summarization_pipe(text, max_length=max_tokens)
            if isinstance(result, list) and result and "summary_text" in result[0]:
                return result[0]["summary_text"]
            if isinstance(result, list) and result and "generated_text" in result[0]:
                return result[0]["generated_text"]
            return str(result)
        except Exception as e:
            return f"Local summarization failed: {e}"
    return "Local summarization pipeline unavailable."

# ------------------------------
# Robust Hugging Face Query Function (with Fallback)
# ------------------------------
@st.cache_data(ttl=60*60)  # cache AI responses for 1 hour
def query_huggingface_model_cached(prompt: str, max_tokens: int = 200, model_id: str = DEFAULT_HF_INSTRUCTION_MODEL) -> str:
    """Cached wrapper for the main query function."""
    return query_huggingface_model(prompt, max_tokens=max_tokens, model_id=model_id)

def query_huggingface_model(prompt, max_tokens=200, model_id=DEFAULT_HF_INSTRUCTION_MODEL, retries=2, delay=2):
    """
    Safe HF inference call — short timeout, retries, and local fallback.
    THIS IS THE ONLY FUNCTION THAT SHOULD BE CALLED FOR AI.
    """
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": max_tokens,  # CORRECT PARAMETER
            "temperature": 0.5,
            "do_sample": True
        }
    }

    for attempt in range(retries):
        try:
            response = requests.post(
                f"https://api-inference.huggingface.co/models/{model_id}",
                headers=headers,
                data=json.dumps(payload),
                timeout=30  # short timeout to avoid Streamlit freezes
            )
            response.raise_for_status() # Will raise for 400/404 errors
            result = response.json()

            # Best-effort parsing of common outputs
            if isinstance(result, list) and result:
                item = result[0]
                if isinstance(item, dict):
                    if "generated_text" in item:
                        return item["generated_text"].replace(prompt, "").strip()
                    if "summary_text" in item:
                        return item["summary_text"].strip()
                    if "text" in item:
                        return item["text"].strip()
                return str(item)

            if isinstance(result, dict) and "error" in result:
                st.warning(f"Model {model_id} error: {result['error']}")
                break # Don't retry if model returns a specific error

            return str(result)

        except requests.exceptions.Timeout:
            st.warning(f"Timeout from {model_id}, attempt {attempt+1}/{retries}")
            if attempt < retries - 1:
                time.sleep(delay)
            continue

        except requests.exceptions.HTTPError as e:
            # This catches 400 and 404 errors
            st.warning(f"Hugging Face HTTP error: {e}")
            break

        except Exception as e:
            st.error(f"Error with {model_id}: {e}")
            break

    # Local fallback if all remote attempts fail
    st.info(f"Remote AI failed, attempting local fallback for: {model_id}")
    if model_id == DEFAULT_HF_INSTRUCTION_MODEL:
        return local_fallback_instruction(prompt, max_tokens)
    elif model_id == DEFAULT_HF_SUMMARIZATION_MODEL:
        return local_fallback_summary(prompt, max_tokens)

    return "AI generation failed after retries (no fallback available)."

# ------------------------------
# Robust CSS loading
# ------------------------------
def local_css(file_name="style.css"):
    try:
        base = Path(__file__).parent
    except Exception:
        base = Path.cwd()
    css_file_path = base / file_name
    try:
        if css_file_path.exists():
            with open(css_file_path, encoding="utf-8") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
        else:
            # do not error out if missing; use default styling
            st.info("No custom CSS loaded; using default Streamlit styling.")
    except Exception as e:
        st.warning(f"An error occurred while loading CSS: {e}")

# ------------------------------
# Session State & Config
# ------------------------------
for key, default in {
    "admin_logged": False,
    "student_logged_in_username": None,
    "student_access_code": None,
    "otp_store": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# --- Filenames & OTP ---
STUDENTS_CSV = "students.csv"
ATTENDANCE_CSV = "attendance.csv"
LOG_CSV = "activity_log.csv"
OTP_VALIDITY_MINUTES = 5

# ------------------------------
# CSV Data Handling (with Schema)
# ------------------------------
def ensure_students_schema(df: pd.DataFrame) -> pd.DataFrame:
    expected = ["username", "password", "college", "level", "remarks", "device_id"]
    for col in expected:
        if col not in df.columns:
            if col == "remarks":
                df[col] = ""
            elif col == "password":
                df[col] = "default123"
            elif col == "device_id":
                df[col] = None # Use None/NaN for empty device ID
            else:
                df[col] = ""
    return df[expected]

def load_students():
    try:
        df = pd.read_csv(STUDENTS_CSV)
        df = ensure_students_schema(df)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=["username", "password", "college", "level", "remarks", "device_id"])
        df.to_csv(STUDENTS_CSV, index=False)
        return df
    except Exception as e:
        st.error(f"Students CSV read error: {e}. Recreating students file.")
        df = pd.DataFrame(columns=["username", "password", "college", "level", "remarks", "device_id"])
        df.to_csv(STUDENTS_CSV, index=False)
        return df

def save_students(df):
    df.to_csv(STUDENTS_CSV, index=False)

def ensure_attendance_schema(df: pd.DataFrame) -> pd.DataFrame:
    expected = ["date", "username", "college", "level", "timestamp"]
    for col in expected:
        if col not in df.columns:
            df[col] = ""
    return df[expected]

def load_attendance():
    try:
        df = pd.read_csv(ATTENDANCE_CSV)
        df = ensure_attendance_schema(df)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=["date", "username", "college", "level", "timestamp"])
        df.to_csv(ATTENDANCE_CSV, index=False)
        return df
    except Exception as e:
        st.error(f"Attendance CSV read error: {e}. Recreating attendance file.")
        df = pd.DataFrame(columns=["date", "username", "college", "level", "timestamp"])
        df.to_csv(ATTENDANCE_CSV, index=False)
        return df

def save_attendance(df):
    df.to_csv(ATTENDANCE_CSV, index=False)

def log_action(action: str, details: str = ""):
    now = datetime.now().isoformat()
    row = {"timestamp": now, "action": action, "details": details}
    try:
        if Path(LOG_CSV).exists():
            log_df = pd.read_csv(LOG_CSV)
            log_df = pd.concat([log_df, pd.DataFrame([row])], ignore_index=True)
        else:
            log_df = pd.DataFrame([row])
        log_df.to_csv(LOG_CSV, index=False)
    except Exception as e:
        st.warning(f"Could not write log: {e}")

# ------------------------------
# OTP & Attendance Logic
# ------------------------------
def generate_student_access_code():
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    st.session_state.student_access_code = code
    log_action("generate_access_code", code)
    return code

def send_otp(username):
    otp = "".join(random.choices(string.digits, k=6))
    expiry = datetime.now() + timedelta(minutes=OTP_VALIDITY_MINUTES)
    st.session_state.otp_store[username] = (otp, expiry)
    log_action("send_otp", username)
    return otp

def verify_otp(username, input_otp):
    record = st.session_state.otp_store.get(username)
    if not record:
        return False, "No OTP sent for this user or session expired. Please request a new OTP."
    otp, expiry = record
    if datetime.now() > expiry:
        del st.session_state.otp_store[username]
        return False, "OTP expired. Please request a new OTP."
    if input_otp == otp:
        del st.session_state.otp_store[username]
        log_action("verify_otp_success", username)
        return True, "OTP verified successfully ✅"
    log_action("verify_otp_fail", f"{username}:{input_otp}")
    return False, "Incorrect OTP ❌"

def has_marked_attendance_today(username):
    attendance_df = load_attendance()
    today_date_str = date.today().isoformat()
    return not attendance_df[(attendance_df['username'] == username) & (attendance_df['date'] == today_date_str)].empty

def mark_attendance(username, college, level):
    """Marks attendance based on OTP flow (no device ID)."""
    students_df = load_students()
    
    if username not in students_df["username"].values:
        return False, "Username not found. Please contact admin to add your account."

    if has_marked_attendance_today(username):
        return False, "Attendance already marked today for this student."

    df = load_attendance()
    new_entry = {
        "date": date.today().isoformat(),
        "username": username,
        "college": college,
        "level": level,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }
    df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
    save_attendance(df)
    log_action("mark_attendance_success", username)
    return True, "Attendance marked successfully ✅"

# ------------------------------
# AI Analytics & Reports (REFACTORED)
# ------------------------------
@st.cache_data(ttl=60*30)
def generate_analytics_summary_cached():
    return generate_analytics_summary()

def generate_analytics_summary():
    attendance_df = load_attendance()
    if attendance_df.empty:
        return "No attendance data available to generate a summary."

    college_attendance = attendance_df.groupby('college').size().reset_index(name='total_attendance')
    college_summary = college_attendance.to_string(index=False)
    level_attendance = attendance_df.groupby(['level', 'date']).size().reset_index(name='count')
    level_pivot = level_attendance.pivot_table(index='date', columns='level', values='count').fillna(0)
    
    full_prompt = f"""
    Analyze the following attendance data and provide a concise summary highlighting key insights:
    1. Which colleges have the highest/lowest attendance.
    2. Noticeable trends in L1/L2 group attendance.
    3. Any other significant patterns.

    Attendance by College:
    {college_summary}
    Attendance Trends by Level (daily counts):
    {level_pivot.to_string()}
    Summary:
    """

    st.info("Generating AI analytics summary, please wait...")

    # This single call tries remote, then falls back to local.
    summary = query_huggingface_model_cached(full_prompt, model_id=DEFAULT_HF_SUMMARIZATION_MODEL, max_tokens=300)

    # Simplified check
    if not summary or "failed" in summary.lower() or "unavailable" in summary.lower() or summary.strip().lower().startswith("analyze the following attendance data"):
        return "The AI could not generate a meaningful summary based on the current data. Please ensure there is sufficient attendance data and try again."

    return summary

@st.cache_data(ttl=60*30)
def generate_student_ai_report_cached(student_username: str):
    return generate_student_ai_report(student_username)

def generate_student_ai_report(student_username):
    students_df = load_students()
    attendance_df = load_attendance()

    student_data = students_df[students_df['username'] == student_username]
    if student_data.empty:
        return "Student not found."

    student_remarks = student_data['remarks'].iloc[0] if 'remarks' in student_data.columns else ""
    student_attendance = attendance_df[attendance_df['username'] == student_username]
    total_days_attended = len(student_attendance)

    total_possible_days_in_dataset = len(attendance_df['date'].unique()) if not attendance_df.empty else 1
    attendance_percentage = (total_days_attended / total_possible_days_in_dataset) * 100 if total_possible_days_in_dataset > 0 else 0
    l1_count = student_attendance[student_attendance['level'] == 'L1'].shape[0]
    l2_count = student_attendance[student_attendance['level'] == 'L2'].shape[0]

    prompt = f"""
    Generate a personalized, constructive student report for {student_username}.
    Student Username: {student_username}
    Total Attendance: {total_days_attended} days attended out of {total_possible_days_in_dataset} total days ({attendance_percentage:.2f}%).
    L1 Attendance: {l1_count} days.
    L2 Attendance: {l2_count} days.
    Admin Remarks: "{student_remarks}"
    Personalized Student Report for {student_username}:
    """
    st.info(f"Generating AI report for {student_username}, please wait...")

    # This single call tries remote, then falls back to local.
    report = query_huggingface_model_cached(prompt, max_tokens=300, model_id=DEFAULT_HF_INSTRUCTION_MODEL)
    
    if not report or "failed" in report.lower() or "unavailable" in report.lower():
         return f"AI report generation failed for {student_username}."
    return report

def summarize_student_remark_for_student(admin_remark):
    if not admin_remark.strip():
        return "No specific remarks from the admin at this time."

    prompt = f"""
    The admin has made the following remark about your performance/behavior:
    "{admin_remark}"
    Rephrase this remark into a clear, concise, and constructive summary for a student.
    Start directly with the summary.
    """
    st.info("Generating AI summary of admin remarks...")
    
    # This single call tries remote, then falls back to local.
    summary = query_huggingface_model_cached(prompt, max_tokens=100, model_id=DEFAULT_HF_INSTRUCTION_MODEL)
    
    if not summary or "failed" in summary.lower() or "unavailable" in summary.lower():
        return "AI remark summarization failed."
    return summary

# ------------------------------
# Admin Auth & Panel
# ------------------------------
def admin_login():
    st.sidebar.header("🔐 Admin Login")
    username = st.sidebar.text_input("Username", key="admin_username_input")
    password = st.sidebar.text_input("Password", type="password", key="admin_password_input")
    if st.sidebar.button("Login as Admin"):
        if username in ADMINS and ADMINS[username]["password"] == password:
            st.session_state.admin_logged = True
            st.session_state.admin_user = username
            st.sidebar.success(f"Welcome, {username}")
            log_action("admin_login", username)
            st.rerun()
        else:
            st.sidebar.error("Invalid admin credentials ❌")
            log_action("admin_login_failed", username)

def admin_logout():
    if st.sidebar.button("🚪 Logout Admin"):
        st.session_state.admin_logged = False
        st.session_state.admin_user = None
        log_action("admin_logout", "")
        st.rerun()

def paginate_df(df: pd.DataFrame, page:int, page_size:int) -> Tuple[pd.DataFrame, int]:
    total = len(df)
    last_page = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, last_page))
    start = (page - 1) * page_size
    end = start + page_size
    return df.iloc[start:end], last_page

def admin_panel():
    st.markdown('<div class="header">🛠️ Admin Panel</div>', unsafe_allow_html=True)
    st.write(f"Logged in as: **{st.session_state.admin_user}**")

    st.markdown('<div class="subheader">🎟️ Student Access Code</div>', unsafe_allow_html=True)
    access_code_display = st.empty()
    if st.session_state.student_access_code:
        access_code_display.info(f"Current Access Code: **{st.session_state.student_access_code}** (Expires with app restart)")
    else:
        access_code_display.warning("No access code generated yet for students.")

    if st.button("Generate New Access Code"):
        code = generate_student_access_code()
        access_code_display.success(f"New Access Code: **{code}** (Expires with app restart)")

    tabs = st.tabs(["➕ Manage Students", "📊 View Attendance", "🧠 AI Analytics Summary", "📄 Student AI Reports", "📋 Logs"])

    with tabs[0]:
        df = load_students()
        st.markdown('<div class="subheader">Add New Student</div>', unsafe_allow_html=True)
        new_username = st.text_input("Username", key="new_student_username")
        new_college = st.text_input("College", key="new_student_college")
        new_level = st.selectbox("Level", ["L1", "L2"], key="new_student_level")

        if st.button("Add Student", key="add_student_button"):
            if new_username and new_college:
                if new_username.lower() in df["username"].str.lower().values:
                    st.warning(f"Username '{new_username}' already exists. Please choose a different one.")
                else:
                    new_student = {
                        "username": new_username, "password": "default123",
                        "college": new_college, "level": new_level,
                        "remarks": "", "device_id": None
                    }
                    df = pd.concat([df, pd.DataFrame([new_student])], ignore_index=True)
                    save_students(df)
                    st.success(f"Student '{new_username}' added successfully.")
                    log_action("add_student", new_username)
                    st.rerun()
            else:
                st.warning("Please fill all fields to add a student.")

        st.markdown('<div class="subheader">Manage Existing Students (Add Remarks / Reset Device)</div>', unsafe_allow_html=True)
        if not df.empty:
            search = st.text_input("Search username/college", key="admin_search_students")
            page_size = st.selectbox("Rows per page", [10, 25, 50], index=0, key="admin_page_size")
            page = st.number_input("Page", value=1, min_value=1, step=1, key="admin_page_number")

            filtered = df.copy()
            if search:
                mask = filtered["username"].str.contains(search, case=False, na=False) | filtered["college"].str.contains(search, case=False, na=False)
                filtered = filtered[mask]

            page_df, last_page = paginate_df(filtered.reset_index(drop=True), int(page), int(page_size))
            st.caption(f"Showing page {min(int(page), last_page)} of {last_page} (total {len(filtered)} records)")

            st.dataframe(page_df.drop(columns=["password"]), use_container_width=True)

            selected_student_for_remarks = st.selectbox("Select Student to Add Remarks or Reset Device", [""] + sorted(df["username"].tolist()), key="select_student_remark")
            if selected_student_for_remarks:
                current_remarks = df[df['username'] == selected_student_for_remarks]['remarks'].iloc[0]
                new_remark = st.text_area(f"Add/Edit Remarks for {selected_student_for_remarks}", value=current_remarks, key="admin_student_remark_input")
                if st.button(f"Save Remarks for {selected_student_for_remarks}", key="save_student_remark_button"):
                    df.loc[df['username'] == selected_student_for_remarks, 'remarks'] = new_remark
                    save_students(df)
                    st.success(f"Remarks saved for {selected_student_for_remarks}")
                    log_action("save_remark", selected_student_for_remarks)
                    st.rerun()

                if st.button(f"Reset Device for {selected_student_for_remarks}", key="reset_device_button", help="This resets the device binding, which is used for non-OTP attendance methods."):
                    df.loc[df['username'] == selected_student_for_remarks, 'device_id'] = None
                    save_students(df)
                    st.success(f"Device binding reset for {selected_student_for_remarks}.")
                    log_action("reset_device", selected_student_for_remarks)
                    st.rerun()
        else:
            st.info("No students added yet.")

    with tabs[1]:
        attendance_df = load_attendance()
        st.markdown('<div class="subheader">All Attendance Records</div>', unsafe_allow_html=True)
        if attendance_df.empty:
            st.info("No attendance yet.")
        else:
            unique_dates = sorted(attendance_df['date'].unique(), reverse=True)
            filter_date = st.selectbox("Filter by Date", ["All"] + unique_dates, key="filter_attendance_date")
            filtered_attendance_df = attendance_df.copy()
            if filter_date != "All":
                filtered_attendance_df = filtered_attendance_df[filtered_attendance_df['date'] == filter_date]

            unique_colleges = sorted(attendance_df['college'].unique())
            filter_college = st.selectbox("Filter by College", ["All"] + unique_colleges, key="filter_attendance_college")
            if filter_college != "All":
                filtered_attendance_df = filtered_attendance_df[filtered_attendance_df['college'] == filter_college]

            unique_levels = sorted(attendance_df['level'].unique())
            filter_level = st.selectbox("Filter by Level", ["All"] + unique_levels, key="filter_attendance_level")
            if filter_level != "All":
                filtered_attendance_df = filtered_attendance_df[filtered_attendance_df['level'] == filter_level]

            page_size = st.selectbox("Rows per page (attendance)", [10, 25, 50], index=0, key="attendance_page_size")
            page = st.number_input("Attendance page", value=1, min_value=1, step=1, key="attendance_page_number")
            pg_df, last_page = paginate_df(filtered_attendance_df.reset_index(drop=True), int(page), int(page_size))
            st.caption(f"Showing page {min(int(page), last_page)} of {last_page} (total {len(filtered_attendance_df)} records)")
            st.dataframe(pg_df, use_container_width=True)

            st.markdown("### Attendance by College")
            try:
                college_counts = attendance_df.groupby('college').size()
                st.bar_chart(college_counts)
            except Exception as e:
                st.warning(f"Could not render college chart: {e}")

            st.markdown("### Attendance by Level Over Time")
            try:
                level_attendance = attendance_df.groupby(['date', 'level']).size().unstack(fill_value=0)
                st.line_chart(level_attendance)
            except Exception as e:
                st.warning(f"Could not render level trend chart: {e}")

    with tabs[2]:
        st.markdown('<div class="subheader">🧠 AI-Generated Analytics Summary</div>', unsafe_allow_html=True)
        st.write("Get an AI-powered summary of overall attendance trends.")
        if st.button("Generate AI Analytics Summary"):
            with st.spinner("Generating smart analytics summary... This may take a moment."):
                summary = generate_analytics_summary_cached()
                st.markdown(f"**Analytics Summary:**\n{summary}")

    with tabs[3]:
        st.markdown('<div class="subheader">📄 AI-Powered Student Report Generator</div>', unsafe_allow_html=True)
        students_df_for_report = load_students()
        if not students_df_for_report.empty:
            student_for_report = st.selectbox("Select Student for AI Report", [""] + sorted(students_df_for_report["username"].tolist()), key="select_student_report")
            if student_for_report:
                if st.button(f"Generate AI Report for {student_for_report}"):
                    with st.spinner(f"Generating personalized report for {student_for_report}..."):
                        report = generate_student_ai_report_cached(student_for_report)
                        st.markdown(f"**Personalized Report for {student_for_report}:**\n{report}")
        else:
            st.info("No students available to generate reports for.")

    with tabs[4]:
        st.markdown('<div class="subheader">📋 Activity Logs</div>', unsafe_allow_html=True)
        if Path(LOG_CSV).exists():
            log_df = pd.read_csv(LOG_CSV)
            st.dataframe(log_df.tail(200).sort_values("timestamp", ascending=False), use_container_width=True)
        else:
            st.info("No logs yet.")

# ------------------------------
# Student Dashboard & Login Flow
# ------------------------------
def student_dashboard():
    """Shows the login/marking page for a student who is *not* logged in."""
    st.markdown('<div class="header">📚 Student Attendance</div>', unsafe_allow_html=True)
    with st.container():
        st.markdown("Please enter your details and the daily access code to mark your attendance.")
        username = st.text_input("Enter Username", key="student_username_input")
        college = st.text_input("Enter College", key="student_college_input")
        level = st.selectbox("Select Level", ["L1", "L2"], key="student_level_input")

        access_code_input = st.text_input("Enter Access Code", help="Get this from your admin", key="student_access_code_input", type="password")

        is_student_details_provided = bool(username and college and level)
        is_access_code_valid = (access_code_input == st.session_state.get("student_access_code"))

        otp_attendance_container = st.empty()

        if is_student_details_provided and is_access_code_valid:
            with otp_attendance_container.container():
                st.success("Access code verified! Proceed to mark attendance.")

                col1, col2 = st.columns([1, 1])
                with col1:
                    if st.button("Send OTP", key="send_otp_button"):
                        if username in st.session_state.otp_store and datetime.now() < st.session_state.otp_store[username][1]:
                            st.info(f"An OTP was already sent to {username} and is still valid.")
                        else:
                            otp = send_otp(username)
                            st.info(f"OTP sent to: {username}. (For demo: OTP is {otp})")

                with col2:
                    otp_input = st.text_input("Enter OTP", key="otp_input", type="password")
                    if st.button("Verify OTP & Mark Attendance", key="verify_mark_attendance_button"):
                        valid_otp, otp_msg = verify_otp(username, otp_input)
                        if not valid_otp:
                            st.error(otp_msg)
                        else:
                            st.success(otp_msg)
                            success, mark_msg = mark_attendance(username, college, level)
                            if success:
                                st.success(mark_msg)
                                st.session_state.student_logged_in_username = username
                                st.rerun() # Rerun to show the logged-in panel
                            else:
                                st.warning(mark_msg)
                                if "Attendance already marked today" in mark_msg:
                                    st.session_state.student_logged_in_username = username
                                    st.rerun() # Rerun to show the logged-in panel

        elif is_student_details_provided and not access_code_input and st.session_state.get("student_access_code"):
            st.info("Please enter the daily access code to proceed.")
        elif is_student_details_provided and access_code_input and not is_access_code_valid:
            st.error("Invalid Access Code ⛔. Please get the correct code from your admin.")
        else:
            st.info("Fill in your details and the access code to proceed with attendance marking.")

        st.markdown("---")
        st.markdown('<div class="subheader">ℹ️ Your AI-Generated Remarks Summary</div>', unsafe_allow_html=True)

        if username:
            students_df = load_students()
            current_student_data = students_df[students_df['username'] == username]

            if not current_student_data.empty:
                admin_remark_for_student = current_student_data['remarks'].iloc[0]

                if st.button("View AI Summary of Admin Remarks", key="view_remarks_btn"):
                    with st.spinner("Generating summary of admin remarks..."):
                        summary = summarize_student_remark_for_student(admin_remark_for_student)
                        st.session_state[f'remarks_summary_{username}'] = summary

                if f'remarks_summary_{username}' in st.session_state:
                    st.info(f"**Admin's Feedback for {username}:**\n{st.session_state[f'remarks_summary_{username}']}")
                else:
                    st.info("Click 'View AI Summary' to see your feedback.")
            else:
                st.info(f"No student data found for '{username}'. Please ensure your username is correct and added by the admin.")
        else:
            st.info("Enter your username above to see your AI-generated remarks summary.")

def student_panel():
    """Shows the dashboard for a student who is already logged in."""
    username = st.session_state.student_logged_in_username
    st.markdown(f'<div class="header">👋 Welcome, {username}!</div>', unsafe_allow_html=True)
    
    if st.sidebar.button("🚪 Logout Student"):
        log_action("student_logout", username)
        st.session_state.student_logged_in_username = None
        st.rerun()

    st.success("You have successfully marked your attendance for today (or were already marked).")
    
    tabs = st.tabs(["📊 My Attendance History", "🧠 AI-Generated Remarks"])

    with tabs[0]:
        st.markdown('<div class="subheader">My Attendance History</div>', unsafe_allow_html=True)
        attendance_df = load_attendance()
        my_attendance = attendance_df[attendance_df["username"] == username]
        if my_attendance.empty:
            st.info("You have no attendance records yet.")
        else:
            st.dataframe(my_attendance.sort_values("date", ascending=False), use_container_width=True)

    with tabs[1]:
        st.markdown('<div class="subheader">ℹ️ Your AI-Generated Remarks Summary</div>', unsafe_allow_html=True)
        students_df = load_students()
        current_student_data = students_df[students_df['username'] == username]

        if not current_student_data.empty:
            admin_remark_for_student = current_student_data['remarks'].iloc[0]

            if st.button("View My AI Summary of Admin Remarks", key="view_remarks_btn_panel"):
                with st.spinner("Generating summary of admin remarks..."):
                    summary = summarize_student_remark_for_student(admin_remark_for_student)
                    st.session_state[f'remarks_summary_{username}'] = summary

            if f'remarks_summary_{username}' in st.session_state:
                st.info(f"**Admin's Feedback for {username}:**\n{st.session_state[f'remarks_summary_{username}']}")
            else:
                st.info("Click 'View AI Summary' to see your feedback.")
        else:
            st.error("Could not find your student data.")

# ------------------------------
# Main App Router
# ------------------------------
def get_role_from_sidebar():
    with st.sidebar:
        sel = st.radio("Open as", options=["Student", "Admin"], index=0, key="role_radio")
    return sel.lower()

def main():
    st.set_page_config(page_title="Smart Attendance", layout="wide")
    local_css() # Load CSS
    st.sidebar.title("📋 Attendance System")
    role = get_role_from_sidebar()
    
    if role == "admin":
        # --- Admin View ---
        if st.session_state.admin_logged:
            admin_logout()
            admin_panel()
        else:
            admin_login()
            st.info("Admin: please login from the sidebar to manage students & reports.")
    
    else:
        # --- Student View (with proper login flow) ---
        if st.session_state.get("student_logged_in_username"):
            # If student is already logged in, show their panel
            student_panel()
        else:
            # Otherwise, show the login/marking page
            student_dashboard()
        
        # Keep admin login accessible from student view
        with st.sidebar.expander("Admin Login"):
            admin_login()

if __name__ == "__main__":
    main()
