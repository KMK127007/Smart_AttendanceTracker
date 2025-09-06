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

try:
    HUGGINGFACE_API_KEY = st.secrets["huggingface_key"]
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as e:
    st.error(f"Configuration error: Missing secret key '{e}'. Please ensure your secrets.toml has 'huggingface_key' and 'admin_user.username', 'admin_user.password'.")
    st.stop()

DEFAULT_HF_INSTRUCTION_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"
DEFAULT_HF_SUMMARIZATION_MODEL = "facebook/bart-large-cnn"

try:
    local_instruction_pipe = pipeline("text2text-generation", model="t5-small")
    local_summarization_pipe = pipeline("summarization", model="t5-small")
except Exception as e:
    local_instruction_pipe = None
    local_summarization_pipe = None
    print(f"Local pipelines could not be initialized: {e}")

def local_fallback_instruction(prompt, max_tokens=200):
    if local_instruction_pipe:
        try:
            result = local_instruction_pipe(prompt, max_new_tokens=max_tokens, do_sample=True, temperature=0.7)
            return result[0]["generated_text"]
        except Exception as e:
            return f"Local AI generation failed: {e}"
    return "Local instruction pipeline unavailable."

def local_fallback_summary(text, max_tokens=200):
    if local_summarization_pipe:
        try:
            result = local_summarization_pipe(text, max_new_tokens=max_tokens)
            return result[0]["summary_text"]
        except Exception as e:
            return f"Local summarization failed: {e}"
    return "Local summarization pipeline unavailable."


def query_huggingface_model(prompt, max_tokens=200, model_id=DEFAULT_HF_INSTRUCTION_MODEL, retries=3, delay=5):
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": max_tokens,
            "temperature": 0.7,
            "do_sample": True
        }
    }

    for attempt in range(retries):
        try:
            response = requests.post(
                f"https://api-inference.huggingface.co/models/{model_id}",
                headers=headers,
                data=json.dumps(payload),
                timeout=360
            )
            response.raise_for_status()
            result = response.json()

            # Handle instruction model
            if model_id == DEFAULT_HF_INSTRUCTION_MODEL:
                if isinstance(result, list) and result and "generated_text" in result[0]:
                    return result[0]["generated_text"].replace(prompt, "").strip()

            # Handle summarization model
            elif model_id == DEFAULT_HF_SUMMARIZATION_MODEL:
                if isinstance(result, list) and result and "summary_text" in result[0]:
                    return result[0]["summary_text"].strip()

            # Handle API error
            if isinstance(result, dict) and "error" in result:
                error_msg = result["error"]
                st.warning(f"Model {model_id} error: {error_msg}")
                # go to fallback instead of returning immediately
                break  

            return str(result)

        except requests.exceptions.Timeout:
            st.warning(f"Timeout from {model_id}, attempt {attempt+1}/{retries}")
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            break  # use fallback after retries

        except Exception as e:
            st.error(f"Error with {model_id}: {e}")
            break  # use fallback instead of returning immediately

    # 🔄 Local fallback after retries or failure
    if model_id == DEFAULT_HF_INSTRUCTION_MODEL:
        return local_fallback_instruction(prompt, max_tokens)
    elif model_id == DEFAULT_HF_SUMMARIZATION_MODEL:
        return local_fallback_summary(prompt, max_tokens)

    return "AI generation failed after retries (no fallback available)."

def local_css(file_name="style.css"):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    css_file_path = os.path.join(current_dir, file_name)

    try:
        with open(css_file_path) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        st.error(f"Error: CSS file '{file_name}' not found at '{css_file_path}'. Please ensure it's in the same directory as app.py.")
    except Exception as e:
        st.error(f"An error occurred while loading CSS: {e}")


local_css()

