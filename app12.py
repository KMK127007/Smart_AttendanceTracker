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

# Suppress warnings
warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

# ------------------------------
# Load secrets for admin authentication
try:
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as _:
    st.error(f"Configuration error: Missing admin credentials in secrets.")
    st.stop()

# ------------------------------
# CSS
def local_css(file_name="style.css"):
    try:
        base = Path(__file__).parent
    except Exception as _:
        base = Path.cwd()
    css_file_path = base / file_name
    try:
        if css_file_path.exists():
            with open(css_file_path, encoding="utf-8") as f:
                st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except Exception as _:
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
# File paths
STUDENTS_NEW_CSV = "students_new.csv"
ATTENDANCE_NEW_CSV = "attendance_new.csv"
DEVICE_BINDING_CSV = "device_binding.csv"
LOG_CSV = "activity_log.csv"

# ------------------------------
# CSV helpers
def load_students_new():
    try:
        df = pd.read_csv(STUDENTS_NEW_CSV)
        expected = ["rollnumber", "studentname", "branch"]
        for col in expected:
            if col not in df.columns:
                df[col] = ""
        return df[expected]
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "studentname", "branch"])
        df.to_csv(STUDENTS_NEW_CSV, index=False)
        return df

def save_students_new(df):
    df.to_csv(STUDENTS_NEW_CSV, index=False)

def load_attendance_new():
    try:
        df = pd.read_csv(ATTENDANCE_NEW_CSV)
        expected = ["rollnumber", "studentname", "timestamp", "datestamp"]
        for col in expected:
            if col not in df.columns:
                df[col] = ""
        return df[expected]
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
        df.to_csv(ATTENDANCE_NEW_CSV, index=False)
        return df

def load_device_binding():
    try:
        df = pd.read_csv(DEVICE_BINDING_CSV)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "device_id", "bound_at"])
        df.to_csv(DEVICE_BINDING_CSV, index=False)
        return df

def save_device_binding(df):
    df.to_csv(DEVICE_BINDING_CSV, index=False)

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
    except Exception as _:
        pass

