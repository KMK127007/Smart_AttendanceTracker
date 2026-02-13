import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import qrcode
from io import BytesIO
import base64
import os
from pathlib import Path
import warnings
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
    st.error(f"Missing secret: {e}. Please add to secrets.toml")
    st.stop()

# ------------------------------
# Supabase client
@st.cache_resource
def get_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase_client()

# ------------------------------
# CSS
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
    except Exception:
        pass

local_css()

# ------------------------------
# Session state
for key, default in {
    "admin_logged": False,
    "admin_user": None,
    "qr_code_active": False,
    "qr_code_data": None,
    "qr_code_url": None,
    "qr_generated_time": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------
# Supabase DB helpers

def log_action(action: str, details: str = ""):
    try:
        supabase.table("activity_log").insert({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details
        }).execute()
    except Exception as e:
        print(f"Log error: {e}")

def load_students():
    try:
        res = supabase.table("students_new").select("*").order("rollnumber").execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["rollnumber", "studentname", "branch"])
    except Exception as e:
        st.error(f"Error loading students: {e}")
        return pd.DataFrame(columns=["rollnumber", "studentname", "branch"])

def add_student(rollnumber, studentname, branch):
    try:
        supabase.table("students_new").insert({
            "rollnumber": rollnumber.strip(),
            "studentname": studentname.strip(),
            "branch": branch.strip()
        }).execute()
        return True, f"✅ Student '{studentname}' added!"
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return False, f"⚠️ Roll number '{rollnumber}' already exists!"
        return False, f"Error: {e}"

def delete_student(rollnumber):
    try:
        supabase.table("students_new").delete().eq("rollnumber", rollnumber).execute()
        return True, f"✅ Removed '{rollnumber}'"
    except Exception as e:
        return False, f"Error: {e}"

def load_attendance():
    try:
        res = supabase.table("attendance_new").select("*").order("datestamp", desc=True).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
    except Exception as e:
        st.error(f"Error loading attendance: {e}")
        return pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])

def load_today_attendance():
    try:
        today = date.today().isoformat()
        res = supabase.table("attendance_new").select("*").eq("datestamp", today).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
    except Exception as e:
        st.error(f"Error loading today's attendance: {e}")
        return pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])

def check_already_marked(rollnumber, date_str):
    try:
        res = supabase.table("attendance_new").select("*")\
            .eq("rollnumber", rollnumber)\
            .eq("datestamp", date_str).execute()
        return len(res.data) > 0
    except Exception:
        return False

def mark_attendance_manual(rollnumber, studentname, date_str):
    try:
        if check_already_marked(rollnumber, date_str):
            return False, f"⚠️ Attendance already marked for {rollnumber} on {date_str}"
        supabase.table("attendance_new").insert({
            "rollnumber": rollnumber,
            "studentname": studentname,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "datestamp": date_str
        }).execute()
        return True, f"✅ Attendance marked for {studentname} on {date_str}!"
    except Exception as e:
        return False, f"Error: {e}"

def load_device_bindings():
    try:
        res = supabase.table("device_binding").select("*").execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["rollnumber", "device_id", "bound_at"])
    except Exception as e:
        st.error(f"Error loading device bindings: {e}")
        return pd.DataFrame(columns=["rollnumber", "device_id", "bound_at"])

def unbind_device(rollnumber):
    try:
        supabase.table("device_binding").delete().eq("rollnumber", rollnumber).execute()
        return True, f"✅ Device unbound for '{rollnumber}'"
    except Exception as e:
        return False, f"Error: {e}"

def load_logs():
    try:
        res = supabase.table("activity_log").select("*").order("timestamp", desc=True).limit(100).execute()
        return pd.DataFrame(res.data) if res.data else pd.DataFrame(columns=["timestamp", "action", "details"])
    except Exception as e:
        return pd.DataFrame(columns=["timestamp", "action", "details"])

# ------------------------------
# QR Code generation
def generate_qr_code():
    """Generate QR code with 40-second expiry token"""
    current_timestamp = int(time.time())
    access_token = f"qr_{current_timestamp}"
    qr_url = f"https://smartapp12.streamlit.app?access={access_token}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.getvalue()).decode()

    st.session_state.qr_code_active = True
    st.session_state.qr_code_data = img_base64
    st.session_state.qr_code_url = qr_url
    st.session_state.qr_generated_time = current_timestamp
    log_action("generate_qr_code", f"Timestamp: {current_timestamp}")

    return img_base64, qr_url, current_timestamp

