import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, date, timezone, timedelta
import time, os, warnings
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

COLLEGE_LAT  = 17.4553223
COLLEGE_LON  = 78.6664965
RADIUS_M     = 500

STUDENTS_CSV = "students_new.csv"
DEVICE_CSV   = "device_binding.csv"

def att_csv(c): return f"attendance_{c.strip().replace(' ','_')}.csv"

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
    "location_blocked": False,
    "current_company": "General",
    "loc_required": False,
    "device_id": None,
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── Helpers ───────────────────────────────────────────────
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
    path = att_csv(company)
    try:
        att_df = pd.read_csv(path)
        try:
            stu_df = pd.read_csv(STUDENTS_CSV)
            if 'rollnumber' not in stu_df.columns:
                for col in stu_df.columns:
                    if 'roll' in col.lower(): stu_df = stu_df.rename(columns={col:'rollnumber'}); break
            merged = att_df.merge(stu_df, on='rollnumber', how='left', suffixes=('','_stu'))
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
    companies = []
    for f in Path(".").glob("attendance_*.csv"):
        name = f.stem.replace("attendance_","").replace("_"," ")
        companies.append(name)
    if st.session_state.current_company and st.session_state.current_company not in companies:
        companies.append(st.session_state.current_company)
    return sorted(set(companies))

# ── Haversine distance ────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1,lon1,lat2,lon2 = map(radians,[lat1,lon1,lat2,lon2])
    dlat,dlon = lat2-lat1, lon2-lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def in_range(user_lat, user_lon):
    d = haversine(COLLEGE_LAT, COLLEGE_LON, user_lat, user_lon)
    return d <= RADIUS_M, d

# ── Device binding ────────────────────────────────────────
def check_device_binding(rollnumber, device_id):
    if not device_id:
        return False, "❌ Device ID not found. Please refresh the page."
    df = load_device_binding()
    roll_lower = rollnumber.strip().lower()
    existing = df[df['rollnumber'].str.lower() == roll_lower]
    if existing.empty:
        new_row = pd.DataFrame([{'rollnumber': roll_lower, 'device_id': device_id, 'bound_at': ist_datetime_str()}])
        df = pd.concat([df, new_row], ignore_index=True)
        save_device_binding(df)
        return True, "✅ Device registered"
    bound = existing.iloc[0]['device_id']
    if bound == device_id: return True, "✅ Device verified"
    return False, "❌ This roll number is already registered on another device. Contact admin to unbind."

# ── QR access check ───────────────────────────────────────
def check_qr_access():
    import urllib.parse
    params = st.query_params
    if "access" in params:
        token = params["access"]
        if token.startswith("qr_"):
            try:
                ts = int(token.replace("qr_",""))
                elapsed = int(time.time()) - ts
                company = urllib.parse.unquote(params.get("company","General"))
                loc_enabled = params.get("loc","0") == "1"
                if elapsed <= 30:
                    st.session_state.qr_access_granted = True
                    st.session_state.current_company = company
                    st.session_state.loc_required = loc_enabled
                    return True, None
                return False, f"⏰ QR expired ({elapsed}s old). Ask admin for the latest QR."
            except: return False, "Invalid QR format."
    if st.session_state.qr_access_granted: return True, None
    return False, "Please scan the QR code shown by your admin."

# ── Mark attendance ───────────────────────────────────────
def mark_attendance(rollnumber, company, device_id):
    students = load_students()
    if students.empty or 'rollnumber' not in students.columns:
        return False, "❌ Student database not loaded. Contact admin."
    students['rollnumber'] = students['rollnumber'].astype(str).str.strip()
    if students[students['rollnumber'].str.lower() == rollnumber.strip().lower()].empty:
        return False, f"❌ Roll number '{rollnumber}' not found. Check your roll number."
    ok, msg = check_device_binding(rollnumber, device_id)
    if not ok: return False, msg
    att_df = load_attendance(company)
    today = ist_date_str()
    if not att_df.empty:
        dup = att_df[(att_df['rollnumber'].str.lower()==rollnumber.strip().lower())&(att_df['datestamp']==today)]
        if not dup.empty: return False, f"⚠️ Attendance already marked today for {company}!"
    new = pd.DataFrame([{'rollnumber':rollnumber.strip(),'timestamp':ist_time_str(),'datestamp':today,'company':company}])
    att_df = pd.concat([att_df, new], ignore_index=True)
    att_df.to_csv(att_csv(company), index=False)
    return True, "✅ Attendance marked successfully!"

