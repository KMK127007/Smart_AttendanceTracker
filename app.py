import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import qrcode
from io import BytesIO
import base64
import os
import json
from pathlib import Path
import warnings

warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

# ------------------------------
# Load secrets
try:
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as e:
    st.error(f"Missing secret: {e}")
    st.stop()

# ------------------------------
# CSS
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
# File paths
STUDENTS_NEW_CSV = "students_new.csv"
ATTENDANCE_NEW_CSV = "attendance_new.csv"
DEVICE_BINDING_CSV = "device_binding.csv"
LOG_CSV = "activity_log.csv"
QR_SETTINGS_FILE = "qr_settings.json"

# ------------------------------
# Session state
for key, default in {
    "admin_logged": False,
    "admin_user": None,
    "qr_active": False,
    "qr_start_time": None,
    "qr_window_seconds": 60,
    "qr_location_enabled": False,
    "qr_current_token": None,
    "qr_current_image": None,
    "qr_last_refresh": None,
    "students_uploaded": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------
# CSV Helpers
def load_students():
    try:
        df = pd.read_csv(STUDENTS_NEW_CSV)
        if 'rollnumber' not in df.columns:
            # Try to find a column that looks like rollnumber
            for col in df.columns:
                if 'roll' in col.lower():
                    df = df.rename(columns={col: 'rollnumber'})
                    break
        return df
    except FileNotFoundError:
        return pd.DataFrame(columns=["rollnumber"])
    except Exception as e:
        st.error(f"Error loading students: {e}")
        return pd.DataFrame(columns=["rollnumber"])

def save_students(df):
    df.to_csv(STUDENTS_NEW_CSV, index=False)

def load_attendance():
    try:
        df = pd.read_csv(ATTENDANCE_NEW_CSV)
        expected = ["rollnumber", "timestamp", "datestamp"]
        for col in expected:
            if col not in df.columns:
                df[col] = ""
        return df[expected]
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "timestamp", "datestamp"])
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
    except Exception:
        pass

