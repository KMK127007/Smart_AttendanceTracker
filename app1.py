import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, date, timezone, timedelta
import time, json, os, warnings
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

STUDENTS_CSV = "students_new.csv"
DEVICE_CSV   = "device_binding.csv"

def att_csv(company): return f"attendance_{company.strip().replace(' ','_')}.csv"

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
    "show_location_form": False,
    "device_id": None,           # stable JS fingerprint stored here
    "current_company": "General",
    "loc_required": False,
    "attendance_marked": False,  # once marked, block re-entry
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── CSS helpers ───────────────────────────────────────────
BLOCKED_STYLE = """
<style>
.blocked-box {
    background: #fff0f0;
    border: 2px solid #ff4444;
    border-radius: 10px;
    padding: 20px;
    text-align: center;
    margin: 20px 0;
}
</style>
"""

# ── Device fingerprint via JavaScript ────────────────────
# We use localStorage so the same browser always has the same ID
# even across page refreshes and reruns
DEVICE_JS = """
<script>
    const KEY = 'satt_device_id';
    let did = localStorage.getItem(KEY);
    if (!did) {
        // Generate once, store forever in this browser
        did = 'D' + Date.now().toString(36) + Math.random().toString(36).substr(2,10);
        localStorage.setItem(KEY, did);
    }
    // Send to Streamlit via URL param trick
    // We write it to the page title so Streamlit can't read it directly,
    // but we store in window for the hidden input below
    window._device_id = did;

    // Write to a hidden input that we can read via st.query_params workaround
    // Best approach: write to URL hash so Streamlit can read it
    if (!window.location.hash.includes('dev=')) {
        window.location.hash = 'dev=' + did;
    }
</script>
"""

def inject_device_js():
    """Inject JS to write device ID to URL hash, then read it back"""
    components.html(DEVICE_JS, height=0)

def get_device_id_from_hash():
    """Read device ID from URL hash if present"""
    params = st.query_params
    # Check if device_id was passed as a query param from JS
    # Since we can't directly read JS values, we use a form approach
    # Fallback: use session-level ID (less secure but functional)
    if st.session_state.device_id:
        return st.session_state.device_id
    return None

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
    qr_comp = st.session_state.current_company
    if qr_comp and qr_comp not in companies: companies.append(qr_comp)
    return sorted(set(companies))

# ── Device binding (session-level, enforced per roll number) ─
def check_device_binding(rollnumber):
    """
    Device ID is stored in session_state.device_id.
    It is set ONCE when the student first enters the portal (from JS fingerprint).
    Same browser session = same device_id.
    Different browser/device = different session = different device_id.
    """
    device_id = st.session_state.device_id
    if not device_id:
        return False, "❌ Device ID not found. Please refresh and try again."

    df = load_device_binding()
    roll_lower = rollnumber.strip().lower()
    existing = df[df['rollnumber'].str.lower() == roll_lower]

    if existing.empty:
        # First time - bind this roll number to this device
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

