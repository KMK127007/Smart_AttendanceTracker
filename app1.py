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
def ist_now():          return datetime.now(IST)
def ist_time_str():     return ist_now().strftime("%H:%M:%S")
def ist_date_str():     return ist_now().strftime("%d-%m-%Y")
def ist_datetime_str(): return ist_now().strftime("%d-%m-%Y %H:%M:%S")

try:
    ADMIN_USERNAME = st.secrets["admin_user"]["username"]
    ADMIN_PASSWORD = st.secrets["admin_user"]["password"]
    ADMINS = {ADMIN_USERNAME: {"password": ADMIN_PASSWORD}}
except KeyError as e:
    st.error(f"Missing secret: {e}"); st.stop()

COLLEGE_LAT = 17.4553223
COLLEGE_LON = 78.6664965
RADIUS_M    = 500

STUDENTS_CSV = "students_new.csv"
DEVICE_CSV   = "device_binding.csv"

def att_csv(c): return f"attendance_{c.strip().replace(' ','_')}.csv"

def local_css():
    try:
        p = Path(__file__).parent / "style.css"
        if p.exists(): st.markdown(f"<style>{p.read_text()}</style>", unsafe_allow_html=True)
    except: pass

local_css()

# ── Session state ─────────────────────────────────────────
for k, v in {
    "admin_logged_app1": False,
    "qr_access_granted": False,
    "location_verified": False,
    "current_company": "General",
    "loc_required": False,
    "device_id": None,
    "gps_lat": None,
    "gps_lon": None,
    "gps_error": None,
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── CSV helpers ───────────────────────────────────────────
def load_students():
    try:
        df = pd.read_csv(STUDENTS_CSV)
        if 'rollnumber' not in df.columns:
            for col in df.columns:
                if 'roll' in col.lower(): return df.rename(columns={col: 'rollnumber'})
        return df
    except: return pd.DataFrame(columns=["rollnumber"])

def load_attendance(company):
    path = att_csv(company)
    try:
        df = pd.read_csv(path)
        for c in ["rollnumber","timestamp","datestamp","company"]:
            if c not in df.columns: df[c] = ""
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
    if st.session_state.current_company not in companies:
        companies.append(st.session_state.current_company)
    return sorted(set(companies))

# ── Haversine ─────────────────────────────────────────────
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
        return False, "❌ Device ID missing. Please refresh."
    df = load_device_binding()
    roll_lower = rollnumber.strip().lower()

    device_rows = df[df['device_id'] == device_id]
    if not device_rows.empty:
        bound_roll = device_rows.iloc[0]['rollnumber'].lower()
        if bound_roll != roll_lower:
            return False, f"❌ This device is already used by **{bound_roll.upper()}**. One device = one student only."
        return True, "ok"

    roll_rows = df[df['rollnumber'].str.lower() == roll_lower]
    if not roll_rows.empty:
        return False, "❌ Your roll number is already registered on a different device. Contact admin to unbind."

    new_row = pd.DataFrame([{'rollnumber': roll_lower, 'device_id': device_id, 'bound_at': ist_datetime_str()}])
    df = pd.concat([df, new_row], ignore_index=True)
    save_device_binding(df)
    return True, "ok"

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
        return False, f"❌ Roll number '{rollnumber}' not found."

    ok, msg = check_device_binding(rollnumber, device_id)
    if not ok: return False, msg

    att_df = load_attendance(company)
    if not att_df.empty:
        already = att_df[att_df['rollnumber'].str.lower() == rollnumber.strip().lower()]
        if not already.empty:
            return False, f"⚠️ Attendance already marked for {company} (on {already.iloc[0]['datestamp']})."

    today = ist_date_str()
    new = pd.DataFrame([{'rollnumber': rollnumber.strip(), 'timestamp': ist_time_str(), 'datestamp': today, 'company': company}])
    att_df = pd.concat([att_df, new], ignore_index=True)
    att_df.to_csv(att_csv(company), index=False)
    return True, "✅ Attendance marked successfully!"

# ── GPS via st.text_input + JS (FAST & RELIABLE) ─────────
# Key insight: Streamlit text_input is the only reliable way to pass
# data from JS to Python. We hide it visually and auto-populate via JS.
# GPS uses maximumAge:0 + timeout:5000 for fresh reading.
# Device ID uses localStorage — permanent across refreshes.

def gps_and_device_component():
    """
    Renders two hidden text inputs.
    JS reads localStorage for device_id and browser GPS for location.
    Both are written into the hidden inputs → Streamlit reads them.
    Returns (device_id, lat, lon, gps_error)
    """
    # Hide the inputs visually
    st.markdown("""
    <style>
    [data-testid="stTextInput"]:has(input[aria-label="__did__"]),
    [data-testid="stTextInput"]:has(input[aria-label="__glat__"]),
    [data-testid="stTextInput"]:has(input[aria-label="__glon__"]),
    [data-testid="stTextInput"]:has(input[aria-label="__gerr__"]) {
        height: 0 !important; overflow: hidden !important;
        margin: 0 !important; padding: 0 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    did_val  = st.text_input("d", key="__did__",  label_visibility="collapsed")
    lat_val  = st.text_input("a", key="__glat__", label_visibility="collapsed")
    lon_val  = st.text_input("b", key="__glon__", label_visibility="collapsed")
    err_val  = st.text_input("c", key="__gerr__", label_visibility="collapsed")

    # JS that fires immediately, populates the hidden inputs and triggers rerun
    js_code = f"""
    <script>
    (function() {{
        var loc_required = {str(st.session_state.loc_required).lower()};
        var already_have_gps = {str(bool(st.session_state.gps_lat)).lower()};
        var already_have_did = {str(bool(st.session_state.device_id)).lower()};

        function setInput(ariaLabel, value) {{
            // Find the input by its aria-label (Streamlit sets this from key)
            var inputs = window.parent.document.querySelectorAll('input');
            for (var i = 0; i < inputs.length; i++) {{
                if (inputs[i].getAttribute('aria-label') === ariaLabel) {{
                    var setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set;
                    setter.call(inputs[i], value);
                    inputs[i].dispatchEvent(new Event('input', {{bubbles: true}}));
                    return true;
                }}
            }}
            return false;
        }}

        // Step 1: Device ID from localStorage
        if (!already_have_did) {{
            var KEY = 'satt_did_v3';
            var did = localStorage.getItem(KEY);
            if (!did) {{
                did = 'DV' + Date.now().toString(36) + Math.random().toString(36).substr(2,8);
                localStorage.setItem(KEY, did);
            }}
            setInput('__did__', did);
        }}

        // Step 2: GPS (only if location required and not yet fetched)
        if (loc_required && !already_have_gps) {{
            if (!navigator.geolocation) {{
                setInput('__gerr__', '99');
                return;
            }}
            navigator.geolocation.getCurrentPosition(
                function(pos) {{
                    setInput('__glat__', pos.coords.latitude.toFixed(6));
                    setInput('__glon__', pos.coords.longitude.toFixed(6));
                }},
                function(err) {{
                    setInput('__gerr__', String(err.code));
                }},
                {{
                    enableHighAccuracy: false,
                    timeout: 5000,
                    maximumAge: 30000
                }}
            );
        }}
    }})();
    </script>
    """
    components.html(js_code, height=0)

    return did_val or None, lat_val or None, lon_val or None, err_val or None

# ── Student portal ────────────────────────────────────────
def student_portal(company, device_id):
    st.markdown('<div class="header">📱 QR Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")
    st.info(f"🏢 **Company / Drive:** {company}")

    roll = st.text_input("Roll Number", key="qr_roll", placeholder="e.g. 22311a1965")
    if st.button("✅ Mark Attendance", type="primary", key="mark_btn"):
        if roll.strip():
            active_device = st.session_state.device_id or device_id
            with st.spinner("Marking attendance..."):
                ok, msg = mark_attendance(roll, company, active_device)
            if ok:
                st.success(msg); st.balloons()
                st.info(f"**Roll:** {roll.strip()} | **Company:** {company} | **Time:** {ist_time_str()} | **Date:** {ist_date_str()}")
            else:
                st.error(msg)
        else:
            st.warning("⚠️ Please enter your Roll Number")

    st.markdown("---")
    st.info("💡 Enter only your Roll Number and click Mark Attendance")
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
        admin_tabs = st.tabs(["📂 Upload CSV","📊 Today's Attendance","📋 All Records","✍️ Manual Entry","📱 Device Bindings"])

        with admin_tabs[0]:
            st.markdown("### 📂 Upload Students CSV")
            st.info("Upload any CSV with a **rollnumber** column.")
            uf = st.file_uploader("Upload CSV", type=["csv"], key="csv_upload")
            if uf:
                try:
                    df = pd.read_csv(uf)
                    roll_col = next((c for c in df.columns if 'roll' in c.lower()), None)
                    if not roll_col: st.error("❌ No rollnumber column!")
                    else:
                        if roll_col != 'rollnumber': df = df.rename(columns={roll_col:'rollnumber'})
                        df.to_csv(STUDENTS_CSV, index=False)
                        st.success(f"✅ {len(df)} students saved.")
                        st.dataframe(df.head(10), width=600)
                except Exception as e: st.error(f"Error: {e}")
            cur = load_students()
            if not cur.empty:
                st.markdown("---"); st.success(f"📋 **{len(cur)} students** in database"); st.dataframe(cur, width=700)

        with admin_tabs[1]:
            today = ist_date_str()
            st.markdown(f"### 📅 Today ({today})")
            comps = get_all_companies()
            if comps:
                sel = st.selectbox("Company:", comps, key="today_comp")
                merged = load_attendance_with_all_fields(sel)
                if not merged.empty and 'datestamp' in merged.columns:
                    td = merged[merged['datestamp']==today]
                    if not td.empty:
                        st.success(f"**{len(td)} present**"); st.dataframe(td, width=900)
                        st.download_button("⬇️ Download", td.to_csv(index=False).encode(), f"att_{sel}_{today}.csv","text/csv",key="dl_td")
                    else: st.info("No attendance today.")
                else: st.info("No records yet.")
            else: st.info("No companies yet.")

        with admin_tabs[2]:
            st.markdown("### 📋 All Records by Company")
            comps = get_all_companies()
            if comps:
                for comp in comps:
                    merged = load_attendance_with_all_fields(comp)
                    if not merged.empty:
                        c1,c2,c3 = st.columns([2,1,1])
                        with c1: st.write(f"🏢 **{comp}**")
                        with c2: st.write(f"{len(merged)} records")
                        with c3: st.download_button("⬇️ Download", merged.to_csv(index=False).encode(), f"attendance_{comp}.csv","text/csv",key=f"dl_{comp}")
                        st.markdown("---")
            else: st.info("No records yet.")

        with admin_tabs[3]:
            st.markdown("### ✍️ Manual Entry")
            students = load_students()
            man_roll = st.selectbox("Roll Number:",[""] + students['rollnumber'].tolist(), key="man_roll_sel") if not students.empty else st.text_input("Roll Number:", key="man_roll_txt")
            mode = st.radio("Company:", ["Select Existing","Enter New"], horizontal=True, key="man_comp_mode")
            all_comps = get_all_companies(); man_company = None
            if mode=="Select Existing":
                if all_comps: man_company = st.selectbox("Select:", all_comps, key="man_comp_sel")
                else: st.warning("No companies yet.")
            if mode=="Enter New":
                nc = st.text_input("Company Name:", key="man_new_comp")
                if nc.strip(): man_company = nc.strip()
            man_date = st.date_input("Date:", value=date.today(), key="man_date")
            if st.button("✅ Mark", type="primary", key="man_mark_btn"):
                if man_roll and man_company:
                    ds = man_date.strftime("%d-%m-%Y")
                    att_df = load_attendance(man_company)
                    already = att_df[att_df['rollnumber'].str.lower()==str(man_roll).lower()] if not att_df.empty else pd.DataFrame()
                    if not already.empty: st.warning(f"Already marked {man_roll} for {man_company}")
                    else:
                        new = pd.DataFrame([{'rollnumber':str(man_roll).strip(),'timestamp':ist_time_str(),'datestamp':ds,'company':man_company}])
                        att_df = pd.concat([att_df,new],ignore_index=True); att_df.to_csv(att_csv(man_company),index=False)
                        st.success(f"✅ {man_roll} marked for {man_company} on {ds}!"); st.rerun()
                else: st.warning("Enter both roll number and company.")

        with admin_tabs[4]:
            st.markdown("### 📱 Device Bindings")
            st.info("One device = one student. Unbind here if student changes device.")
            df = load_device_binding()
            if not df.empty:
                st.dataframe(df, width=800); st.info(f"**{len(df)} devices bound**")
                to_unbind = st.selectbox("Roll to Unbind:", [""]+df['rollnumber'].tolist(), key="unbind_sel")
                if to_unbind and st.button("🔓 Unbind", key="unbind_btn"):
                    df = df[df['rollnumber']!=to_unbind]; save_device_binding(df)
                    st.success(f"✅ '{to_unbind}' unbound."); st.rerun()
            else: st.info("No devices bound yet.")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker — QR Portal | Powered by Streamlit")

# ── Main ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="QR Attendance Portal", page_icon="📱", layout="centered")

    import urllib.parse
    params = st.query_params

    # ── ADMIN: no checks, stays forever ──────────────────
    if st.session_state.admin_logged_app1:
        company = st.session_state.current_company or urllib.parse.unquote(params.get("company","General"))
        student_portal(company, st.session_state.device_id)
        return

    # ── Get device_id + GPS via hidden inputs ─────────────
    # This runs on EVERY load - fills device_id and GPS into session state
    did_val, lat_val, lon_val, err_val = gps_and_device_component()

    # Store device_id from localStorage (permanent)
    if did_val and not st.session_state.device_id:
        st.session_state.device_id = did_val
        st.rerun()

    # Store GPS result (if location required)
    if st.session_state.loc_required and not st.session_state.gps_lat:
        if lat_val and lon_val:
            st.session_state.gps_lat = float(lat_val)
            st.session_state.gps_lon = float(lon_val)
            st.rerun()
        elif err_val:
            st.session_state.gps_error = err_val
            st.rerun()

    # ── STUDENT: QR check ────────────────────────────────
    valid, err = check_qr_access()

    if not valid:
        st.error("🔒 **Access Denied**")
        if err: st.warning(err)
        st.info("📱 Scan the QR code shown by your admin.")
        st.markdown("---")
        with st.expander("🔑 Admin Login"):
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

    # ── Location check ────────────────────────────────────
    if loc_required and not st.session_state.location_verified:

        if st.session_state.gps_lat and st.session_state.gps_lon:
            # GPS received - verify distance
            ok, dist = in_range(st.session_state.gps_lat, st.session_state.gps_lon)
            if ok:
                st.session_state.location_verified = True
                st.rerun()
            else:
                st.error("🚫 **Location Blocked**")
                st.markdown(f"You are **{int(dist)}m** away from SNIST. Must be within **{RADIUS_M}m**.")
                st.stop()

        elif st.session_state.gps_error:
            msgs = {"1":"❌ Location permission denied. Enable location in browser settings.", "2":"❌ GPS unavailable. Enable GPS on device.", "3":"❌ GPS timed out. Enable GPS and try again.", "99":"❌ GPS not supported."}
            st.error(msgs.get(st.session_state.gps_error, "❌ Location error."))
            st.stop()

        else:
            # Still waiting for GPS from JS
            st.info(f"🏢 **Company:** {company}")
            st.warning("📍 **Verifying location...**")
            st.info("Please **Allow** location access when your browser asks.")
            with st.spinner("Getting location (2-5 seconds)..."):
                time.sleep(1)
            st.rerun()

    if loc_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR verified!")

    student_portal(company, st.session_state.device_id)

if __name__ == "__main__":
    main()
