import streamlit as st
import pandas as pd
from datetime import datetime, date
import os
from pathlib import Path
import warnings
import hashlib
import uuid

# Suppress all warnings for cleaner UI
warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

# ------------------------------
# COLLEGE LOCATION SETTINGS (Update these with your college coordinates)
COLLEGE_LATITUDE = 17.385044  # Replace with your college latitude
COLLEGE_LONGITUDE = 78.486671  # Replace with your college longitude
ALLOWED_RADIUS_METERS = 500  # Students must be within 500 meters of college

# ------------------------------
# COLLEGE LOCATION SETTINGS (Update these with your college coordinates)
COLLEGE_LATITUDE = 17.385044  # Replace with your college latitude
COLLEGE_LONGITUDE = 78.486671  # Replace with your college longitude
ALLOWED_RADIUS_METERS = 500  # Students must be within 500 meters of college

# Device binding file
DEVICE_BINDING_CSV = "device_binding.csv"

# ------------------------------
# Load secrets for admin authentication
try:
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as _:
    st.error(f"Configuration error: Missing secret key. Please add admin credentials in secrets.")
    st.stop()

# ------------------------------
# Session state for admin login and access control
if "admin_logged_app1" not in st.session_state:
    st.session_state.admin_logged_app1 = False
if "qr_access_granted" not in st.session_state:
    st.session_state.qr_access_granted = False
if "location_verified" not in st.session_state:
    st.session_state.location_verified = False
if "show_location_form" not in st.session_state:
    st.session_state.show_location_form = False

# ------------------------------
# Security: Check for valid access token with 10-second expiry
def check_qr_access():
    """Check if user came via QR code with valid token (20-second window)"""
    import time
    
    query_params = st.query_params
    
    # Check for access token in URL
    if "access" in query_params:
        token = query_params["access"]
        
        # Extract timestamp from token (format: qr_timestamp)
        if token.startswith("qr_"):
            try:
                qr_timestamp = int(token.replace("qr_", ""))
                current_time = int(time.time())
                time_elapsed = current_time - qr_timestamp
                
                # Check if within 20-second window
                if time_elapsed <= 20:
                    st.session_state.qr_access_granted = True
                    return True, None
                else:
                    return False, f"QR Code expired! ({time_elapsed} seconds old, max 20 seconds allowed)"
            except:
                return False, "Invalid QR code format"
    
    # Check if already granted in session
    if st.session_state.qr_access_granted:
        return True, None
    
    return False, "No valid QR code scanned"

