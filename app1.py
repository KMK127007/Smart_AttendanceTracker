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
    
    # Validate student exists in students_new.csv
    student_record = students_new_df[
        (students_new_df['rollnumber'].str.lower() == rollnumber.lower()) &
        (students_new_df['studentname'].str.lower() == studentname.lower()) &
        (students_new_df['branch'].str.lower() == branch.lower())
    ]
    
    if student_record.empty:
        return False, "Student not found in the database. Please check your Roll Number, Name, and Branch."
    
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
    
    # Show the QR portal
    qr_student_portal()

if __name__ == "__main__":
    main()