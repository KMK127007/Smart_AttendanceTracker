import streamlit as st
import pandas as pd
from datetime import datetime, date
import os
from pathlib import Path
import warnings

# Suppress all warnings for cleaner UI
warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

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

# ------------------------------
# Security: Check for valid access token
def check_qr_access():
    """Check if user came via QR code with valid token"""
    query_params = st.query_params
    
    # Check for access token in URL
    if "access" in query_params and query_params["access"] == "qr_portal_2026":
        st.session_state.qr_access_granted = True
        return True
    
    # Check if already granted in session
    if st.session_state.qr_access_granted:
        return True
    
    return False

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
    expected = ["rollnumber", "studentname", "timestamp", "datestamp"]
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
        df = pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
        df.to_csv(ATTENDANCE_NEW_CSV, index=False)
        return df
    except Exception as _:
        st.error(f"Attendance New CSV read error: {_}. Recreating attendance_new file.")
        df = pd.DataFrame(columns=["rollnumber", "studentname", "timestamp", "datestamp"])
        df.to_csv(ATTENDANCE_NEW_CSV, index=False)
        return df

def save_attendance_new(df):
    df.to_csv(ATTENDANCE_NEW_CSV, index=False)

# ------------------------------
# QR Attendance marking function
def mark_attendance_qr(rollnumber, studentname, branch):
    """Mark attendance using QR code portal"""
    students_new_df = load_students_new()
    
    # Trim whitespace from input
    rollnumber = rollnumber.strip()
    studentname = studentname.strip()
    branch = branch.strip()
    
    # Trim whitespace from CSV data
    students_new_df['rollnumber'] = students_new_df['rollnumber'].str.strip()
    students_new_df['studentname'] = students_new_df['studentname'].str.strip()
    students_new_df['branch'] = students_new_df['branch'].str.strip()
    
    # Debug: Show what we're searching for (can be removed later)
    # st.info(f"Searching for: Roll={rollnumber.lower()}, Name={studentname.lower()}, Branch={branch.lower()}")
    
    # Validate student exists in students_new.csv
    student_record = students_new_df[
        (students_new_df['rollnumber'].str.lower() == rollnumber.lower()) &
        (students_new_df['studentname'].str.lower() == studentname.lower()) &
        (students_new_df['branch'].str.lower() == branch.lower())
    ]
    
    # Debug info if student not found
    if student_record.empty:
        # Check each field individually to help debug
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
    
    # Check if already marked today
    attendance_new_df = load_attendance_new()
    today_date_str = date.today().isoformat()
    
    already_marked = attendance_new_df[
        (attendance_new_df['rollnumber'].str.lower() == rollnumber.lower()) &
        (attendance_new_df['datestamp'] == today_date_str)
    ]
    
    if not already_marked.empty:
        return False, "Attendance already marked today for this student via QR code."
    
    # Mark attendance
    new_entry = {
        "rollnumber": rollnumber,
        "studentname": studentname,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "datestamp": today_date_str
    }
    
    attendance_new_df = pd.concat([attendance_new_df, pd.DataFrame([new_entry])], ignore_index=True)
    save_attendance_new(attendance_new_df)
    
    return True, "Attendance marked successfully via QR code ✅"

# ------------------------------
# QR Student Portal - Main Interface
def qr_student_portal():
    st.markdown('<div class="header">📱 QR Code Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Quick Attendance via QR Code")
    
    with st.container():
        st.markdown("Please enter your details to mark attendance.")
        
        rollnumber = st.text_input("Roll Number", key="qr_rollnumber_input", placeholder="Enter your roll number")
        studentname = st.text_input("Student Name", key="qr_studentname_input", placeholder="Enter your full name")
        branch = st.text_input("Branch", key="qr_branch_input", placeholder="Enter your branch (e.g., CSE, ECE)")
        
        if st.button("Mark Attendance", key="qr_mark_attendance_button", type="primary"):
            if rollnumber and studentname and branch:
                with st.spinner("Marking attendance..."):
                    success, message = mark_attendance_qr(rollnumber, studentname, branch)
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
    st.info("💡 **How to use this portal:**\n\n1. Enter your Roll Number\n2. Enter your Student Name\n3. Enter your Branch\n4. Click 'Mark Attendance'\n\nYour attendance will be recorded instantly!")
    
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
        admin_tabs = st.tabs(["👥 Manage Students", "📊 View Attendance"])
        
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
    
    # Check if user has valid QR access
    if not check_qr_access():
        st.error("🔒 **Access Denied**")
        st.warning("This portal can only be accessed by scanning the QR code from the admin panel.")
        st.info("Please ask your administrator to generate a QR code and scan it to access this portal.")
        st.stop()
    
    # Show the QR portal if access is granted
    qr_student_portal()

if __name__ == "__main__":
    main()