for key, default in {
    "admin_logged": False,
    "student_logged_in_username": None,
    "student_access_code": None,
    "otp_store": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

STUDENTS_CSV = "students.csv"
ATTENDANCE_CSV = "attendance.csv"
OTP_VALIDITY_MINUTES = 5


def load_students():
    try:
        df = pd.read_csv(STUDENTS_CSV)
        if {"username", "password", "college", "level"}.issubset(df.columns):
            if 'remarks' not in df.columns:
                df['remarks'] = ''
            return df
    except FileNotFoundError:
        pass
    df = pd.DataFrame(columns=["username", "password", "college", "level", "remarks"])
    df.to_csv(STUDENTS_CSV, index=False)
    return df

def save_students(df):
    df.to_csv(STUDENTS_CSV, index=False)

def load_attendance():
    try:
        return pd.read_csv(ATTENDANCE_CSV)
    except FileNotFoundError:
        df = pd.DataFrame(columns=["date", "username", "college", "level", "timestamp"])
        df.to_csv(ATTENDANCE_CSV, index=False)
        return df

def save_attendance(df):
    df.to_csv(ATTENDANCE_CSV, index=False)

def generate_student_access_code():
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    st.session_state.student_access_code = code
    return code

def send_otp(username):
    otp = "".join(random.choices(string.digits, k=6))
    expiry = datetime.now() + timedelta(minutes=OTP_VALIDITY_MINUTES)
    st.session_state.otp_store[username] = (otp, expiry)
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
        return True, "OTP verified successfully ✅"
    return False, "Incorrect OTP ❌"

def has_marked_attendance_today(username):
    attendance_df = load_attendance()
    today_date_str = date.today().isoformat()
    return not attendance_df[(attendance_df['username'] == username) &
                             (attendance_df['date'] == today_date_str)].empty

def mark_attendance(username, college, level):
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
    return True, "Attendance marked successfully ✅"

def generate_analytics_summary():
    attendance_df = load_attendance()
    if attendance_df.empty:
        return "No attendance data available to generate a summary."

    college_attendance = attendance_df.groupby('college').size().reset_index(name='total_attendance')
    college_summary = college_attendance.to_string(index=False)

    level_attendance = attendance_df.groupby(['level', 'date']).size().reset_index(name='count')
    level_pivot = level_attendance.pivot_table(index='date', columns='level', values='count').fillna(0)
    
    level_trend_summary = []
    if 'L1' in level_pivot.columns:
        if len(level_pivot['L1']) > 1:
            l1_trend = "upward" if level_pivot['L1'].iloc[-1] > level_pivot['L1'].mean() else ("downward" if level_pivot['L1'].iloc[-1] < level_pivot['L1'].mean() else "stable")
        else:
            l1_trend = "not enough data for a clear trend"
        level_trend_summary.append(f"L1 group attendance trend: {l1_trend}.")
    if 'L2' in level_pivot.columns:
        if len(level_pivot['L2']) > 1:
            l2_trend = "upward" if level_pivot['L2'].iloc[-1] > level_pivot['L2'].mean() else ("downward" if level_pivot['L2'].iloc[-1] < level_pivot['L2'].mean() else "stable")
        else:
            l2_trend = "not enough data for a clear trend"
        level_trend_summary.append(f"L2 group attendance trend: {l2_trend}.")
    
    full_prompt = f"""
    Analyze the following attendance data and provide a concise summary highlighting key insights such as:
    1. Which colleges have the highest/lowest attendance.
    2. Any noticeable trends in L1/L2 group attendance (e.g., upward, downward, stable).
    3. Any other significant patterns.

    Attendance by College:
    {college_summary}

    Attendance Trends by Level (daily counts for L1 and L2):
    {level_pivot.to_string()}

    Summary:
    """
    
    st.info("Generating AI analytics summary, please wait...")
    summary = query_huggingface_model(full_prompt, model_id=DEFAULT_HF_SUMMARIZATION_MODEL, max_tokens=300)
    
    # Check if the summary is empty or just echoes the prompt (common for summarization models with insufficient input)
    if not summary or summary.strip().lower().startswith("analyze the following attendance data"):
        return "The AI could not generate a meaningful summary based on the current data. Please ensure there is sufficient attendance data and try again."
    
    return summary


def generate_student_ai_report(student_username):
    students_df = load_students()
    attendance_df = load_attendance()

    student_data = students_df[students_df['username'] == student_username]
    if student_data.empty:
        return "Student not found."

    student_remarks = student_data['remarks'].iloc[0] if 'remarks' in student_data.columns else ""

    student_attendance = attendance_df[attendance_df['username'] == student_username]
    total_days_attended = len(student_attendance)
    
    if not attendance_df.empty:
        all_attendance_dates = pd.to_datetime(attendance_df['date']).unique()
        total_possible_days_in_dataset = len(all_attendance_dates)
    else:
        total_possible_days_in_dataset = 1
    
    attendance_percentage = (total_days_attended / total_possible_days_in_dataset) * 100 if total_possible_days_in_dataset > 0 else 0

    l1_count = student_attendance[student_attendance['level'] == 'L1'].shape[0]
    l2_count = student_attendance[student_attendance['level'] == 'L2'].shape[0]

    prompt = f"""
    Generate a personalized student report for {student_username} based on the following information.
    Focus on providing constructive feedback and insights, starting directly with the report.

    Student Username: {student_username}
    Total Attendance: {total_days_attended} days attended out of {total_possible_days_in_dataset} total possible attendance days ({attendance_percentage:.2f}%).
    L1 Attendance Count: {l1_count}
    L2 Attendance Count: {l2_count}
    Admin Remarks: "{student_remarks}"

    The report should cover:
    - Overall attendance summary, including the percentage.
    - Insights into L1/L2 attendance pattern (e.g., more attendance in one level, consistency, or lack thereof).
    - Behavioral insights derived from admin remarks, phrased constructively and encouragingly.
    - An encouraging closing statement.

    Personalized Student Report for {student_username}:
    """
    st.info(f"Generating AI report for {student_username}, please wait...")
    report = query_huggingface_model(prompt, max_tokens=300, model_id=DEFAULT_HF_INSTRUCTION_MODEL)
    return report

def summarize_student_remark_for_student(admin_remark):
    if not admin_remark.strip():
        return "No specific remarks from the admin at this time."
    
    prompt = f"""
    The admin has made the following remark about your performance/behavior:
    "{admin_remark}"

    Please rephrase this remark into a clear, concise, and constructive summary that a student can understand,
    focusing on areas for improvement or positive recognition. Avoid overly formal or negative language.
    Start directly with the summary, for example: "The admin notes that your attendance has been irregular..."

    Student-friendly summary:
    """
    st.info("Generating AI summary of admin remarks...")
    summary = query_huggingface_model(prompt, max_tokens=100, model_id=DEFAULT_HF_INSTRUCTION_MODEL)
    return summary

def admin_login():
    st.sidebar.header("🔐 Admin Login")
    username = st.sidebar.text_input("Username", key="admin_username_input")
    password = st.sidebar.text_input("Password", type="password", key="admin_password_input")
    if st.sidebar.button("Login as Admin"):
        if username in ADMINS and ADMINS[username]["password"] == password:
            st.session_state.admin_logged = True
            st.session_state.admin_user = username
            st.sidebar.success(f"Welcome, {username}")
            st.rerun()
        else:
            st.sidebar.error("Invalid admin credentials ❌")

def admin_logout():
    if st.sidebar.button("🚪 Logout Admin"):
        st.session_state.admin_logged = False
        st.session_state.admin_user = None
        st.rerun()

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
    
    tabs = st.tabs(["➕ Manage Students", "📊 View Attendance", "🧠 AI Analytics Summary", "📄 Student AI Reports"])

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
                        "username": new_username,
                        "password": "default123",
                        "college": new_college,
                        "level": new_level,
                        "remarks": "",
                    }
                    df = pd.concat([df, pd.DataFrame([new_student])], ignore_index=True)
                    save_students(df)
                    st.success(f"Student '{new_username}' added successfully.")
                    st.rerun()
            else:
                st.warning("Please fill all fields to add a student.")
        
        st.markdown('<div class="subheader">Manage Existing Students (Add Remarks)</div>', unsafe_allow_html=True)
        if not df.empty:
            selected_student_for_remarks = st.selectbox("Select Student to Add Remarks", [""] + sorted(df["username"].tolist()), key="select_student_remark")
            
            if selected_student_for_remarks:
                current_remarks = df[df['username'] == selected_student_for_remarks]['remarks'].iloc[0]
                new_remark = st.text_area(f"Add/Edit Remarks for {selected_student_for_remarks}", value=current_remarks, key="admin_student_remark_input")
                if st.button(f"Save Remarks for {selected_student_for_remarks}", key="save_student_remark_button"):
                    df.loc[df['username'] == selected_student_for_remarks, 'remarks'] = new_remark
                    save_students(df)
                    st.success(f"Remarks saved for {selected_student_for_remarks}")
                    st.rerun()
        else:
            st.info("No students added yet. Please add a new student above.")

        st.markdown('<div class="subheader">All Students</div>', unsafe_allow_html=True)
        if not df.empty:
            st.dataframe(df.drop(columns=["password"]), use_container_width=True)
        else:
            st.info("No student data available.")

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
            
            st.dataframe(filtered_attendance_df, use_container_width=True)

    with tabs[2]:
        st.markdown('<div class="subheader">🧠 AI-Generated Analytics Summary</div>', unsafe_allow_html=True)
        st.write("Click the button below to get an AI-powered summary of overall attendance trends.")
        if st.button("Generate AI Analytics Summary"):
            summary_placeholder = st.empty()
            with st.spinner("Generating smart analytics summary... This may take a moment."):
                summary = generate_analytics_summary()
                summary_placeholder.markdown(f"**Analytics Summary:**\n{summary}")
            
    with tabs[3]:
        st.markdown('<div class="subheader">📄 AI-Powered Student Report Generator</div>', unsafe_allow_html=True)
        students_df_for_report = load_students()
        if not students_df_for_report.empty:
            student_for_report = st.selectbox("Select Student for AI Report", [""] + sorted(students_df_for_report["username"].tolist()), key="select_student_report")
            if student_for_report:
                if st.button(f"Generate AI Report for {student_for_report}"):
                    report_placeholder = st.empty()
                    with st.spinner(f"Generating personalized report for {student_for_report}... This may take a moment."):
                        report = generate_student_ai_report(student_for_report)
                        report_placeholder.markdown(f"**Personalized Report for {student_for_report}:**\n{report}")
            else:
                st.info("Select a student from the dropdown to generate their AI report.")
        else:
            st.info("No students available to generate reports for. Please add students first.")