# ------------------------------
# Admin login/logout
def admin_login():
    st.sidebar.header("🔐 Admin Login")
    username = st.sidebar.text_input("Username", key="admin_username_input")
    password = st.sidebar.text_input("Password", type="password", key="admin_password_input")
    if st.sidebar.button("Login"):
        if username in ADMINS and ADMINS[username]["password"] == password:
            st.session_state.admin_logged = True
            st.session_state.admin_user = username
            st.sidebar.success(f"Welcome, {username}!")
            log_action("admin_login", username)
            st.rerun()
        else:
            st.sidebar.error("Invalid credentials ❌")

def admin_logout():
    if st.sidebar.button("🚪 Logout"):
        st.session_state.admin_logged = False
        st.session_state.admin_user = None
        log_action("admin_logout", "")
        st.rerun()

# ------------------------------
# Admin panel
def admin_panel():
    st.markdown('<div class="header">🎯 QR Attendance System - Admin Panel</div>', unsafe_allow_html=True)
    st.write(f"Logged in as: **{st.session_state.admin_user}**")
    st.markdown("---")

    # QR Code Section
    st.markdown("### 📱 Generate QR Code")
    st.info("⏱️ **QR codes expire in 40 seconds.** Generate a new one for each attendance session.")

    if st.button("🔲 Generate New QR Code", type="primary", key="gen_qr_btn"):
        qr_img, qr_url, timestamp = generate_qr_code()
        st.success("✅ QR Code generated!")
        st.info(f"**Valid for 40 seconds** | Generated at: {datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')}")

    # Display active QR
    if st.session_state.qr_code_active and st.session_state.qr_code_data:
        st.markdown("### 📱 Active QR Code")
        current_time = int(time.time())
        time_elapsed = current_time - st.session_state.qr_generated_time
        time_remaining = 40 - time_elapsed

        if time_remaining > 0:
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(f'<img src="data:image/png;base64,{st.session_state.qr_code_data}" width="300"/>', unsafe_allow_html=True)
            with col2:
                st.metric("⏱️ Time Remaining", f"{time_remaining}s")
                st.success("✅ Active")
                st.caption(f"Expires at: {datetime.fromtimestamp(st.session_state.qr_generated_time + 40).strftime('%H:%M:%S')}")
        else:
            st.error("❌ QR Code Expired - Generate a new one")

        if st.button("🗑️ Clear QR"):
            st.session_state.qr_code_active = False
            st.session_state.qr_code_data = None
            st.rerun()

    st.markdown("---")

    # Tabs
    tabs = st.tabs(["👥 Manage Students", "📊 View Attendance", "✍️ Manual Attendance", "📱 Device Bindings", "📋 Logs"])

    # TAB 1: Manage Students
    with tabs[0]:
        st.markdown("### ➕ Add New Student")
        col1, col2, col3 = st.columns(3)
        with col1:
            new_roll = st.text_input("Roll Number", key="new_roll")
        with col2:
            new_name = st.text_input("Student Name", key="new_name")
        with col3:
            new_branch = st.text_input("Branch", key="new_branch")

        if st.button("➕ Add Student", key="add_btn"):
            if new_roll and new_name and new_branch:
                success, msg = add_student(new_roll, new_name, new_branch)
                if success:
                    st.success(msg)
                    log_action("add_student", new_roll)
                    st.rerun()
                else:
                    st.warning(msg)
            else:
                st.warning("⚠️ Fill all fields")

        st.markdown("---")
        st.markdown("### 👥 All Students")
        students_df = load_students()
        if not students_df.empty:
            display_cols = [c for c in ["rollnumber", "studentname", "branch"] if c in students_df.columns]
            st.dataframe(students_df[display_cols], width=1000)
            st.info(f"**Total:** {len(students_df)} students")

            csv = students_df[display_cols].to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download List", csv, "students_new.csv", "text/csv", key="dl_students")

            st.markdown("### 🗑️ Remove Student")
            to_del = st.selectbox("Select:", [""] + students_df['rollnumber'].tolist(), key="del_sel")
            if to_del and st.button("🗑️ Remove", key="del_btn"):
                success, msg = delete_student(to_del)
                if success:
                    st.success(msg)
                    log_action("delete_student", to_del)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            st.info("No students yet.")

    # TAB 2: View Attendance
    with tabs[1]:
        st.markdown("### 📊 Attendance Records")
        today_df = load_today_attendance()
        today = date.today().isoformat()

        if not today_df.empty:
            st.success(f"📅 **Today ({today})**")
            display_cols = [c for c in ["rollnumber", "studentname", "timestamp", "datestamp"] if c in today_df.columns]
            st.dataframe(today_df[display_cols], width=1000)
            st.info(f"**Present Today:** {len(today_df)}")

            csv = today_df[display_cols].to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download Today's", csv, f"attendance_{today}.csv", "text/csv", key="dl_today")
        else:
            st.info("No attendance today yet.")

        st.markdown("---")
        all_df = load_attendance()
        if not all_df.empty:
            st.markdown("### 📋 All Records")
            display_cols = [c for c in ["rollnumber", "studentname", "timestamp", "datestamp"] if c in all_df.columns]
            st.dataframe(all_df[display_cols], width=1000)
            st.info(f"**Total Records:** {len(all_df)}")

            csv_all = all_df[display_cols].to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download All", csv_all, "attendance_all.csv", "text/csv", key="dl_all")
        else:
            st.info("No attendance records yet.")

    # TAB 3: Manual Attendance
    with tabs[2]:
        st.markdown("### ✍️ Mark Attendance Manually")
        st.info("💡 Use this if a student missed the QR scan.")

        students_df = load_students()

        if not students_df.empty:
            st.markdown("#### Option 1: Select from Student List")
            sel_roll = st.selectbox("Select Student:", [""] + students_df['rollnumber'].tolist(), key="man_sel")

            if sel_roll:
                s = students_df[students_df['rollnumber'] == sel_roll].iloc[0]
                st.success(f"**Name:** {s['studentname']} | **Branch:** {s['branch']}")
                man_date = st.date_input("Date", value=date.today(), key="man_date1")

                if st.button("✅ Mark Attendance", key="man_btn1", type="primary"):
                    success, msg = mark_attendance_manual(sel_roll, s['studentname'], man_date.isoformat())
                    if success:
                        st.success(msg)
                        log_action("manual_attendance", f"{sel_roll} - {man_date.isoformat()}")
                        st.rerun()
                    else:
                        st.warning(msg)

            st.markdown("---")
            st.markdown("#### Option 2: Enter Details Manually")
            st.warning("⚠️ Only if student is NOT in the list")

            col1, col2, col3 = st.columns(3)
            with col1:
                m_roll = st.text_input("Roll Number", key="m_roll")
            with col2:
                m_name = st.text_input("Student Name", key="m_name")
            with col3:
                m_branch = st.text_input("Branch", key="m_branch")

            m_date = st.date_input("Date", value=date.today(), key="m_date2")

            if st.button("✅ Mark Manually", key="man_btn2"):
                if m_roll and m_name and m_branch:
                    success, msg = mark_attendance_manual(m_roll, m_name, m_date.isoformat())
                    if success:
                        st.success(msg)
                        log_action("manual_attendance_custom", f"{m_roll} - {m_date.isoformat()}")
                        st.rerun()
                    else:
                        st.warning(msg)
                else:
                    st.warning("⚠️ Fill all fields")
        else:
            st.info("Add students first.")

    # TAB 4: Device Bindings
    with tabs[3]:
        st.markdown("### 📱 Device Bindings")
        st.info("One device per student only.")
        device_df = load_device_bindings()

        if not device_df.empty:
            display_cols = [c for c in ["rollnumber", "device_id", "bound_at"] if c in device_df.columns]
            st.dataframe(device_df[display_cols], width=1000)
            st.info(f"**Bound Devices:** {len(device_df)}")

            st.markdown("### 🔓 Unbind Device")
            to_unbind = st.selectbox("Select:", [""] + device_df['rollnumber'].tolist(), key="unbind_sel")
            if to_unbind and st.button("🔓 Unbind", key="unbind_btn"):
                success, msg = unbind_device(to_unbind)
                if success:
                    st.success(msg)
                    log_action("unbind_device", to_unbind)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            st.info("No devices bound yet.")

    # TAB 5: Logs
    with tabs[4]:
        st.markdown("### 📋 Activity Logs")
        log_df = load_logs()
        if not log_df.empty:
            st.dataframe(log_df, width=1000)
        else:
            st.info("No logs yet.")

# ------------------------------
# Main
def main():
    st.set_page_config(
        page_title="QR Attendance Admin",
        page_icon="🎯",
        layout="wide"
    )
    st.sidebar.title("🎯 QR Attendance System")

    if st.session_state.admin_logged:
        admin_logout()
        admin_panel()
    else:
        admin_login()
        st.markdown('<div class="header">🎯 Smart QR Attendance System</div>', unsafe_allow_html=True)
        st.markdown("""
        ### Features:
        - ✅ **Supabase Database** - Real-time, permanent storage
        - ✅ **40-second QR expiry** - Secure time-limited access
        - ✅ **Location verification** - Students must be at college
        - ✅ **Device binding** - One device per student
        - ✅ **Manual attendance** - Admin override option
        
        **👈 Login from sidebar to get started!**
        """)

if __name__ == "__main__":
    main()