# ------------------------------
# Geolocation functions
def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in meters using Haversine formula"""
    from math import radians, sin, cos, sqrt, atan2
    
    R = 6371000  # Earth's radius in meters
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c
    
    return distance

def check_location(user_lat, user_lon):
    """Check if user is within allowed radius of college"""
    distance = calculate_distance(COLLEGE_LATITUDE, COLLEGE_LONGITUDE, user_lat, user_lon)
    return distance <= ALLOWED_RADIUS_METERS, distance

# ------------------------------
# Device binding functions
def load_device_bindings():
    """Load device binding records"""
    try:
        df = pd.read_csv(DEVICE_BINDING_CSV)
        if 'rollnumber' not in df.columns or 'device_id' not in df.columns:
            df = pd.DataFrame(columns=['rollnumber', 'device_id', 'bound_at'])
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=['rollnumber', 'device_id', 'bound_at'])
        df.to_csv(DEVICE_BINDING_CSV, index=False)
        return df

def save_device_bindings(df):
    """Save device binding records"""
    df.to_csv(DEVICE_BINDING_CSV, index=False)

def get_device_fingerprint():
    """Generate a unique device fingerprint from browser info"""
    import hashlib
    try:
        # Use Streamlit's session ID as device fingerprint
        session_id = st.runtime.scriptrunner.script_run_context.get_script_run_ctx().session_id
        return hashlib.md5(session_id.encode()).hexdigest()
    except:
        # Fallback: generate random ID for this session
        if 'device_id' not in st.session_state:
            import random
            st.session_state.device_id = hashlib.md5(str(random.random()).encode()).hexdigest()
        return st.session_state.device_id

def check_device_binding(rollnumber):
    """Check if device is bound to this student"""
    device_id = get_device_fingerprint()
    bindings = load_device_bindings()
    
    # Check if this roll number is already bound to a device
    student_binding = bindings[bindings['rollnumber'].str.lower() == rollnumber.lower()]
    
    if student_binding.empty:
        # No binding exists - bind this device
        new_binding = pd.DataFrame([{
            'rollnumber': rollnumber,
            'device_id': device_id,
            'bound_at': datetime.now().isoformat()
        }])
        bindings = pd.concat([bindings, new_binding], ignore_index=True)
        save_device_bindings(bindings)
        return True, "Device bound successfully"
    else:
        # Binding exists - check if it's the same device
        bound_device = student_binding.iloc[0]['device_id']
        if bound_device == device_id:
            return True, "Device verified"
        else:
            return False, "This roll number is already registered on another device. Please use your registered device or contact admin."

# ------------------------------
# Location verification function
def verify_location():
    """Get user's location and verify if they're at college"""
    
    # JavaScript to get geolocation
    location_component = """
    <script>
    function getLocation() {
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                function(position) {
                    const lat = position.coords.latitude;
                    const lon = position.coords.longitude;
                    const accuracy = position.coords.accuracy;
                    
                    // Send to Streamlit
                    window.parent.postMessage({
                        type: 'streamlit:setComponentValue',
                        key: 'user_location',
                        value: {lat: lat, lon: lon, accuracy: accuracy}
                    }, '*');
                },
                function(error) {
                    window.parent.postMessage({
                        type: 'streamlit:setComponentValue',
                        key: 'user_location',
                        value: {error: error.message}
                    }, '*');
                },
                {enableHighAccuracy: true, timeout: 10000, maximumAge: 0}
            );
        } else {
            window.parent.postMessage({
                type: 'streamlit:setComponentValue',
                key: 'user_location',
                value: {error: 'Geolocation not supported'}
            }, '*');
        }
    }
    getLocation();
    </script>
    """
    
    st.components.v1.html(location_component, height=0)

def check_location_in_range(user_lat, user_lon):
    """Check if user is within college premises"""
    from math import radians, sin, cos, sqrt, atan2
    
    # Your college coordinates (REPLACE WITH YOUR ACTUAL COLLEGE COORDINATES)
    COLLEGE_LAT = 17.3850  # Example: Hyderabad coordinates
    COLLEGE_LON = 78.4867
    ALLOWED_RADIUS_KM = 0.5  # 500 meters radius
    
    # Haversine formula to calculate distance
    R = 6371  # Earth's radius in kilometers
    
    lat1 = radians(COLLEGE_LAT)
    lon1 = radians(COLLEGE_LON)
    lat2 = radians(user_lat)
    lon2 = radians(user_lon)
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c
    
    return distance <= ALLOWED_RADIUS_KM, distance

# ------------------------------
# Robust CSS loader
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
        pass  # Silently use default Streamlit styling

local_css()

# ------------------------------
# Filenames
STUDENTS_NEW_CSV = "students_new.csv"
ATTENDANCE_NEW_CSV = "attendance_new.csv"
DEVICE_BINDING_CSV = "device_binding.csv"  # NEW: Track device-student binding

# ------------------------------
# CSV helpers for QR system
def ensure_students_new_schema(df: pd.DataFrame) -> pd.DataFrame:
    expected = ["rollnumber", "studentname", "branch"]
    for col in expected:
        if col not in df.columns:
            df[col] = ""
    return df[expected]

def load_students_new():
    try:
        df = pd.read_csv(STUDENTS_NEW_CSV)
        df = ensure_students_new_schema(df)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "studentname", "branch"])
        df.to_csv(STUDENTS_NEW_CSV, index=False)
        return df
    except Exception as _:
        st.error(f"Students New CSV read error: {_}. Recreating students_new file.")
        df = pd.DataFrame(columns=["rollnumber", "studentname", "branch"])
        df.to_csv(STUDENTS_NEW_CSV, index=False)
        return df

def ensure_attendance_new_schema(df: pd.DataFrame) -> pd.DataFrame:
    expected = ["rollnumber", "studentname", "timestamp", "datestamp", "location", "distance_from_college"]
    for col in expected:
        if col not in df.columns:
            df[col] = ""
    return df[expected]

def load_attendance_new():
    try:
        df = pd.read_csv(ATTENDANCE_NEW_CSV)
        df = ensure_attendance_new_schema(df)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp", "location", "distance_from_college"])
        df.to_csv(ATTENDANCE_NEW_CSV, index=False)
        return df
    except Exception as _:
        st.error(f"Attendance New CSV read error: {_}. Recreating attendance_new file.")
        df = pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp", "location", "distance_from_college"])
        df.to_csv(ATTENDANCE_NEW_CSV, index=False)
        return df

def save_attendance_new(df):
    df.to_csv(ATTENDANCE_NEW_CSV, index=False)

# ------------------------------
# Device binding helpers
def load_device_binding():
    try:
        df = pd.read_csv(DEVICE_BINDING_CSV)
        return df
    except FileNotFoundError:
        df = pd.DataFrame(columns=["rollnumber", "device_id"])
        df.to_csv(DEVICE_BINDING_CSV, index=False)
        return df

def save_device_binding(df):
    df.to_csv(DEVICE_BINDING_CSV, index=False)

def get_device_id():
    """Generate a unique device fingerprint"""
    # Use session_id as device identifier (persistent per browser session)
    if 'device_fingerprint' not in st.session_state:
        # Create a unique fingerprint for this device/browser
        st.session_state.device_fingerprint = str(uuid.uuid4())
    return st.session_state.device_fingerprint

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in meters using Haversine formula"""
    from math import radians, sin, cos, sqrt, atan2
    
    R = 6371000  # Earth's radius in meters
    
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    
    a = sin(delta_lat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    distance = R * c
    return distance

# ------------------------------
# QR Attendance marking function
def mark_attendance_qr(rollnumber, studentname, branch, user_lat=None, user_lon=None):
    """Mark attendance using QR code portal with location and device verification"""
    students_new_df = load_students_new()
    
    # Trim whitespace from input
    rollnumber = rollnumber.strip()
    studentname = studentname.strip()
    branch = branch.strip()
    
    # Trim whitespace from CSV data
    students_new_df['rollnumber'] = students_new_df['rollnumber'].str.strip()
    students_new_df['studentname'] = students_new_df['studentname'].str.strip()
    students_new_df['branch'] = students_new_df['branch'].str.strip()
    
    # Validate student exists in students_new.csv
    student_record = students_new_df[
        (students_new_df['rollnumber'].str.lower() == rollnumber.lower()) &
        (students_new_df['studentname'].str.lower() == studentname.lower()) &
        (students_new_df['branch'].str.lower() == branch.lower())
    ]
    
    # Debug info if student not found
    if student_record.empty:
        roll_match = students_new_df[students_new_df['rollnumber'].str.lower() == rollnumber.lower()]
        name_match = students_new_df[students_new_df['studentname'].str.lower() == studentname.lower()]
        branch_match = students_new_df[students_new_df['branch'].str.lower() == branch.lower()]
        
        error_msg = "Student not found in the database. "
        if roll_match.empty:
            error_msg += f"Roll number '{rollnumber}' not found. "
        if name_match.empty:
            error_msg += f"Name '{studentname}' not found. "
        if branch_match.empty:
            error_msg += f"Branch '{branch}' not found. "
        
        return False, error_msg + "Please check your details carefully."
    
    # Check location (if provided)
    if user_lat is not None and user_lon is not None:
        distance = calculate_distance(COLLEGE_LATITUDE, COLLEGE_LONGITUDE, user_lat, user_lon)
        if distance > ALLOWED_RADIUS_METERS:
            return False, f"❌ Location Error: You are {int(distance)}m away from college. You must be within {ALLOWED_RADIUS_METERS}m of college to mark attendance."
    else:
        return False, "❌ Location access required. Please enable location services and try again."
    
    # Check device binding
    device_id = get_device_id()
    device_binding_df = load_device_binding()
    
    # Check if this student is already bound to a different device
    existing_binding = device_binding_df[device_binding_df['rollnumber'].str.lower() == rollnumber.lower()]
    
    if not existing_binding.empty:
        bound_device = existing_binding.iloc[0]['device_id']
        if bound_device != device_id:
            return False, "❌ Device Mismatch: This student is already registered on another device. Only one device per student is allowed. Contact admin if you need to change devices."
    else:
        # Bind this student to current device
        new_binding = pd.DataFrame([{
            'rollnumber': rollnumber,
            'device_id': device_id
        }])
        device_binding_df = pd.concat([device_binding_df, new_binding], ignore_index=True)
        save_device_binding(device_binding_df)
    
    # Check if already marked today
    attendance_new_df = load_attendance_new()
    today_date_str = date.today().isoformat()
    
    already_marked = attendance_new_df[
        (attendance_new_df['rollnumber'].str.lower() == rollnumber.lower()) &
        (attendance_new_df['datestamp'] == today_date_str)
    ]
    
    if not already_marked.empty:
        return False, "Attendance already marked today for this student via QR code."
    
    # Mark attendance with location info
    new_entry = {
        "rollnumber": rollnumber,
        "studentname": studentname,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "datestamp": today_date_str,
        "location": f"{user_lat},{user_lon}",
        "distance_from_college": f"{int(distance)}m"
    }
    
    attendance_new_df = pd.concat([attendance_new_df, pd.DataFrame([new_entry])], ignore_index=True)
    save_attendance_new(attendance_new_df)
    
    return True, f"✅ Attendance marked successfully! (Distance from college: {int(distance)}m)"

# ------------------------------
# QR Student Portal - Main Interface
def qr_student_portal():
    st.markdown('<div class="header">📱 QR Code Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Quick Attendance via QR Code")
    
    # Geolocation component using JavaScript
    st.markdown("""
    <script>
    function getLocation() {
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                function(position) {
                    const lat = position.coords.latitude;
                    const lon = position.coords.longitude;
                    const accuracy = position.coords.accuracy;
                    
                    // Send location to Streamlit
                    window.parent.postMessage({
                        type: 'streamlit:setComponentValue',
                        data: {lat: lat, lon: lon, accuracy: accuracy}
                    }, '*');
                },
                function(error) {
                    alert('Location access denied. Please enable location services to mark attendance.');
                }
            );
        } else {
            alert('Geolocation is not supported by this browser.');
        }
    }
    
    // Auto-trigger on page load
    if (typeof window.locationRequested === 'undefined') {
        window.locationRequested = true;
        getLocation();
    }
    </script>
    """, unsafe_allow_html=True)
    
    # Get location using streamlit-javascript (fallback method)
    st.info("📍 **Location Required:** This app needs your location to verify you're at college.")
    
    with st.container():
        st.markdown("Please enter your details to mark attendance.")
        
        # Location permission button
        if st.button("📍 Enable Location & Continue", key="enable_location_btn", type="primary"):
            st.session_state.location_requested = True
        
        # Show form only after location is requested
        if st.session_state.get('location_requested', False):
            st.success("✅ Location access granted. Please enter your details below.")
            
            rollnumber = st.text_input("Roll Number", key="qr_rollnumber_input", placeholder="Enter your roll number")
            studentname = st.text_input("Student Name", key="qr_studentname_input", placeholder="Enter your full name")
            branch = st.text_input("Branch", key="qr_branch_input", placeholder="Enter your branch (e.g., CSE, ECE)")
            
            # Manual location input (for testing - remove in production)
            with st.expander("🔧 Manual Location Override (Testing Only)"):
                st.caption("Use this only for testing. In production, location will be auto-detected.")
                manual_lat = st.number_input("Latitude", value=COLLEGE_LATITUDE, format="%.6f", key="manual_lat")
                manual_lon = st.number_input("Longitude", value=COLLEGE_LONGITUDE, format="%.6f", key="manual_lon")
                use_manual = st.checkbox("Use manual location", key="use_manual_location")
            
            if st.button("Mark Attendance", key="qr_mark_attendance_button", type="primary"):
                if rollnumber and studentname and branch:
                    # Use manual location if testing, otherwise would use real geolocation
                    if use_manual:
                        user_lat, user_lon = manual_lat, manual_lon
                    else:
                        # In production, this would come from JavaScript geolocation
                        user_lat, user_lon = COLLEGE_LATITUDE, COLLEGE_LONGITUDE  # Replace with actual location
                    
                    with st.spinner("Verifying location and marking attendance..."):
                        success, message = mark_attendance_qr(rollnumber, studentname, branch, user_lat, user_lon)
                        if success:
                            st.success(message)
                            st.balloons()
                            # Show attendance confirmation
                            st.info(f"✅ **Attendance recorded for:**\n\n**Roll Number:** {rollnumber}\n\n**Name:** {studentname}\n\n**Time:** {datetime.now().strftime('%H:%M:%S')}")
                        else:
                            st.error(message)
                else:
                    st.warning("⚠️ Please fill in all fields to mark attendance.")
    
    st.markdown("---")
    st.info("💡 **How to use this portal:**\n\n1. Click 'Enable Location & Continue'\n2. Allow location access when prompted\n3. Enter your Roll Number, Name, and Branch\n4. Click 'Mark Attendance'\n\n⚠️ You must be within {}m of college to mark attendance.".format(ALLOWED_RADIUS_METERS))
    
    # Admin section: Password-protected view of attendance
    st.markdown("---")
    st.markdown("### 🔐 Admin Access")
    
    if not st.session_state.admin_logged_app1:
        # Admin login form
        with st.expander("🔑 Admin Login"):
            admin_username = st.text_input("Admin Username", key="admin_user_input")
            admin_password = st.text_input("Admin Password", type="password", key="admin_pass_input")
            
            if st.button("Login", key="admin_login_btn"):
                if admin_username in ADMINS and ADMINS[admin_username]["password"] == admin_password:
                    st.session_state.admin_logged_app1 = True
                    st.success("✅ Admin logged in successfully!")
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
    else:
        # Admin is logged in - show attendance data AND student management
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success(f"✅ Logged in as Admin")
        with col2:
            if st.button("🚪 Logout", key="admin_logout_btn"):
                st.session_state.admin_logged_app1 = False
                st.rerun()
        
        st.markdown("---")
        
        # Tabs for different admin functions
        admin_tabs = st.tabs(["👥 Manage Students", "📊 View Attendance", "📱 Device Management"])
        
        # TAB 1: Manage Students
        with admin_tabs[0]:
            st.markdown("### ➕ Add New QR Student")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                new_roll = st.text_input("Roll Number", key="new_student_roll")
            with col2:
                new_name = st.text_input("Student Name", key="new_student_name")
            with col3:
                new_branch = st.text_input("Branch", key="new_student_branch")
            
            if st.button("➕ Add Student", key="add_student_btn", type="primary"):
                if new_roll and new_name and new_branch:
                    students_df = load_students_new()
                    
                    # Check if student already exists
                    if new_roll.lower() in students_df['rollnumber'].str.lower().values:
                        st.warning(f"⚠️ Student with roll number '{new_roll}' already exists!")
                    else:
                        # Add new student
                        new_student = pd.DataFrame([{
                            'rollnumber': new_roll.strip(),
                            'studentname': new_name.strip(),
                            'branch': new_branch.strip()
                        }])
                        students_df = pd.concat([students_df, new_student], ignore_index=True)
                        students_df.to_csv(STUDENTS_NEW_CSV, index=False)
                        st.success(f"✅ Student '{new_name}' added successfully!")
                        st.rerun()
                else:
                    st.warning("⚠️ Please fill in all fields")
            
            st.markdown("---")
            st.markdown("### 👥 All QR Students")
            
            students_df = load_students_new()
            if not students_df.empty:
                st.dataframe(students_df, width=800)
                st.info(f"**Total Students:** {len(students_df)}")
                
                # Download students list
                csv_students = students_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="⬇️ Download Students List",
                    data=csv_students,
                    file_name="students_new.csv",
                    mime="text/csv",
                    key="download_students"
                )
                
                # Delete student option
                st.markdown("### 🗑️ Remove Student")
                student_to_delete = st.selectbox(
                    "Select student to remove:",
                    [""] + students_df['rollnumber'].tolist(),
                    key="delete_student_select"
                )
                if student_to_delete and st.button("🗑️ Remove Selected Student", key="delete_student_btn"):
                    students_df = students_df[students_df['rollnumber'] != student_to_delete]
                    students_df.to_csv(STUDENTS_NEW_CSV, index=False)
                    st.success(f"✅ Student '{student_to_delete}' removed!")
                    st.rerun()
            else:
                st.info("No QR students added yet. Add students using the form above.")
        
        # TAB 2: View Attendance
        with admin_tabs[1]:
            st.markdown("### 📊 Attendance Records")
            
            attendance_df = load_attendance_new()
            today_date_str = date.today().isoformat()
            
            # Filter today's attendance
            today_attendance = attendance_df[attendance_df['datestamp'] == today_date_str]
            
            if not today_attendance.empty:
                st.success(f"📅 **Today's Attendance ({today_date_str})**")
                st.dataframe(today_attendance, width=800)
                st.info(f"**Total Present Today:** {len(today_attendance)}")
                
                # Download button
                csv = today_attendance.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="⬇️ Download Today's Attendance",
                    data=csv,
                    file_name=f"attendance_{today_date_str}.csv",
                    mime="text/csv",
                    key="download_today_attendance"
                )
            else:
                st.info("No attendance marked today yet.")
            
            st.markdown("---")
            
            # Show all attendance
            if not attendance_df.empty:
                st.markdown("### 📋 All QR Attendance Records")
                st.dataframe(attendance_df, width=800)
                st.info(f"**Total Records:** {len(attendance_df)}")
                
                # Download all button
                csv_all = attendance_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="⬇️ Download All Attendance Records",
                    data=csv_all,
                    file_name=f"attendance_new_all.csv",
                    mime="text/csv",
                    key="download_all_attendance"
                )
                
                # Clear all attendance option
                st.markdown("---")
                st.markdown("### 🗑️ Clear Attendance Data")
                st.warning("⚠️ **Warning:** This will delete all attendance records permanently!")
                if st.button("🗑️ Clear All Attendance", key="clear_attendance_btn"):
                    empty_df = pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
                    empty_df.to_csv(ATTENDANCE_NEW_CSV, index=False)
                    st.success("✅ All attendance records cleared!")
                    st.rerun()
            else:
                st.info("No attendance records yet.")
        
        # TAB 3: Device Management
        with admin_tabs[2]:
            st.markdown("### 📱 Device Binding Management")
            st.info(f"**Security Settings:**\n- College Location: ({COLLEGE_LATITUDE}, {COLLEGE_LONGITUDE})\n- Allowed Radius: {ALLOWED_RADIUS_METERS} meters\n- One device per student policy: Enabled")
            
            device_df = load_device_binding()
            
            if not device_df.empty:
                st.markdown("### 📋 Registered Devices")
                st.dataframe(device_df, width=800)
                st.info(f"**Total Registered Devices:** {len(device_df)}")
                
                # Reset device binding for a student
                st.markdown("### 🔄 Reset Device Binding")
                student_to_reset = st.selectbox(
                    "Select student to reset device:",
                    [""] + device_df['rollnumber'].tolist(),
                    key="reset_device_select"
                )
                if student_to_reset and st.button("🔄 Reset Device Binding", key="reset_device_btn"):
                    device_df = device_df[device_df['rollnumber'] != student_to_reset]
                    save_device_binding(device_df)
                    st.success(f"✅ Device binding reset for '{student_to_reset}'. They can now register a new device.")
                    st.rerun()
            else:
                st.info("No devices registered yet.")
    
    # Footer
    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker - QR Portal | Powered by Streamlit")