# ------------------------------
# QR Code generation
def generate_qr_code():
    """Generate QR code with 20-second expiry token"""
    current_timestamp = int(time.time())
    access_token = f"qr_{current_timestamp}"
    
    # QR URL with timestamp token
    qr_url = f"https://smartapp12.streamlit.app?access={access_token}"
    
    # Create QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to bytes
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    # Convert to base64
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
    
    # QR Code Generation Section
    st.markdown("### 📱 Generate QR Code")
    st.info("⏱️ **QR codes expire in 20 seconds.** Generate a new one for each attendance session.")
    
    if st.button("🔲 Generate New QR Code", type="primary", key="gen_qr_btn"):
        qr_img, qr_url, timestamp = generate_qr_code()
        st.success("✅ QR Code generated successfully!")
        st.info(f"**Valid for 20 seconds** | Generated at: {datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')}")
    
    # Display active QR code
    if st.session_state.qr_code_active and st.session_state.qr_code_data:
        st.markdown("### 📱 Active QR Code")
        
        # Calculate time remaining
        current_time = int(time.time())
        time_elapsed = current_time - st.session_state.qr_generated_time
        time_remaining = 20 - time_elapsed
        
        if time_remaining > 0:
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(f'<img src="data:image/png;base64,{st.session_state.qr_code_data}" width="300"/>', unsafe_allow_html=True)
            with col2:
                st.metric("⏱️ Time Remaining", f"{time_remaining}s", delta=None)
                st.success("✅ QR Code Active")
                st.caption(f"Expires at: {datetime.fromtimestamp(st.session_state.qr_generated_time + 20).strftime('%H:%M:%S')}")
        else:
            st.error("❌ QR Code Expired")
            st.warning(f"Expired {abs(time_remaining)} seconds ago. Generate a new one.")
        
        if st.button("🗑️ Clear QR Code"):
            st.session_state.qr_code_active = False
            st.session_state.qr_code_data = None
            st.rerun()
    
    st.markdown("---")
    
    # Tabs for management
    tabs = st.tabs(["👥 Manage Students", "📊 View Attendance", "📱 Device Bindings", "📋 Activity Logs"])
    
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
        
        if st.button("➕ Add Student", key="add_student_btn"):
            if new_roll and new_name and new_branch:
                students_df = load_students_new()
                if new_roll.lower() in students_df['rollnumber'].str.lower().values:
                    st.warning(f"⚠️ Student '{new_roll}' already exists!")
                else:
                    new_student = pd.DataFrame([{
                        'rollnumber': new_roll.strip(),
                        'studentname': new_name.strip(),
                        'branch': new_branch.strip()
                    }])
                    students_df = pd.concat([students_df, new_student], ignore_index=True)
                    save_students_new(students_df)
                    st.success(f"✅ Student '{new_name}' added!")
                    log_action("add_student", new_roll)
                    st.rerun()
            else:
                st.warning("⚠️ Please fill all fields")
        
        st.markdown("---")
        st.markdown("### 👥 All Students")
        
        students_df = load_students_new()
        if not students_df.empty:
            st.dataframe(students_df, width=1000)
            st.info(f"**Total Students:** {len(students_df)}")
            
            # Download
            csv = students_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Download Students List",
                csv,
                "students_new.csv",
                "text/csv",
                key="download_students"
            )
            
            # Delete
            st.markdown("### 🗑️ Remove Student")
            to_delete = st.selectbox("Select student:", [""] + students_df['rollnumber'].tolist(), key="del_student")
            if to_delete and st.button("🗑️ Remove", key="del_btn"):
                students_df = students_df[students_df['rollnumber'] != to_delete]
                save_students_new(students_df)
                st.success(f"✅ Removed '{to_delete}'")
                log_action("delete_student", to_delete)
                st.rerun()
        else:
            st.info("No students added yet.")
    
    # TAB 2: View Attendance
    with tabs[1]:
        st.markdown("### 📊 Attendance Records")
        
        attendance_df = load_attendance_new()
        today = date.today().isoformat()
        
        # Today's attendance
        today_att = attendance_df[attendance_df['datestamp'] == today]
        if not today_att.empty:
            st.success(f"📅 **Today's Attendance ({today})**")
            st.dataframe(today_att, width=1000)
            st.info(f"**Present Today:** {len(today_att)} students")
            
            csv = today_att.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Download Today's Attendance",
                csv,
                f"attendance_{today}.csv",
                "text/csv",
                key="download_today"
            )
        else:
            st.info("No attendance marked today yet.")
        
        st.markdown("---")
        
        # All attendance
        if not attendance_df.empty:
            st.markdown("### 📋 All Attendance Records")
            st.dataframe(attendance_df, width=1000)
            st.info(f"**Total Records:** {len(attendance_df)}")
            
            csv_all = attendance_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "⬇️ Download All Records",
                csv_all,
                "attendance_new_all.csv",
                "text/csv",
                key="download_all"
            )
        else:
            st.info("No attendance records yet.")
    
    # TAB 3: Device Bindings
    with tabs[2]:
        st.markdown("### 📱 Device Bindings")
        st.info("Each student can only mark attendance from one registered device.")
        
        device_df = load_device_binding()
        if not device_df.empty:
            st.dataframe(device_df, width=1000)
            st.info(f"**Total Devices Bound:** {len(device_df)}")
            
            # Unbind device
            st.markdown("### 🔓 Unbind Device")
            st.warning("⚠️ Use this to allow a student to use a different device.")
            to_unbind = st.selectbox("Select student:", [""] + device_df['rollnumber'].tolist(), key="unbind_select")
            if to_unbind and st.button("🔓 Unbind Device", key="unbind_btn"):
                device_df = device_df[device_df['rollnumber'] != to_unbind]
                save_device_binding(device_df)
                st.success(f"✅ Device unbound for '{to_unbind}'. They can now use a new device.")
                log_action("unbind_device", to_unbind)
                st.rerun()
        else:
            st.info("No devices bound yet.")
    
    # TAB 4: Activity Logs
    with tabs[3]:
        st.markdown("### 📋 Recent Activity")
        
        if Path(LOG_CSV).exists():
            log_df = pd.read_csv(LOG_CSV)
            st.dataframe(log_df.tail(100).sort_values("timestamp", ascending=False), width=1000)
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
        st.info("👈 Please login from the sidebar to access the admin panel.")
        
        # Welcome screen
        st.markdown('<div class="header">🎯 Smart QR Attendance System</div>', unsafe_allow_html=True)
        st.markdown("""
        ### Features:
        - ✅ **20-second QR expiry** - Secure, time-limited access
        - ✅ **Location verification** - Students must be at college
        - ✅ **Device binding** - One device per student
        - ✅ **Real-time tracking** - Instant attendance records
        - ✅ **Easy management** - Add/remove students, view reports
        
        **Login as admin to get started!**
        """)

if __name__ == "__main__":
    main()