# ── JS: Auto GPS + Device ID on page load ─────────────────
# This runs immediately when page loads - no button click needed
# GPS result + device_id are injected into hidden Streamlit text inputs
INIT_JS = """
<script>
(function() {
    // ── 1. Device ID from localStorage (stable across sessions) ──
    var DEVICE_KEY = 'satt_device_id_v2';
    var did = localStorage.getItem(DEVICE_KEY);
    if (!did) {
        did = 'DEV_' + Date.now().toString(36).toUpperCase() + 
              Math.random().toString(36).substr(2,8).toUpperCase();
        localStorage.setItem(DEVICE_KEY, did);
    }

    // ── 2. Write device_id to URL param (Streamlit can read it) ──
    var url = new URL(window.parent.location.href);
    var needReload = false;

    if (url.searchParams.get('device_id') !== did) {
        url.searchParams.set('device_id', did);
        needReload = true;
    }

    // ── 3. GPS: only fetch if loc=1 and not already in URL ──
    var locRequired = url.searchParams.get('loc') === '1';
    var hasGps = url.searchParams.has('gps_lat') && url.searchParams.has('gps_lon');

    if (locRequired && !hasGps) {
        // Get GPS first, then reload once with everything
        if (navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                function(pos) {
                    url.searchParams.set('gps_lat', pos.coords.latitude.toFixed(7));
                    url.searchParams.set('gps_lon', pos.coords.longitude.toFixed(7));
                    url.searchParams.set('gps_acc', Math.round(pos.coords.accuracy));
                    window.parent.location.href = url.toString();
                },
                function(err) {
                    // GPS failed - reload with device_id only, show error in app
                    url.searchParams.set('gps_err', err.code);
                    window.parent.location.href = url.toString();
                },
                {enableHighAccuracy: true, timeout: 15000, maximumAge: 60000}
            );
        } else {
            url.searchParams.set('gps_err', '99');
            window.parent.location.href = url.toString();
        }
    } else if (needReload) {
        // Just reload to add device_id
        window.parent.location.href = url.toString();
    }
})();
</script>
"""

def inject_init_js():
    components.html(INIT_JS, height=0)

