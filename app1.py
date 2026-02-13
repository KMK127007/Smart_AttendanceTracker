import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import os
from pathlib import Path
import warnings
import uuid
from math import radians, sin, cos, sqrt, atan2
from supabase import create_client, Client

# Suppress warnings
warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

# ------------------------------
# Load secrets
try:
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
    SUPABASE_URL = st.secrets["supabase"]["url"]
    SUPABASE_KEY = st.secrets["supabase"]["key"]
except KeyError as e:
    st.error(f"Missing secret: {e}")
    st.stop()

# ------------------------------
# Supabase client
@st.cache_resource
def get_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase_client()

# ------------------------------
# College location settings
COLLEGE_LATITUDE = 17.4553223
COLLEGE_LONGITUDE = 78.6664965
ALLOWED_RADIUS_METERS = 500

# ------------------------------
# Session state
for key, default in {
    "admin_logged_app1": False,
    "qr_access_granted": False,
    "location_verified": False,
    "show_location_form": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------
# Supabase helpers

def load_students():
    try:
        res = supabase.table("students_new").select("*").execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["rollnumber", "studentname", "branch"])
    except Exception as e:
        st.error(f"Error: {e}")
        return pd.DataFrame(columns=["rollnumber", "studentname", "branch"])

def check_already_marked(rollnumber, date_str):
    try:
        res = supabase.table("attendance_new").select("*")\
            .eq("rollnumber", rollnumber.lower())\
            .eq("datestamp", date_str).execute()
        return len(res.data) > 0
    except Exception:
        return False

def get_device_fingerprint():
    if 'device_fingerprint' not in st.session_state:
        st.session_state.device_fingerprint = str(uuid.uuid4())
    return st.session_state.device_fingerprint

def check_device_binding(rollnumber):
    device_id = get_device_fingerprint()
    try:
        res = supabase.table("device_binding").select("*")\
            .eq("rollnumber", rollnumber.lower()).execute()

        if not res.data:
            # First time - bind device
            supabase.table("device_binding").insert({
                "rollnumber": rollnumber.lower(),
                "device_id": device_id,
                "bound_at": datetime.now().isoformat()
            }).execute()
            return True, "Device bound ✅"
        else:
            bound_device = res.data[0]['device_id']
            if bound_device == device_id:
                return True, "Device verified ✅"
            else:
                return False, "❌ This roll number is already registered on another device. Contact admin to unbind."
    except Exception as e:
        return False, f"Device check error: {e}"

def mark_attendance_qr(rollnumber, studentname, branch):
    # Load and validate student
    students_df = load_students()

    # Strip and lower comparison
    students_df['rollnumber'] = students_df['rollnumber'].str.strip()
    students_df['studentname'] = students_df['studentname'].str.strip()
    students_df['branch'] = students_df['branch'].str.strip()

    match = students_df[
        (students_df['rollnumber'].str.lower() == rollnumber.strip().lower()) &
        (students_df['studentname'].str.lower() == studentname.strip().lower()) &
        (students_df['branch'].str.lower() == branch.strip().lower())
    ]

    if match.empty:
        roll_match = students_df[students_df['rollnumber'].str.lower() == rollnumber.strip().lower()]
        name_match = students_df[students_df['studentname'].str.lower() == studentname.strip().lower()]
        branch_match = students_df[students_df['branch'].str.lower() == branch.strip().lower()]

        msg = "Student not found. "
        if roll_match.empty:
            msg += f"Roll '{rollnumber}' not found. "
        if name_match.empty:
            msg += f"Name '{studentname}' not found. "
        if branch_match.empty:
            msg += f"Branch '{branch}' not found. "
        return False, msg + "Check your details."

    # Device check
    device_ok, device_msg = check_device_binding(rollnumber)
    if not device_ok:
        return False, device_msg

    # Check already marked today
    today = date.today().isoformat()
    if check_already_marked(rollnumber, today):
        return False, "Attendance already marked today! ⚠️"

    # Mark attendance
    try:
        supabase.table("attendance_new").insert({
            "rollnumber": rollnumber.strip(),
            "studentname": studentname.strip(),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "datestamp": today
        }).execute()
        return True, "Attendance marked successfully! ✅"
    except Exception as e:
        return False, f"Error saving attendance: {e}"

def load_attendance_for_admin():
    try:
        res = supabase.table("attendance_new").select("*").order("datestamp", desc=True).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
    except Exception:
        return pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])

# ------------------------------
# Location helpers
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def check_location_in_range(user_lat, user_lon):
    distance = calculate_distance(COLLEGE_LATITUDE, COLLEGE_LONGITUDE, user_lat, user_lon)
    return distance <= ALLOWED_RADIUS_METERS, distance

# ------------------------------
# QR Access check
def check_qr_access():
    query_params = st.query_params
    if "access" in query_params:
        token = query_params["access"]
        if token.startswith("qr_"):
            try:
                qr_timestamp = int(token.replace("qr_", ""))
                time_elapsed = int(time.time()) - qr_timestamp
                if time_elapsed <= 40:
                    st.session_state.qr_access_granted = True
                    return True, None
                else:
                    return False, f"QR expired! ({time_elapsed}s old, max 40s)"
            except:
                return False, "Invalid QR format"
    if st.session_state.qr_access_granted:
        return True, None
    return False, "No valid QR scanned"

# ------------------------------
# CSS Loader
def local_css(file_name="style.css"):
    try:
        base = Path(__file__).parent
    except Exception:
        base = Path.cwd()
    try:
        css_path = base / file_name
        if css_path.exists():
            with open(css_path, encoding="utf-8") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except Exception:
        pass

local_css()