def student_dashboard():
    st.markdown('<div class="header">📚 Student Attendance</div>', unsafe_allow_html=True)
    with st.container():
        st.markdown("Please enter your details and the daily access code to mark your attendance.")
        username = st.text_input("Enter Username", key="student_username_input")
        college = st.text_input("Enter College", key="student_college_input")
        level = st.selectbox("Select Level", ["L1", "L2"], key="student_level_input")
        access_code_input = st.text_input("Enter Access Code", help="Get this from your admin", key="student_access_code_input")
        
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
                             st.info(f"An OTP was already sent to {username} and is still valid. Check your (simulated) message.")
                        else:
                            otp = send_otp(username)
                            st.info(f"OTP sent to: {username}. (For demo: OTP is {otp})")

                with col2:
                    otp_input = st.text_input("Enter OTP", key="otp_input")
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
                            else:
                                st.warning(mark_msg)
                                if "Attendance already marked today" in mark_msg:
                                    st.session_state.student_logged_in_username = username
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
                    st.info(f"**Admin's Feedback for {username}:**\n"
                            f"{st.session_state[f'remarks_summary_{username}']}")
                else:
                    st.info("Click 'View AI Summary of Admin Remarks' to see your feedback.")

            else:
                st.info(f"No student data found for '{username}'. Please ensure your username is correct and added by the admin.")
        else:
            st.info("Enter your username above to see your AI-generated remarks summary.")


def main():
    st.sidebar.title("📋 Attendance System")
    if st.session_state.admin_logged:
        admin_logout()
        admin_panel()
    else:
        student_dashboard()
        admin_login()

if __name__ == "__main__":
    main()