# ------------------------------
# Save QR settings to file so app1.py can read them
def save_qr_settings(location_enabled, window_seconds):
    settings = {
        "location_enabled": location_enabled,
        "window_seconds": window_seconds,
        "updated_at": datetime.now().isoformat()
    }
    with open(QR_SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

# ------------------------------
# QR Code generation
def generate_single_qr(token):
    """Generate a single QR code image for given token"""
    qr_url = f"https://smartapp12.streamlit.app?access={token}"

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
    return base64.b64encode(buffer.getvalue()).decode()

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
        st.session_state.qr_active = False
        log_action("admin_logout", "")
        st.rerun()

# ------------------------------
# Admin panel
def admin_panel():
    st.markdown('<div class="header">🎯 QR Attendance System - Admin Panel</div>', unsafe_allow_html=True)
    st.write(f"Logged in as: **{st.session_state.admin_user}**")
    st.markdown("---")

    tabs = st.tabs(["📱 Generate QR", "👥 Manage Students", "📊 View Attendance", "✍️ Manual Attendance", "📱 Device Bindings", "📋 Logs"])

    # ─────────────────────────────────────────────
    # TAB 1: Generate QR
    # ─────────────────────────────────────────────
    with tabs[0]:
        st.markdown("### 📱 QR Code Generator")

        # STEP 1: Upload Students CSV
        st.markdown("#### 📂 Step 1: Upload Students CSV")
        st.info("Upload your `students_new.csv` file. It must contain a **rollnumber** column.")

        uploaded_file = st.file_uploader("Upload Students CSV", type=["csv"], key="students_uploader")
        if uploaded_file is not None:
            try:
                df_uploaded = pd.read_csv(uploaded_file)
                # Auto-detect rollnumber column
                roll_col = None
                for col in df_uploaded.columns:
                    if 'roll' in col.lower():
                        roll_col = col
                        break

                if roll_col is None:
                    st.error("❌ No 'rollnumber' column found! Please make sure your CSV has a rollnumber column.")
                else:
                    if roll_col != 'rollnumber':
                        df_uploaded = df_uploaded.rename(columns={roll_col: 'rollnumber'})
                    df_uploaded.to_csv(STUDENTS_NEW_CSV, index=False)
                    st.session_state.students_uploaded = True
                    st.success(f"✅ CSV uploaded! Found **{len(df_uploaded)}** student records.")
                    st.dataframe(df_uploaded[['rollnumber']].head(5), width=400)
                    st.caption(f"Showing first 5 of {len(df_uploaded)} records")
            except Exception as e:
                st.error(f"Error reading CSV: {e}")

        # Show current students count
        current_students = load_students()
        if not current_students.empty and 'rollnumber' in current_students.columns:
            st.success(f"📋 **Current database:** {len(current_students)} students loaded")
            st.session_state.students_uploaded = True
        else:
            st.warning("⚠️ No students loaded yet. Please upload CSV first.")

        st.markdown("---")

        # STEP 2: QR Settings
        st.markdown("#### ⚙️ Step 2: Configure QR Settings")

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**⏱️ QR Time Window**")
            time_options = {
                "1 minute": 60,
                "3 minutes": 180,
                "5 minutes": 300,
                "10 minutes": 600,
                "15 minutes": 900,
                "30 minutes": 1800,
            }
            selected_time = st.selectbox(
                "How long should QR be active?",
                options=list(time_options.keys()),
                index=0,
                key="time_window_select",
                help="QR code will auto-refresh every 30 seconds within this window"
            )
            selected_seconds = time_options[selected_time]
            st.caption(f"QR will refresh every 30 seconds for {selected_time}")

        with col2:
            st.markdown("**📍 Location Verification**")
            location_enabled = st.toggle(
                "Enable Location Check",
                value=False,
                key="location_toggle",
                help="If enabled, students must be within 500m of SNIST to mark attendance"
            )
            if location_enabled:
                st.success("📍 Location check **ENABLED**")
                st.info("📌 College: SNIST\n\nLat: 17.4553223\nLon: 78.6664965\nRadius: 500m")
            else:
                st.info("📍 Location check **DISABLED**\nStudents can mark from anywhere")

        st.markdown("---")

        # STEP 3: Generate QR
        st.markdown("#### 🔲 Step 3: Generate QR Code")

        if not st.session_state.students_uploaded and current_students.empty:
            st.warning("⚠️ Please upload students CSV first (Step 1)")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("🔲 Start QR Session", type="primary", key="start_qr_btn"):
                    # Save settings for app1.py to read
                    save_qr_settings(location_enabled, selected_seconds)

                    # Initialize QR session
                    current_timestamp = int(time.time())
                    token = f"qr_{current_timestamp}"

                    st.session_state.qr_active = True
                    st.session_state.qr_start_time = current_timestamp
                    st.session_state.qr_window_seconds = selected_seconds
                    st.session_state.qr_location_enabled = location_enabled
                    st.session_state.qr_current_token = token
                    st.session_state.qr_current_image = generate_single_qr(token)
                    st.session_state.qr_last_refresh = current_timestamp

                    log_action("start_qr_session", f"Window: {selected_time}, Location: {location_enabled}")
                    st.rerun()

            with col2:
                if st.session_state.qr_active:
                    if st.button("⏹️ Stop QR Session", key="stop_qr_btn"):
                        st.session_state.qr_active = False
                        st.session_state.qr_current_image = None
                        log_action("stop_qr_session", "")
                        st.rerun()

        # ── Active QR Display ──
        if st.session_state.qr_active:
            current_time = int(time.time())
            total_elapsed = current_time - st.session_state.qr_start_time
            time_remaining_total = st.session_state.qr_window_seconds - total_elapsed

            # Check if overall session expired
            if time_remaining_total <= 0:
                st.error("⏰ QR Session Expired! Start a new session.")
                st.session_state.qr_active = False
                st.rerun()

            # Check if QR needs refresh (every 30 seconds)
            time_since_refresh = current_time - st.session_state.qr_last_refresh
            if time_since_refresh >= 30:
                new_token = f"qr_{current_time}"
                st.session_state.qr_current_token = new_token
                st.session_state.qr_current_image = generate_single_qr(new_token)
                st.session_state.qr_last_refresh = current_time
                log_action("qr_refresh", f"New token at {current_time}")

            # Display QR
            st.markdown("---")
            st.markdown("### 📱 Active QR Code")

            # Status bar
            mins_remaining = int(time_remaining_total // 60)
            secs_remaining = int(time_remaining_total % 60)
            next_refresh_in = 30 - time_since_refresh

            col1, col2 = st.columns([2, 1])
            with col1:
                if st.session_state.qr_current_image:
                    st.markdown(
                        f'<img src="data:image/png;base64,{st.session_state.qr_current_image}" width="280"/>',
                        unsafe_allow_html=True
                    )
            with col2:
                st.metric("⏱️ Session Remaining", f"{mins_remaining}m {secs_remaining}s")
                st.metric("🔄 Next QR Refresh", f"{int(next_refresh_in)}s")
                if st.session_state.qr_location_enabled:
                    st.success("📍 Location: ON")
                else:
                    st.info("📍 Location: OFF")
                st.caption(f"QR refreshes every 30s\nSession window: {selected_time}")

            # Auto-refresh page every 10 seconds to update timers
            st.markdown("""
                <meta http-equiv="refresh" content="10">
            """, unsafe_allow_html=True)

    # ─────────────────────────────────────────────
    # TAB 2: Manage Students
    # ─────────────────────────────────────────────
    with tabs[1]:
        st.markdown("### 👥 Manage Students")

        students_df = load_students()
        if not students_df.empty:
            st.success(f"**Total Students:** {len(students_df)}")
            st.dataframe(students_df, width=1000)

            csv = students_df.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download Students List", csv, "students_new.csv", "text/csv", key="dl_students")

            st.markdown("---")
            st.markdown("### ➕ Add Single Student")
            new_roll = st.text_input("Roll Number", key="add_roll")
            if st.button("➕ Add", key="add_single_btn"):
                if new_roll:
                    if new_roll.lower() in students_df['rollnumber'].str.lower().values:
                        st.warning(f"⚠️ '{new_roll}' already exists!")
                    else:
                        new_row = pd.DataFrame([{'rollnumber': new_roll.strip()}])
                        students_df = pd.concat([students_df, new_row], ignore_index=True)
                        save_students(students_df)
                        st.success(f"✅ Added '{new_roll}'")
                        log_action("add_student", new_roll)
                        st.rerun()
                else:
                    st.warning("Enter roll number")

            st.markdown("### 🗑️ Remove Student")
            to_del = st.selectbox("Select:", [""] + students_df['rollnumber'].tolist(), key="del_sel")
            if to_del and st.button("🗑️ Remove", key="del_btn"):
                students_df = students_df[students_df['rollnumber'] != to_del]
                save_students(students_df)
                st.success(f"✅ Removed '{to_del}'")
                log_action("delete_student", to_del)
                st.rerun()
        else:
            st.info("No students loaded. Upload CSV in 'Generate QR' tab.")

    # ─────────────────────────────────────────────
    # TAB 3: View Attendance
    # ─────────────────────────────────────────────
    with tabs[2]:
        st.markdown("### 📊 Attendance Records")
        attendance_df = load_attendance()
        today = date.today().isoformat()

        today_df = attendance_df[attendance_df['datestamp'] == today] if not attendance_df.empty else pd.DataFrame()

        if not today_df.empty:
            st.success(f"📅 **Today ({today}) - {len(today_df)} present**")
            st.dataframe(today_df, width=1000)
            csv = today_df.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download Today's", csv, f"attendance_{today}.csv", "text/csv", key="dl_today")
        else:
            st.info("No attendance today yet.")

        st.markdown("---")

        if not attendance_df.empty:
            st.markdown("### 📋 All Records")
            st.dataframe(attendance_df, width=1000)
            st.info(f"**Total Records:** {len(attendance_df)}")
            csv_all = attendance_df.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Download All", csv_all, "attendance_all.csv", "text/csv", key="dl_all")
        else:
            st.info("No attendance records yet.")

    # ─────────────────────────────────────────────
    # TAB 4: Manual Attendance
    # ─────────────────────────────────────────────
    with tabs[3]:
        st.markdown("### ✍️ Mark Attendance Manually")
        st.info("💡 Use this if a student missed the QR scan.")

        students_df = load_students()
        attendance_df = load_attendance()

        if not students_df.empty and 'rollnumber' in students_df.columns:
            st.markdown("#### Select Student")
            sel_roll = st.selectbox("Select Roll Number:", [""] + students_df['rollnumber'].tolist(), key="man_sel")
            man_date = st.date_input("Date", value=date.today(), key="man_date")

            if sel_roll and st.button("✅ Mark Attendance", type="primary", key="man_btn"):
                date_str = man_date.isoformat()
                already = attendance_df[
                    (attendance_df['rollnumber'].str.lower() == sel_roll.lower()) &
                    (attendance_df['datestamp'] == date_str)
                ] if not attendance_df.empty else pd.DataFrame()

                if not already.empty:
                    st.warning(f"⚠️ Already marked for {sel_roll} on {date_str}")
                else:
                    new_entry = pd.DataFrame([{
                        'rollnumber': sel_roll,
                        'timestamp': datetime.now().strftime("%H:%M:%S"),
                        'datestamp': date_str
                    }])
                    attendance_df = pd.concat([attendance_df, new_entry], ignore_index=True)
                    attendance_df.to_csv(ATTENDANCE_NEW_CSV, index=False)
                    st.success(f"✅ Attendance marked for **{sel_roll}** on **{date_str}**!")
                    log_action("manual_attendance", f"{sel_roll} - {date_str}")
                    st.rerun()

            st.markdown("---")
            st.markdown("#### ➕ Manual Entry (Roll not in list)")
            m_roll = st.text_input("Roll Number", key="m_roll")
            m_date = st.date_input("Date", value=date.today(), key="m_date")

            if st.button("✅ Mark Manually", key="m_btn"):
                if m_roll:
                    date_str = m_date.isoformat()
                    already = attendance_df[
                        (attendance_df['rollnumber'].str.lower() == m_roll.lower()) &
                        (attendance_df['datestamp'] == date_str)
                    ] if not attendance_df.empty else pd.DataFrame()

                    if not already.empty:
                        st.warning(f"⚠️ Already marked for {m_roll} on {date_str}")
                    else:
                        new_entry = pd.DataFrame([{
                            'rollnumber': m_roll.strip(),
                            'timestamp': datetime.now().strftime("%H:%M:%S"),
                            'datestamp': date_str
                        }])
                        attendance_df = pd.concat([attendance_df, new_entry], ignore_index=True)
                        attendance_df.to_csv(ATTENDANCE_NEW_CSV, index=False)
                        st.success(f"✅ Marked for **{m_roll}** on **{date_str}**!")
                        log_action("manual_attendance_custom", f"{m_roll} - {date_str}")
                        st.rerun()
                else:
                    st.warning("Enter roll number")
        else:
            st.info("No students loaded yet.")

    # ─────────────────────────────────────────────
    # TAB 5: Device Bindings
    # ─────────────────────────────────────────────
    with tabs[4]:
        st.markdown("### 📱 Device Bindings")
        st.info("One device per student.")
        device_df = load_device_binding()

        if not device_df.empty:
            st.dataframe(device_df, width=1000)
            st.info(f"**Bound Devices:** {len(device_df)}")

            st.markdown("### 🔓 Unbind Device")
            to_unbind = st.selectbox("Select:", [""] + device_df['rollnumber'].tolist(), key="unbind_sel")
            if to_unbind and st.button("🔓 Unbind", key="unbind_btn"):
                device_df = device_df[device_df['rollnumber'] != to_unbind]
                save_device_binding(device_df)
                st.success(f"✅ Unbound '{to_unbind}'")
                log_action("unbind_device", to_unbind)
                st.rerun()
        else:
            st.info("No devices bound yet.")

    # ─────────────────────────────────────────────
    # TAB 6: Logs
    # ─────────────────────────────────────────────
    with tabs[5]:
        st.markdown("### 📋 Activity Logs")
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
        st.markdown('<div class="header">🎯 Smart QR Attendance System</div>', unsafe_allow_html=True)
        st.markdown("""
        ### Features:
        - ✅ **Upload student CSV** before QR generation
        - ✅ **Custom time window** - 1, 3, 5, 10, 15, 30 minutes
        - ✅ **Auto-refreshing QR** every 30 seconds
        - ✅ **Optional location check** (SNIST)
        - ✅ **Single device per student**
        - ✅ **Manual attendance** override
        
        **👈 Login from sidebar to get started!**
        """)

if __name__ == "__main__":
    main()