# ------------------------------
# QR Student Portal
def qr_student_portal():
    st.markdown('<div class="header">📱 QR Code Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Quick Attendance via QR Code")

    with st.container():
        st.markdown("Enter your details to mark attendance.")
        rollnumber = st.text_input("Roll Number", key="qr_roll", placeholder="e.g. 22311a1965")
        studentname = st.text_input("Student Name", key="qr_name", placeholder="Enter your full name")
        branch = st.text_input("Branch", key="qr_branch", placeholder="e.g. CSE, ECE, ECMB")

        if st.button("✅ Mark Attendance", key="mark_btn", type="primary"):
            if rollnumber and studentname and branch:
                with st.spinner("Marking attendance..."):
                    success, message = mark_attendance_qr(rollnumber, studentname, branch)
                if success:
                    st.success(message)
                    st.balloons()
                    st.info(f"**Roll:** {rollnumber} | **Name:** {studentname} | **Time:** {datetime.now().strftime('%H:%M:%S')}")
                else:
                    st.error(message)
            else:
                st.warning("⚠️ Please fill all fields")

    st.markdown("---")
    st.info("💡 Enter your Roll Number, Name and Branch then click Mark Attendance")

    # Admin section
    st.markdown("---")
    st.markdown("### 🔐 Admin Access")

    if not st.session_state.admin_logged_app1:
        with st.expander("🔑 Admin Login (No time restriction)"):
            u = st.text_input("Username", key="adm_user")
            p = st.text_input("Password", type="password", key="adm_pass")
            if st.button("Login", key="adm_login_btn"):
                if u in ADMINS and ADMINS[u]["password"] == p:
                    st.session_state.admin_logged_app1 = True
                    st.session_state.qr_access_granted = True
                    st.success("✅ Admin logged in!")
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
    else:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success("✅ Logged in as Admin")
        with col2:
            if st.button("🚪 Logout", key="adm_logout"):
                st.session_state.admin_logged_app1 = False
                st.rerun()

        st.markdown("---")
        admin_tabs = st.tabs(["📊 Today's Attendance", "📋 All Records"])

        with admin_tabs[0]:
            today = date.today().isoformat()
            all_df = load_attendance_for_admin()
            today_df = all_df[all_df['datestamp'] == today] if not all_df.empty else pd.DataFrame()

            if not today_df.empty:
                st.success(f"📅 Today ({today}) - {len(today_df)} present")
                cols = [c for c in ["rollnumber", "studentname", "timestamp", "datestamp"] if c in today_df.columns]
                st.dataframe(today_df[cols], width=800)
                csv = today_df[cols].to_csv(index=False).encode('utf-8')
                st.download_button("⬇️ Download Today's", csv, f"attendance_{today}.csv", "text/csv", key="dl_today")
            else:
                st.info("No attendance today yet.")

        with admin_tabs[1]:
            all_df = load_attendance_for_admin()
            if not all_df.empty:
                cols = [c for c in ["rollnumber", "studentname", "timestamp", "datestamp"] if c in all_df.columns]
                st.dataframe(all_df[cols], width=800)
                st.info(f"**Total Records:** {len(all_df)}")
                csv_all = all_df[cols].to_csv(index=False).encode('utf-8')
                st.download_button("⬇️ Download All", csv_all, "attendance_all.csv", "text/csv", key="dl_all")
            else:
                st.info("No records yet.")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker - QR Portal | Powered by Streamlit + Supabase")

# ------------------------------
# Main
def main():
    st.set_page_config(
        page_title="QR Attendance Portal",
        page_icon="📱",
        layout="centered"
    )

    # Admin bypasses QR check entirely
    if st.session_state.admin_logged_app1:
        qr_student_portal()
        return

    # Student path - check QR
    access_valid, error_msg = check_qr_access()

    if not access_valid:
        st.error("🔒 **Access Denied**")
        if error_msg:
            st.warning(f"**Reason:** {error_msg}")
        st.info("📱 Ask admin to generate a NEW QR code and scan within 40 seconds.")

        st.markdown("---")
        st.markdown("### 🔐 Admin Login")
        with st.expander("🔑 Admin Login (No time restriction)"):
            u = st.text_input("Username", key="blocked_adm_user")
            p = st.text_input("Password", type="password", key="blocked_adm_pass")
            if st.button("Login", key="blocked_adm_btn"):
                if u in ADMINS and ADMINS[u]["password"] == p:
                    st.session_state.admin_logged_app1 = True
                    st.session_state.qr_access_granted = True
                    st.success("✅ Admin logged in!")
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
        st.stop()

    # Student location check
    st.success("✅ QR Code verified!")

    if not st.session_state.location_verified:
        st.markdown("### 📍 Location Verification Required")
        st.info("Allow location access to verify you are at college.")

        if st.button("📍 Verify My Location", type="primary", key="loc_btn"):
            st.session_state.show_location_form = True

        if st.session_state.show_location_form:
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                user_lat = st.number_input("Latitude", value=17.4605, format="%.6f", key="lat_input")
            with col2:
                user_lon = st.number_input("Longitude", value=78.4607, format="%.6f", key="lon_input")

            if st.button("✅ Confirm Location", type="primary", key="confirm_loc"):
                in_range, distance = check_location_in_range(user_lat, user_lon)
                if in_range:
                    st.session_state.location_verified = True
                    st.success(f"✅ Location verified! You are {int(distance)}m from college.")
                    st.rerun()
                else:
                    st.error(f"❌ You are {int(distance)}m away. Must be within {ALLOWED_RADIUS_METERS}m of college.")
        st.stop()

    st.success("✅ Location verified!")
    qr_student_portal()

if __name__ == "__main__":
    main()