# ── Get browser GPS via JS ─────────────────────────────────
def request_gps_location():
    """
    Inject JS to get real GPS from the browser.
    GPS result is written to URL params and page is reloaded.
    """
    gps_js = """
    <script>
    function getLocation() {
        document.getElementById('gps-status').innerText = '📡 Getting your location... please wait';
        document.getElementById('gps-status').style.color = '#ff8c00';

        if (!navigator.geolocation) {
            document.getElementById('gps-status').innerText = '❌ GPS not supported on this browser.';
            return;
        }

        navigator.geolocation.getCurrentPosition(
            function(pos) {
                var lat = pos.coords.latitude.toFixed(7);
                var lon = pos.coords.longitude.toFixed(7);
                var acc = Math.round(pos.coords.accuracy);

                document.getElementById('gps-status').innerText = 
                    '✅ Location found! Verifying... (' + lat + ', ' + lon + ')';
                document.getElementById('gps-status').style.color = 'green';

                // Build new URL keeping existing params and adding GPS coords
                var url = new URL(window.parent.location.href);
                url.searchParams.set('gps_lat', lat);
                url.searchParams.set('gps_lon', lon);
                url.searchParams.set('gps_acc', acc);

                // Full page reload with coordinates in URL
                window.parent.location.href = url.toString();
            },
            function(err) {
                var msg = '❌ ';
                if (err.code === 1) msg += 'Location permission denied. Please allow location access.';
                else if (err.code === 2) msg += 'Location unavailable. Enable GPS on your device.';
                else if (err.code === 3) msg += 'Location request timed out. Try again.';
                else msg += err.message;
                document.getElementById('gps-status').innerText = msg;
                document.getElementById('gps-status').style.color = 'red';
            },
            {enableHighAccuracy: true, timeout: 20000, maximumAge: 0}
        );
    }
    </script>
    <div id="gps-status" style="font-size:15px; margin-bottom:10px;">Ready. Click the button below.</div>
    <button onclick="getLocation()" style="
        background:#4CAF50; color:white; border:none; padding:14px 24px;
        border-radius:8px; font-size:16px; cursor:pointer; width:100%;
        font-weight:bold;">
        📍 Get My Location Automatically
    </button>
    """
    components.html(gps_js, height=120)

# ── QR access check ───────────────────────────────────────
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
                    st.session_state.qr_access_granted = True
                    st.session_state.current_company = company
                    st.session_state.loc_required = loc_enabled
                    return True, None
                return False, f"⏰ QR expired ({elapsed}s old). Ask admin for the latest QR."
            except:
                return False, "Invalid QR format."

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

    # ── Single device check ──
    ok, msg = check_device_binding(rollnumber)
    if not ok: return False, msg

    # ── Duplicate check today ──
    att_df = load_attendance(company)
    today = ist_date_str()
    if not att_df.empty:
        dup = att_df[
            (att_df['rollnumber'].str.lower() == rollnumber.strip().lower()) &
            (att_df['datestamp'] == today)
        ]
        if not dup.empty:
            return False, f"⚠️ Attendance already marked today for {company}!"

    # ── Save ──
    new = pd.DataFrame([{
        'rollnumber': rollnumber.strip(),
        'timestamp': ist_time_str(),
        'datestamp': today,
        'company': company
    }])
    att_df = pd.concat([att_df, new], ignore_index=True)
    att_df.to_csv(att_csv(company), index=False)
    return True, "✅ Attendance marked successfully!"

