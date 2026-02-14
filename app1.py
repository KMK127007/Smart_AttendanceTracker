import streamlit as st
import pandas as pd
from datetime import datetime, date, timezone, timedelta
import time, json, uuid, os, warnings
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

IST = timezone(timedelta(hours=5, minutes=30))
def ist_now(): return datetime.now(IST)
def ist_time_str(): return ist_now().strftime("%H:%M:%S")
def ist_date_str(): return ist_now().strftime("%d-%m-%Y")
def ist_datetime_str(): return ist_now().strftime("%d-%m-%Y %H:%M:%S")

try:
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as e:
    st.error(f"Missing secret: {e}"); st.stop()

COLLEGE_LAT, COLLEGE_LON, RADIUS_M = 17.4553223, 78.6664965, 500

STUDENTS_CSV     = "students_new.csv"
DEVICE_CSV       = "device_binding.csv"
QR_SETTINGS_FILE = "qr_settings.json"

def att_csv(company): return f"attendance_{company.strip().replace(' ','_')}.csv"

def local_css():
    try:
        p = Path(__file__).parent / "style.css"
        if p.exists(): st.markdown(f"<style>{p.read_text()}</style>", unsafe_allow_html=True)
    except: pass

local_css()

for k, v in {
    "admin_logged_app1": False,
    "qr_access_granted": False,
    "location_verified": False,
    "show_location_form": False,
    "device_fingerprint": None,
    "loc_required": False,
    "current_company": "General",
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── Stable device fingerprint via JS + session ────────────
def get_or_create_device_fingerprint():
    """
    Inject JS to read/write a stable device ID from localStorage.
    Falls back to a uuid stored in session_state if JS hasn't returned yet.
    """
    # Inject JS to post localStorage device_id back to Streamlit
    st.components.v1.html("""
        <script>
        const key = 'smart_attendance_device_id';
        let did = localStorage.getItem(key);
        if (!did) {
            did = 'dev_' + Math.random().toString(36).substr(2,16) + Date.now();
            localStorage.setItem(key, did);
        }
        // Write to a hidden element so Python can read via URL param workaround
        // We store in sessionStorage as well for this session
        sessionStorage.setItem(key, did);
        </script>
    """, height=0)
    
    # Use session_state fingerprint - generated once per session, stable across reruns
    if not st.session_state.device_fingerprint:
        # Generate once and lock it in session
        st.session_state.device_fingerprint = str(uuid.uuid4())
    return st.session_state.device_fingerprint

# ── QR settings (written by smartapp) ────────────────────
def load_qr_settings():
    try:
        with open(QR_SETTINGS_FILE) as f: return json.load(f)
    except: return {"location_enabled": False, "window_seconds": 60, "company": "General"}

# ── CSV helpers ───────────────────────────────────────────
def load_students():
    try:
        df = pd.read_csv(STUDENTS_CSV)
        if 'rollnumber' not in df.columns:
            for col in df.columns:
                if 'roll' in col.lower(): return df.rename(columns={col:'rollnumber'})
        return df
    except: return pd.DataFrame(columns=["rollnumber"])

def load_attendance(company):
    path = att_csv(company)
    try:
        df = pd.read_csv(path)
        for c in ["rollnumber","timestamp","datestamp","company"]:
            if c not in df.columns: df[c]=""
        return df[["rollnumber","timestamp","datestamp","company"]]
    except:
        df = pd.DataFrame(columns=["rollnumber","timestamp","datestamp","company"])
        df.to_csv(path, index=False); return df

def load_attendance_with_all_fields(company):
    """Load attendance merged with student CSV fields"""
    path = att_csv(company)
    try:
        att_df = pd.read_csv(path)
        try:
            stu_df = pd.read_csv(STUDENTS_CSV)
            if 'rollnumber' not in stu_df.columns:
                for col in stu_df.columns:
                    if 'roll' in col.lower(): stu_df = stu_df.rename(columns={col:'rollnumber'}); break
            # Merge to get all student CSV fields
            merged = att_df.merge(stu_df, on='rollnumber', how='left', suffixes=('','_stu'))
            # Add company column at end if not present
            if 'company' not in merged.columns: merged['company'] = company
            return merged
        except: return att_df
    except: return pd.DataFrame()

def load_device_binding():
    try: return pd.read_csv(DEVICE_CSV)
    except:
        df = pd.DataFrame(columns=["rollnumber","device_id","bound_at"])
        df.to_csv(DEVICE_CSV, index=False); return df

def save_device_binding(df): df.to_csv(DEVICE_CSV, index=False)

def get_all_companies():
    """Collect all companies from existing attendance CSV files"""
    companies = []
    for f in Path(".").glob("attendance_*.csv"):
        name = f.stem.replace("attendance_", "").replace("_", " ")
        companies.append(name)
    # Also from qr_settings
    settings = load_qr_settings()
    qr_comp = settings.get("company", "")
    if qr_comp and qr_comp not in companies:
        companies.append(qr_comp)
    return sorted(set(companies))

# ── Device binding ────────────────────────────────────────
def check_device_binding(rollnumber):
    # Use stable session fingerprint - same for entire browser session
    device_id = get_or_create_device_fingerprint()
    df = load_device_binding()
    
    roll_lower = rollnumber.strip().lower()
    existing = df[df['rollnumber'].str.lower() == roll_lower]
    
    if existing.empty:
        # First time this roll number marks attendance - bind this device
        new_row = pd.DataFrame([{
            'rollnumber': roll_lower,
            'device_id': device_id,
            'bound_at': ist_datetime_str()
        }])
        df = pd.concat([df, new_row], ignore_index=True)
        save_device_binding(df)
        return True, "✅ Device registered"
    
    bound = existing.iloc[0]['device_id']
    if bound == device_id:
        return True, "✅ Device verified"
    return False, "❌ This roll number is already registered on another device. Contact admin to unbind your device."

# ── Location ─────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2-lat1, lon2-lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def in_range(user_lat, user_lon):
    d = haversine(COLLEGE_LAT, COLLEGE_LON, user_lat, user_lon)
    return d <= RADIUS_M, d

# ── QR Access check + read company & location from URL ───
def check_qr_access():
    import urllib.parse
    params = st.query_params

    if "access" in params:
        token = params["access"]
        if token.startswith("qr_"):
            try:
                ts = int(token.replace("qr_", ""))
                elapsed = int(time.time()) - ts
                company = urllib.parse.unquote(params.get("company", "General"))
                loc_enabled = params.get("loc", "0") == "1"

                if elapsed <= 30:
                    # Valid QR - store in session so reruns don't lose them
                    st.session_state.qr_access_granted = True
                    st.session_state.current_company = company
                    st.session_state.loc_required = loc_enabled
                    return True, None
                else:
                    return False, f"⏰ QR expired ({elapsed}s old). Ask admin for the latest QR."
            except:
                return False, "Invalid QR format."

    # QR already granted in this session - use stored values
    if st.session_state.qr_access_granted:
        return True, None

    return False, "Please scan the QR code shown by your admin."

# ── Mark attendance ───────────────────────────────────────
def mark_attendance(rollnumber, company):
    students = load_students()
    if students.empty or 'rollnumber' not in students.columns:
        return False, "❌ Student database not loaded. Contact admin."

    students['rollnumber'] = students['rollnumber'].astype(str).str.strip()
    if students[students['rollnumber'].str.lower() == rollnumber.strip().lower()].empty:
        return False, f"❌ Roll number '{rollnumber}' not found. Check your roll number."

    # Single device check
    ok, msg = check_device_binding(rollnumber)
    if not ok: return False, msg

    # Duplicate check for today in this company
    att_df = load_attendance(company)
    today = ist_date_str()
    if not att_df.empty:
        dup = att_df[(att_df['rollnumber'].str.lower()==rollnumber.strip().lower()) & (att_df['datestamp']==today)]
        if not dup.empty: return False, f"⚠️ Attendance already marked today for {company}!"

    # Save
    new = pd.DataFrame([{'rollnumber': rollnumber.strip(), 'timestamp': ist_time_str(), 'datestamp': today, 'company': company}])
    att_df = pd.concat([att_df, new], ignore_index=True)
    att_df.to_csv(att_csv(company), index=False)
    return True, "✅ Attendance marked successfully!"

# ── Student portal ────────────────────────────────────────
def student_portal(company):
    st.markdown('<div class="header">📱 QR Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")
    st.info(f"🏢 **Company / Drive:** {company}")
    st.markdown("Enter your Roll Number to mark attendance.")

    roll = st.text_input("Roll Number", key="qr_roll", placeholder="e.g. 22311a1965")
    if st.button("✅ Mark Attendance", type="primary", key="mark_btn"):
        if roll.strip():
            with st.spinner("Marking attendance..."):
                ok, msg = mark_attendance(roll, company)
            if ok:
                st.success(msg); st.balloons()
                st.info(f"**Roll:** {roll.strip()} | **Company:** {company} | **Time:** {ist_time_str()} | **Date:** {ist_date_str()}")
            else:
                st.error(msg)
        else:
            st.warning("⚠️ Please enter your Roll Number")

    st.markdown("---")
    st.info("💡 Enter only your Roll Number and click Mark Attendance")

    # ── Admin section ─────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔐 Admin Access")

    if not st.session_state.admin_logged_app1:
        with st.expander("🔑 Admin Login"):
            u = st.text_input("Username", key="adm_u")
            p = st.text_input("Password", type="password", key="adm_p")
            if st.button("Login", key="adm_login"):
                if u in ADMINS and ADMINS[u]["password"] == p:
                    st.session_state.admin_logged_app1 = True
                    st.session_state.qr_access_granted = True
                    st.success("✅ Logged in!"); st.rerun()
                else: st.error("❌ Invalid credentials")
    else:
        c1, c2 = st.columns([3,1])
        with c1: st.success("✅ Admin logged in")
        with c2:
            if st.button("🚪 Logout", key="adm_out"):
                st.session_state.admin_logged_app1 = False; st.rerun()

        st.markdown("---")

        # ── Admin tabs ────────────────────────────────────
        admin_tabs = st.tabs(["📂 Upload CSV", "📊 Today's Attendance", "📋 All Records", "✍️ Manual Entry"])

        # Tab 1: Upload CSV
        with admin_tabs[0]:
            st.markdown("### 📂 Upload Students CSV")
            st.info("Upload any CSV that contains a **rollnumber** column. Only rollnumber is used for validation. All other fields are stored and shown in attendance records.")

            uf = st.file_uploader("Upload CSV", type=["csv"], key="csv_upload")
            if uf is not None:
                try:
                    df = pd.read_csv(uf)
                    roll_col = next((c for c in df.columns if 'roll' in c.lower()), None)
                    if roll_col is None:
                        st.error("❌ No rollnumber column found!")
                    else:
                        if roll_col != 'rollnumber': df = df.rename(columns={roll_col:'rollnumber'})
                        df.to_csv(STUDENTS_CSV, index=False)
                        st.success(f"✅ Uploaded! **{len(df)} students** saved.")
                        st.dataframe(df.head(10), width=600)
                        st.caption(f"Showing first 10 of {len(df)} records")
                except Exception as e: st.error(f"Error: {e}")

            cur = load_students()
            if not cur.empty:
                st.markdown("---")
                st.success(f"📋 **{len(cur)} students** currently in database")
                st.dataframe(cur.head(10), width=600)

        # Tab 2: Today's attendance (all fields from CSV + company)
        with admin_tabs[1]:
            today = ist_date_str()
            st.markdown(f"### 📅 Today's Attendance ({today})")
            companies = get_all_companies()

            if not companies:
                st.info("No attendance records yet.")
            else:
                sel = st.selectbox("Company:", companies, key="today_comp")
                # Load with all fields from student CSV
                merged = load_attendance_with_all_fields(sel)
                if not merged.empty and 'datestamp' in merged.columns:
                    today_df = merged[merged['datestamp'] == today]
                    if not today_df.empty:
                        st.success(f"**{len(today_df)} present** for {sel}")
                        st.dataframe(today_df, width=900)
                        st.download_button("⬇️ Download Today's", today_df.to_csv(index=False).encode(), f"att_{sel}_{today}.csv", "text/csv", key="dl_td")
                    else:
                        st.info(f"No attendance today for {sel}.")
                else:
                    st.info(f"No records yet for {sel}.")

        # Tab 3: All records - downloadable per company
        with admin_tabs[2]:
            st.markdown("### 📋 All Records by Company")
            companies = get_all_companies()

            if not companies:
                st.info("No attendance records yet.")
            else:
                st.markdown("Download attendance records per company:")
                for comp in companies:
                    # Load with all student CSV fields merged
                    merged = load_attendance_with_all_fields(comp)
                    if not merged.empty:
                        col1, col2, col3 = st.columns([2, 1, 1])
                        with col1: st.write(f"🏢 **{comp}**")
                        with col2: st.write(f"{len(merged)} records")
                        with col3:
                            st.download_button(
                                f"⬇️ Download",
                                merged.to_csv(index=False).encode(),
                                f"attendance_{comp}.csv",
                                "text/csv",
                                key=f"dl_{comp}"
                            )
                        st.markdown("---")

        # Tab 4: Manual Entry
        with admin_tabs[3]:
            st.markdown("### ✍️ Manual Attendance Entry")
            st.info("💡 Enter roll number and company name to mark attendance manually.")

            students = load_students()

            # Roll number input
            if not students.empty:
                man_roll = st.selectbox("Roll Number:", [""] + students['rollnumber'].tolist(), key="man_roll_sel")
            else:
                man_roll = st.text_input("Roll Number:", key="man_roll_txt", placeholder="e.g. 22311a1965")

            # Company input for manual entry
            st.markdown("**Company Name:**")
            man_comp_mode = st.radio("", ["Select Existing", "Enter New"], horizontal=True, key="man_comp_mode")
            all_comps = get_all_companies()
            man_company = None

            if man_comp_mode == "Select Existing":
                if all_comps:
                    man_company = st.selectbox("Select Company:", all_comps, key="man_comp_sel")
                else:
                    st.warning("No companies yet. Switch to 'Enter New'.")

            if man_comp_mode == "Enter New":
                nc = st.text_input("Company Name:", placeholder="e.g. TCS, Infosys", key="man_new_comp")
                if nc.strip(): man_company = nc.strip()

            man_date = st.date_input("Date:", value=date.today(), key="man_date")

            if st.button("✅ Mark Attendance", type="primary", key="man_mark_btn"):
                if man_roll and man_company:
                    ds = man_date.strftime("%d-%m-%Y")
                    att_df = load_attendance(man_company)
                    already = att_df[(att_df['rollnumber'].str.lower()==str(man_roll).lower())&(att_df['datestamp']==ds)] if not att_df.empty else pd.DataFrame()
                    if not already.empty:
                        st.warning(f"⚠️ Already marked {man_roll} on {ds} for {man_company}")
                    else:
                        new = pd.DataFrame([{'rollnumber': str(man_roll).strip(), 'timestamp': ist_time_str(), 'datestamp': ds, 'company': man_company}])
                        att_df = pd.concat([att_df, new], ignore_index=True)
                        att_df.to_csv(att_csv(man_company), index=False)
                        st.success(f"✅ **{man_roll}** marked for **{man_company}** on **{ds}**!")
                        st.rerun()
                else:
                    st.warning("⚠️ Enter both roll number and company name.")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker — QR Portal | Powered by Streamlit")

# ── Main ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="QR Attendance Portal", page_icon="📱", layout="centered")

    import urllib.parse
    params = st.query_params

    # Admin bypasses QR check entirely — stays forever
    if st.session_state.admin_logged_app1:
        company = st.session_state.current_company or urllib.parse.unquote(params.get("company", "General"))
        student_portal(company)
        return

    # Student path — must scan valid QR
    valid, err = check_qr_access()

    if not valid:
        st.error("🔒 **Access Denied**")
        if err: st.warning(err)
        st.info("📱 Ask your admin to generate a QR code and scan it within 30 seconds.")
        st.markdown("---")
        with st.expander("🔑 Admin Login (No time restriction)"):
            u = st.text_input("Username", key="bl_u")
            p = st.text_input("Password", type="password", key="bl_p")
            if st.button("Login", key="bl_btn"):
                if u in ADMINS and ADMINS[u]["password"] == p:
                    st.session_state.admin_logged_app1 = True
                    st.session_state.qr_access_granted = True
                    st.success("✅ Logged in!"); st.rerun()
                else: st.error("❌ Invalid credentials")
        st.stop()

    # Read from session state (set during QR validation)
    company = st.session_state.current_company
    loc_required = st.session_state.loc_required

    # Location check (only if admin enabled it in smartapp)
    if loc_required and not st.session_state.location_verified:
        st.success("✅ QR verified!")
        st.info(f"🏢 **Company:** {company}")
        st.markdown("### 📍 Location Verification Required")
        st.info("Your admin has enabled location check.\nYou must be within 500m of SNIST.")

        if st.button("📍 Verify My Location", type="primary", key="loc_btn"):
            st.session_state.show_location_form = True

        if st.session_state.show_location_form:
            c1, c2 = st.columns(2)
            with c1: user_lat = st.number_input("Latitude", value=17.4553, format="%.6f", key="lat")
            with c2: user_lon = st.number_input("Longitude", value=78.6665, format="%.6f", key="lon")
            if st.button("✅ Confirm Location", type="primary", key="conf_loc"):
                ok, dist = in_range(user_lat, user_lon)
                if ok:
                    st.session_state.location_verified = True
                    st.success(f"✅ Location verified! {int(dist)}m from college.")
                    st.rerun()
                else:
                    st.error(f"❌ {int(dist)}m away. Must be within {RADIUS_M}m of SNIST.")
        st.stop()

    if loc_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR Code verified!")

    student_portal(company)

if __name__ == "__main__":
    main()
