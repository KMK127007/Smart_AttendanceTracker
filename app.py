import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, date
import random
import string
import requests

# --- Page Configuration ---
st.set_page_config(
    page_title="📋 Attendance Tracker",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Session State Initialization ---
for key, default in {
    "admin_logged": False,
    "student_logged": False,
    "student_access_code": None,
    "otp_store": {},
    "attendance_marked": set()
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# --- Constants ---
STUDENTS_CSV = "students.csv"
ATTENDANCE_CSV = "attendance.csv"
OTP_VALIDITY_MINUTES = 5
ADMINS = {"kmk": {"password": "password123"}}

# --- Custom CSS Styling ---
def local_css():
    st.markdown("""
        <style>
        .header { font-size: 2.5rem; font-weight: 700; color: #4a90e2; margin-bottom: 0.5rem; }
        .subheader { font-size: 1.5rem; color: #1f77b4; margin-top: 1rem; margin-bottom: 1rem; }
        .section { background: #f8f9fa; padding: 1.5rem; border-radius: 10px; box-shadow: 0 0 10px #e0e0e0; margin-bottom: 2rem; }
        .stButton>button { background-color: #4a90e2; color: white; border-radius: 8px; padding: 0.5rem 1rem; font-weight: bold; }
        .stTextInput>div>div>input, .stSelectbox>div>div>div { border-radius: 8px; padding: 0.4rem; }
        </style>
    """, unsafe_allow_html=True)

local_css()

# --- Utility Functions ---
def load_students():
    try:
        df = pd.read_csv(STUDENTS_CSV)
        if {"username", "password", "college", "level"}.issubset(df.columns):
            return df
    except FileNotFoundError:
        pass
    df = pd.DataFrame(columns=["username", "password", "college", "level"])
    df.to_csv(STUDENTS_CSV, index=False)
    return df

def save_students(df): df.to_csv(STUDENTS_CSV, index=False)

def load_attendance():
    try:
        return pd.read_csv(ATTENDANCE_CSV)
    except FileNotFoundError:
        df = pd.DataFrame(columns=["date", "username", "college", "level", "timestamp"])
        df.to_csv(ATTENDANCE_CSV, index=False)
        return df

def save_attendance(df): df.to_csv(ATTENDANCE_CSV, index=False)

def generate_student_access_code():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    st.session_state.student_access_code = code
    return code

def send_otp(username):
    otp = ''.join(random.choices(string.digits, k=6))
    expiry = datetime.now() + timedelta(minutes=OTP_VALIDITY_MINUTES)
    st.session_state.otp_store[username] = (otp, expiry)
    return otp

def verify_otp(username, input_otp):
    record = st.session_state.otp_store.get(username)
    if not record:
        return False, "No OTP sent or session expired."
    otp, expiry = record
    if datetime.now() > expiry:
        return False, "OTP expired. Please try again."
    if input_otp == otp:
        del st.session_state.otp_store[username]
        return True, "OTP verified successfully ✅"
    return False, "Incorrect OTP ❌"

def mark_attendance(username, college, level):
    device_id = st.experimental_user_agent().user_agent or "unknown-device"
    key = (username, device_id, date.today())
    if key in st.session_state.attendance_marked:
        return False, "Attendance already marked from this device today."
    df = load_attendance()
    new_entry = {
        "date": date.today().isoformat(),
        "username": username,
        "college": college,
        "level": level,
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
    save_attendance(df)
    st.session_state.attendance_marked.add(key)
    return True, "Attendance marked successfully ✅"

# --- Admin Functions ---
def admin_login():
    st.sidebar.header("🔐 Admin Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
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
        st.rerun()

def admin_panel():
    st.markdown('<div class="header">🛠️ Admin Panel</div>', unsafe_allow_html=True)
    st.write(f"Logged in as: **{st.session_state.admin_user}**")

    # Access Code Generator
    st.markdown('<div class="subheader">🎟️ Student Access Code</div>', unsafe_allow_html=True)
    if st.button("Generate New Access Code"):
        code = generate_student_access_code()
        st.success(f"New Access Code: **{code}**")
    elif st.session_state.student_access_code:
        st.info(f"Current Access Code: **{st.session_state.student_access_code}**")
    else:
        st.warning("No access code generated yet.")

    tabs = st.tabs(["➕ Manage Students", "📊 View Attendance"])

    with tabs[0]:
        df = load_students()
        st.markdown('<div class="subheader">Add New Student</div>', unsafe_allow_html=True)
        new_username = st.text_input("Username")
        new_college = st.text_input("College")
        new_level = st.selectbox("Level", ["L1", "L2"])

        if st.button("Add Student"):
            if new_username and new_college:
                if new_username in df["username"].values:
                    st.warning("Username already exists.")
                else:
                    new_student = {
                        "username": new_username,
                        "password": "default123",
                        "college": new_college,
                        "level": new_level
                    }
                    df = pd.concat([df, pd.DataFrame([new_student])], ignore_index=True)
                    save_students(df)
                    st.success(f"Student '{new_username}' added successfully.")
                    st.rerun()
            else:
                st.warning("Please fill all fields.")
        st.dataframe(df.drop(columns=["password"]), use_container_width=True)

    with tabs[1]:
        attendance_df = load_attendance()
        if attendance_df.empty:
            st.info("No attendance yet.")
        else:
            st.dataframe(attendance_df, use_container_width=True)

# --- Student Flow ---
def student_dashboard():
    st.markdown('<div class="header">📚 Student Attendance</div>', unsafe_allow_html=True)
    with st.container():
        username = st.text_input("Enter Username")
        college = st.text_input("Enter College")
        level = st.selectbox("Select Level", ["L1", "L2"])
        access_code_input = st.text_input("Enter Access Code")

        if username and college and level and access_code_input:
            if access_code_input != st.session_state.get("student_access_code"):
                st.error("Invalid Access Code ⛔")
                return

            if st.button("Send OTP"):
                otp = send_otp(username)
                st.success(f"OTP sent to: {username} (Simulated OTP: {otp})")

            otp_input = st.text_input("Enter OTP")
            if st.button("Verify OTP & Mark Attendance"):
                valid, msg = verify_otp(username, otp_input)
                if not valid:
                    st.error(msg)
                else:
                    success, mark_msg = mark_attendance(username, college, level)
                    if success:
                        st.success(mark_msg)
                    else:
                        st.warning(mark_msg)

# --- Main Function ---
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