# ── Student portal ────────────────────────────────────────
def student_portal(company):
    st.markdown('<div class="header">📱 QR Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")
    st.info(f"🏢 **Company / Drive:** {company}")

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

    # ── Admin section (shown only AFTER student portal) ───
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
            st.info("Upload any CSV with a **rollnumber** column. All fields are stored in attendance records.")
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
                        col1, col2, col3 = st.columns([2,1,1])
                        with col1: st.write(f"🏢 **{comp}**")
                        with col2: st.write(f"{len(merged)} records")
                        with col3:
                            st.download_button("⬇️ Download", merged.to_csv(index=False).encode(), f"attendance_{comp}.csv", "text/csv", key=f"dl_{comp}")
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
                if all_comps:
                    man_company = st.selectbox("Select:", all_comps, key="man_comp_sel")
                else:
                    st.warning("No companies yet.")

            if man_comp_mode == "Enter New":
                nc = st.text_input("Company Name:", key="man_new_comp")
                if nc.strip(): man_company = nc.strip()

            man_date = st.date_input("Date:", value=date.today(), key="man_date")

            if st.button("✅ Mark Attendance", type="primary", key="man_mark_btn"):
                if man_roll and man_company:
                    ds = man_date.strftime("%d-%m-%Y")
                    att_df = load_attendance(man_company)
                    already = att_df[
                        (att_df['rollnumber'].str.lower() == str(man_roll).lower()) &
                        (att_df['datestamp'] == ds)
                    ] if not att_df.empty else pd.DataFrame()
                    if not already.empty:
                        st.warning(f"⚠️ Already marked {man_roll} on {ds} for {man_company}")
                    else:
                        new = pd.DataFrame([{'rollnumber': str(man_roll).strip(), 'timestamp': ist_time_str(), 'datestamp': ds, 'company': man_company}])
                        att_df = pd.concat([att_df, new], ignore_index=True)
                        att_df.to_csv(att_csv(man_company), index=False)
                        st.success(f"✅ **{man_roll}** marked for **{man_company}** on **{ds}**!")
                        st.rerun()
                else:
                    st.warning("⚠️ Enter both roll number and company.")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker — QR Portal | Powered by Streamlit")

# ── Location verification page ────────────────────────────
def location_verification_page(company):
    st.markdown('<div class="header">📍 Location Verification</div>', unsafe_allow_html=True)
    st.info(f"🏢 **Company:** {company}")
    st.warning("Your admin has enabled location verification. You must be physically present at SNIST (within 500m).")

    params = st.query_params

    # ── GPS coordinates received from JS ──
    if "gps_lat" in params and "gps_lon" in params:
        try:
            user_lat = float(params["gps_lat"])
            user_lon = float(params["gps_lon"])
            user_acc = int(params.get("gps_acc", 999))

            ok, dist = in_range(user_lat, user_lon)

            st.markdown(f"📡 **Your location:** `{user_lat:.6f}, {user_lon:.6f}` (±{user_acc}m accuracy)")

            if ok:
                # ✅ Verified - set flag, clear GPS params, proceed
                st.session_state.location_verified = True
                st.success(f"✅ Location verified! You are **{int(dist)}m** from SNIST.")
                # Clear GPS params from URL so it doesn't interfere
                st.query_params.clear()
                # Re-set QR access params
                st.session_state.qr_access_granted = True
                st.rerun()
            else:
                # ❌ HARD BLOCK
                st.markdown(BLOCKED_STYLE, unsafe_allow_html=True)
                st.markdown(f"""
                <div class="blocked-box">
                    <h2>🚫 Location Blocked</h2>
                    <p>You are <b>{int(dist)}m</b> away from SNIST.</p>
                    <p>Required: within <b>{RADIUS_M}m</b></p>
                    <p>Please come to college and scan again.</p>
                </div>
                """, unsafe_allow_html=True)
                st.stop()
        except Exception as e:
            st.error(f"Error reading GPS: {e}. Please try again.")
            # Fall through to show button again

    # ── Show GPS button ──
    st.markdown("### 📍 Step 1: Allow Location Access")
    st.info("Click the button below. Your browser will ask for location permission — tap **Allow**.")
    request_gps_location()

    st.markdown("---")
    st.markdown("### ⏳ Step 2: Wait for Verification")
    st.info("After allowing location, the page will reload automatically and verify your location.")
    st.stop()

# ── Main ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="QR Attendance Portal", page_icon="📱", layout="centered")

    # Inject device fingerprint JS
    inject_device_js()

    # Read device_id from URL hash if available
    params = st.query_params
    if "device_id" in params and not st.session_state.device_id:
        st.session_state.device_id = params["device_id"]

    # If device_id still not set, generate session-level one
    # (less secure but prevents complete failure)
    if not st.session_state.device_id:
        import hashlib
        # Use a combination of available browser signals
        session_key = str(id(st.session_state))
        st.session_state.device_id = "sess_" + hashlib.md5(session_key.encode()).hexdigest()[:16]

    # ── ADMIN PATH: no QR, no location, no time limit ──────
    if st.session_state.admin_logged_app1:
        import urllib.parse
        company = st.session_state.current_company or urllib.parse.unquote(params.get("company", "General"))
        student_portal(company)
        return

    # ── STUDENT PATH: must scan valid QR ───────────────────
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
                    st.success("✅ Logged in!")
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
        st.stop()

    # ── STUDENT: location check ────────────────────────────
    company = st.session_state.current_company
    loc_required = st.session_state.loc_required

    if loc_required and not st.session_state.location_verified:
        # Show location verification page - HARD BLOCK if wrong location
        location_verification_page(company)
        return  # never reaches below if location fails

    if loc_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR Code verified!")

    student_portal(company)

if __name__ == "__main__":
    main()
