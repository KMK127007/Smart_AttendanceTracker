import streamlit as st
import pandas as pd
from datetime import datetime, date
import time
import os
import json
import uuid
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
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
# College location (SNIST)
COLLEGE_LATITUDE = 17.4553223
COLLEGE_LONGITUDE = 78.6664965
ALLOWED_RADIUS_METERS = 500

# File paths
STUDENTS_NEW_CSV = "students_new.csv"
ATTENDANCE_NEW_CSV = "attendance_new.csv"
DEVICE_BINDING_CSV = "device_binding.csv"
QR_SETTINGS_FILE = "qr_settings.json"

# ------------------------------
# Session state
for key, default in {
    "admin_logged_app1": False,
    "qr_access_granted": False,
    "location_verified": False,
    "show_location_form": False,
    "device_fingerprint": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ------------------------------
# Load QR settings saved by app12
def load_qr_settings():
    try:
        with open(QR_SETTINGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        # Default: no location check
        return {"location_enabled": False, "window_seconds": 60}

# ------------------------------
# CSV helpers
def load_students():
    try:
        df = pd.read_csv(STUDENTS_NEW_CSV)
        # Auto detect rollnumber column
        if 'rollnumber' not in df.columns:
            for col in df.columns:
                if 'roll' in col.lower():
                    df = df.rename(columns={col: 'rollnumber'})
                    break
        return df
    except FileNotFoundError:
        return pd.DataFrame(columns=["rollnumber"])
    except Exception:
        return pd.DataFrame(columns=["rollnumber"])

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

# ------------------------------
# Device fingerprint
def get_device_fingerprint():
    if not st.session_state.device_fingerprint:
        st.session_state.device_fingerprint = str(uuid.uuid4())
    return st.session_state.device_fingerprint

def check_device_binding(rollnumber):
    device_id = get_device_fingerprint()
    device_df = load_device_binding()

    existing = device_df[device_df['rollnumber'].str.lower() == rollnumber.lower()]

    if existing.empty:
        # First time - bind device
        new_binding = pd.DataFrame([{
            'rollnumber': rollnumber.lower(),
            'device_id': device_id,
            'bound_at': datetime.now().isoformat()
        }])
        device_df = pd.concat([device_df, new_binding], ignore_index=True)
        save_device_binding(device_df)
        return True, "✅ Device registered"
    else:
        bound_device = existing.iloc[0]['device_id']
        if bound_device == device_id:
            return True, "✅ Device verified"
        else:
            return False, "❌ This roll number is already registered on another device. Contact admin to unbind your device."

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
                # Each QR is valid for 30 seconds (refresh window)
                if time_elapsed <= 30:
                    st.session_state.qr_access_granted = True
                    return True, None
                else:
                    return False, f"⏰ QR Code expired! ({time_elapsed}s old). Ask admin to show the latest QR."
            except Exception:
                return False, "Invalid QR format"
    if st.session_state.qr_access_granted:
        return True, None
    return False, "No valid QR scanned. Please scan the QR code shown by your admin."

# ------------------------------
# Mark attendance
def mark_attendance(rollnumber):
    students_df = load_students()

    if 'rollnumber' not in students_df.columns or students_df.empty:
        return False, "❌ Student database not loaded. Contact admin."

    # Strip and compare rollnumber only
    students_df['rollnumber'] = students_df['rollnumber'].astype(str).str.strip()
    match = students_df[students_df['rollnumber'].str.lower() == rollnumber.strip().lower()]

    if match.empty:
        return False, f"❌ Roll number '{rollnumber}' not found in database. Check your roll number or contact admin."

    # Device check
    device_ok, device_msg = check_device_binding(rollnumber)
    if not device_ok:
        return False, device_msg

    # Check already marked today
    attendance_df = load_attendance()
    today = date.today().isoformat()

    if not attendance_df.empty:
        already = attendance_df[
            (attendance_df['rollnumber'].str.lower() == rollnumber.strip().lower()) &
            (attendance_df['datestamp'] == today)
        ]
        if not already.empty:
            return False, "⚠️ Attendance already marked today!"

    # Save attendance
    new_entry = pd.DataFrame([{
        'rollnumber': rollnumber.strip(),
        'timestamp': datetime.now().strftime("%H:%M:%S"),
        'datestamp': today
    }])
    attendance_df = pd.concat([attendance_df, new_entry], ignore_index=True)
    attendance_df.to_csv(ATTENDANCE_NEW_CSV, index=False)

    return True, "✅ Attendance marked successfully!"

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
# QR Student Portal
def qr_student_portal():
    st.markdown('<div class="header">📱 QR Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")

    with st.container():
        st.markdown("Enter your Roll Number to mark attendance.")

        rollnumber = st.text_input(
            "Roll Number",
            key="qr_roll",
            placeholder="e.g. 22311a1965"
        )

        if st.button("✅ Mark Attendance", key="mark_btn", type="primary"):
            if rollnumber.strip():
                with st.spinner("Marking attendance..."):
                    success, message = mark_attendance(rollnumber)
                if success:
                    st.success(message)
                    st.balloons()
                    st.info(f"**Roll Number:** {rollnumber.strip()} | **Time:** {datetime.now().strftime('%H:%M:%S')} | **Date:** {date.today().isoformat()}")
                else:
                    st.error(message)
            else:
                st.warning("⚠️ Please enter your Roll Number")

    st.markdown("---")
    st.info("💡 Enter only your Roll Number and click Mark Attendance")

    # Admin section at bottom
    st.markdown("---")
    st.markdown("### 🔐 Admin Access")

    if not st.session_state.admin_logged_app1:
        with st.expander("🔑 Admin Login"):
            u = st.text_input("Username", key="adm_user")
            p = st.text_input("Password", type="password", key="adm_pass")
            if st.button("Login", key="adm_login_btn"):
                if u in ADMINS and ADMINS[u]["password"] == p:
                    st.session_state.admin_logged_app1 = True
                    st.session_state.qr_access_granted = True
                    st.success("✅ Logged in!")
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
    else:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success("✅ Admin logged in")
        with col2:
            if st.button("🚪 Logout", key="adm_logout"):
                st.session_state.admin_logged_app1 = False
                st.rerun()

        st.markdown("---")
        admin_tabs = st.tabs(["📊 Today's Attendance", "📋 All Records"])

        with admin_tabs[0]:
            today = date.today().isoformat()
            att_df = load_attendance()
            today_df = att_df[att_df['datestamp'] == today] if not att_df.empty else pd.DataFrame()

            if not today_df.empty:
                st.success(f"📅 Today ({today}) - **{len(today_df)} present**")
                st.dataframe(today_df, width=800)
                csv = today_df.to_csv(index=False).encode('utf-8')
                st.download_button("⬇️ Download Today's", csv, f"attendance_{today}.csv", "text/csv", key="dl_today")
            else:
                st.info("No attendance today yet.")

        with admin_tabs[1]:
            att_df = load_attendance()
            if not att_df.empty:
                st.dataframe(att_df, width=800)
                st.info(f"**Total Records:** {len(att_df)}")
                csv_all = att_df.to_csv(index=False).encode('utf-8')
                st.download_button("⬇️ Download All", csv_all, "attendance_all.csv", "text/csv", key="dl_all")
            else:
                st.info("No records yet.")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker - QR Portal | Powered by Streamlit")

# ------------------------------
# Main
def main():
    st.set_page_config(
        page_title="QR Attendance Portal",
        page_icon="📱",
        layout="centered"
    )

    # Admin always bypasses QR check
    if st.session_state.admin_logged_app1:
        qr_student_portal()
        return

    # Check QR access for students
    access_valid, error_msg = check_qr_access()

    if not access_valid:
        st.error("🔒 **Access Denied**")
        if error_msg:
            st.warning(f"{error_msg}")
        st.info("📱 Ask your admin to generate a QR code and scan it within 30 seconds.")

        st.markdown("---")
        st.markdown("### 🔐 Admin Login")
        with st.expander("🔑 Admin Login (No time restriction)"):
            u = st.text_input("Username", key="blocked_adm_user")
            p = st.text_input("Password", type="password", key="blocked_adm_pass")
            if st.button("Login", key="blocked_adm_btn"):
                if u in ADMINS and ADMINS[u]["password"] == p:
                    st.session_state.admin_logged_app1 = True
                    st.session_state.qr_access_granted = True
                    st.success("✅ Logged in!")
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
        st.stop()

    # Load QR settings to check if location is required
    settings = load_qr_settings()
    location_required = settings.get("location_enabled", False)

    # Location check (only if admin enabled it)
    if location_required and not st.session_state.location_verified:
        st.success("✅ QR Code verified!")
        st.markdown("### 📍 Location Verification Required")
        st.info("Your admin has enabled location verification.\nYou must be within 500m of SNIST to mark attendance.")

        if st.button("📍 Verify My Location", type="primary", key="loc_btn"):
            st.session_state.show_location_form = True

        if st.session_state.show_location_form:
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                user_lat = st.number_input("Latitude", value=17.4553, format="%.6f", key="lat_input")
            with col2:
                user_lon = st.number_input("Longitude", value=78.6665, format="%.6f", key="lon_input")

            if st.button("✅ Confirm Location", type="primary", key="confirm_loc"):
                in_range, distance = check_location_in_range(user_lat, user_lon)
                if in_range:
                    st.session_state.location_verified = True
                    st.success(f"✅ Location verified! You are {int(distance)}m from college.")
                    st.rerun()
                else:
                    st.error(f"❌ You are {int(distance)}m away. Must be within {ALLOWED_RADIUS_METERS}m of SNIST.")
        st.stop()

    # All checks passed - show portal
    if location_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR Code verified!")

    qr_student_portal()

if __name__ == "__main__":
    main()