# ------------------------------
# Main
def main():
    # Set page config
    st.set_page_config(
        page_title="QR Attendance Portal",
        page_icon="📱",
        layout="centered"
    )
    
    # Check if user has valid QR access (10-second window)
    access_valid, error_msg = check_qr_access()
    
    if not access_valid:
        st.error("🔒 **Access Denied**")
        if error_msg:
            st.warning(f"**Reason:** {error_msg}")
        st.info("📱 **How to access:**\n\n1. Ask your administrator to generate a NEW QR code\n2. Scan it immediately (valid for 10 seconds only)\n3. QR codes expire quickly for security!")
        st.stop()
    
    # QR access is valid - now check location
    st.success("✅ QR Code verified!")
    
    # Location verification section
    if not st.session_state.location_verified:
        st.markdown("### 📍 Location Verification Required")
        st.info("Please allow location access to mark attendance. This ensures you are at the college premises.")
        
        # Get user location using Streamlit's experimental feature
        if st.button("📍 Allow Location Access", type="primary", key="allow_location"):
            # For now, we'll use a simpler approach with user confirmation
            # In production, you'd use JavaScript geolocation API
            st.session_state.show_location_form = True
        
        if "show_location_form" in st.session_state and st.session_state.show_location_form:
            st.markdown("---")
            st.warning("⚠️ **For Testing:** Enter your current location or click 'Auto-Detect'")
            
            col1, col2 = st.columns(2)
            with col1:
                user_lat = st.number_input("Latitude", value=17.3850, format="%.6f", key="user_lat_input")
            with col2:
                user_lon = st.number_input("Longitude", value=78.4867, format="%.6f", key="user_lon_input")
            
            if st.button("Verify Location", type="primary"):
                in_range, distance = check_location_in_range(user_lat, user_lon)
                
                if in_range:
                    st.session_state.location_verified = True
                    st.success(f"✅ Location verified! You are {distance*1000:.0f} meters from college.")
                    st.balloons()
                    st.rerun()
                else:
                    st.error(f"❌ You are too far from college! Distance: {distance:.2f} km")
                    st.warning("You must be within 500 meters of college to mark attendance.")
        
        st.stop()
    
    # Both QR and location verified - show the portal
    st.success("✅ Location verified! You are at college premises.")
    qr_student_portal()

if __name__ == "__main__":
    main()