# ── Student portal ────────────────────────────────────────
def student_portal(company, device_id):
    st.markdown('<div class="header">📱 QR Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")
    st.info(f"🏢 **Company / Drive:** {company}")
    st.markdown("Enter your Roll Number to mark attendance.")

    roll = st.text_input("Roll Number", key="qr_roll", placeholder="e.g. 22311a1965")
    if st.button("✅ Mark Attendance", type="primary", key="mark_btn"):
        if roll.strip():
            with st.spinner("Marking attendance..."):
                ok, msg = mark_attendance(roll, company, device_id)
            if ok:
                st.success(msg); st.balloons()
                st.info(f"**Roll:** {roll.strip()} | **Company:** {company} | **Time:** {ist_time_str()} | **Date:** {ist_date_str()}")
            else:
                st.error(msg)
        else:
            st.warning("⚠️ Please enter your Roll Number")

    st.markdown("---")
    st.info("💡 Enter only your Roll Number and click Mark Attendance")

    # Admin section
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
        admin_tabs = st.tabs(["📂 Upload CSV", "📊 Today's Attendance", "📋 All Records", "✍️ Manual Entry"])

        with admin_tabs[0]:
            st.markdown("### 📂 Upload Students CSV")
            st.info("Upload any CSV with a **rollnumber** column.")
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
                except Exception as e: st.error(f"Error: {e}")
            cur = load_students()
            if not cur.empty:
                st.markdown("---")
                st.success(f"📋 **{len(cur)} students** in database")

        with admin_tabs[1]:
            today = ist_date_str()
            st.markdown(f"### 📅 Today's Attendance ({today})")
            companies = get_all_companies()
            if companies:
                sel = st.selectbox("Company:", companies, key="today_comp")
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
            else:
                st.info("No companies yet.")

        with admin_tabs[2]:
            st.markdown("### 📋 All Records by Company")
            companies = get_all_companies()
            if companies:
                for comp in companies:
                    merged = load_attendance_with_all_fields(comp)
                    if not merged.empty:
                        c1, c2, c3 = st.columns([2,1,1])
                        with c1: st.write(f"🏢 **{comp}**")
                        with c2: st.write(f"{len(merged)} records")
                        with c3: st.download_button("⬇️ Download", merged.to_csv(index=False).encode(), f"attendance_{comp}.csv", "text/csv", key=f"dl_{comp}")
                        st.markdown("---")
            else:
                st.info("No records yet.")

        with admin_tabs[3]:
            st.markdown("### ✍️ Manual Attendance Entry")
            students = load_students()
            if not students.empty:
                man_roll = st.selectbox("Roll Number:", [""] + students['rollnumber'].tolist(), key="man_roll_sel")
            else:
                man_roll = st.text_input("Roll Number:", key="man_roll_txt")

            st.markdown("**Company:**")
            man_comp_mode = st.radio("", ["Select Existing","Enter New"], horizontal=True, key="man_comp_mode")
            all_comps = get_all_companies()
            man_company = None
            if man_comp_mode == "Select Existing":
                if all_comps: man_company = st.selectbox("Select:", all_comps, key="man_comp_sel")
                else: st.warning("No companies yet.")
            if man_comp_mode == "Enter New":
                nc = st.text_input("Company Name:", key="man_new_comp")
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
                        new = pd.DataFrame([{'rollnumber':str(man_roll).strip(),'timestamp':ist_time_str(),'datestamp':ds,'company':man_company}])
                        att_df = pd.concat([att_df, new], ignore_index=True)
                        att_df.to_csv(att_csv(man_company), index=False)
                        st.success(f"✅ **{man_roll}** marked for **{man_company}** on **{ds}**!")
                        st.rerun()
                else:
                    st.warning("⚠️ Enter both roll number and company.")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker — QR Portal | Powered by Streamlit")

# ── Main ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="QR Attendance Portal", page_icon="📱", layout="centered")

    # ── Inject JS immediately on every page load ──────────
    # This auto-fetches device_id from localStorage and GPS if needed
    inject_init_js()

    params = st.query_params
    import urllib.parse

    # ── Read device_id from URL (set by JS above) ─────────
    url_device_id = params.get("device_id", None)
    if url_device_id and not st.session_state.device_id:
        st.session_state.device_id = url_device_id
    device_id = st.session_state.device_id or url_device_id

    # ── ADMIN PATH ────────────────────────────────────────
    if st.session_state.admin_logged_app1:
        company = st.session_state.current_company or urllib.parse.unquote(params.get("company","General"))
        student_portal(company, device_id)
        return

    # ── STUDENT: QR check ─────────────────────────────────
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

    company      = st.session_state.current_company
    loc_required = st.session_state.loc_required

    # ── STUDENT: location check ───────────────────────────
    if loc_required and not st.session_state.location_verified:

        gps_err = params.get("gps_err", None)
        gps_lat = params.get("gps_lat", None)
        gps_lon = params.get("gps_lon", None)

        if gps_lat and gps_lon:
            # GPS received - check if in range
            try:
                user_lat = float(gps_lat)
                user_lon = float(gps_lon)
                user_acc = int(params.get("gps_acc", 999))
                ok, dist = in_range(user_lat, user_lon)

                if ok:
                    st.session_state.location_verified = True
                    st.rerun()  # proceed to portal
                else:
                    # ❌ HARD BLOCK
                    st.error("🚫 **Location Blocked**")
                    st.markdown(f"""
                    You are **{int(dist)}m** away from SNIST.  
                    Required: within **{RADIUS_M}m**.  
                    Please come to college and scan the QR again.
                    """)
                    st.stop()
            except:
                st.error("❌ Could not read GPS. Please refresh and try again.")
                st.stop()

        elif gps_err:
            # GPS error from JS
            err_msgs = {
                "1": "❌ Location permission denied. Please enable location in your browser settings and scan again.",
                "2": "❌ Location unavailable. Please enable GPS on your device.",
                "3": "❌ Location request timed out. Please ensure GPS is enabled and try again.",
                "99": "❌ GPS not supported on this browser."
            }
            st.error(err_msgs.get(gps_err, "❌ Location error. Please try again."))
            st.stop()

        else:
            # Waiting for GPS - JS is fetching in background
            st.info(f"🏢 **Company:** {company}")
            st.warning("📍 **Location verification required.**")
            st.info("📡 Getting your location automatically... **Please allow location access when your browser asks.**")
            # Show spinner while waiting
            with st.spinner("Fetching your GPS location... this may take a few seconds"):
                time.sleep(2)
            st.rerun()  # rerun to check if GPS params have arrived

    if loc_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR Code verified!")

    student_portal(company, device_id)

if __name__ == "__main__":
    main()
