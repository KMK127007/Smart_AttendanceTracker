import streamlit as st
import pandas as pd
from datetime import datetime, date, timezone, timedelta
import time, os, warnings, urllib.parse
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

warnings.filterwarnings('ignore')
os.environ["PYTHONWARNINGS"] = "ignore"

# streamlit-js-eval: reliable JS execution in Streamlit
# Add to requirements.txt: streamlit-js-eval==0.1.7
try:
    from streamlit_js_eval import get_geolocation
    JS_EVAL_AVAILABLE = True
except ImportError:
    JS_EVAL_AVAILABLE = False

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

COLLEGE_LAT = 17.4558417
COLLEGE_LON = 78.6670873
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

for k, v in {
    "admin_logged_app1": False,
    "qr_access_granted": False,
    "location_verified": False,
    "current_company": "General",
    "loc_required": False,
    "device_id": None,
    "gps_lat": None,
    "gps_lon": None,
}.items():
    if k not in st.session_state: st.session_state[k] = v

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
            if c not in df.columns: df[c] = ""
        return df[["rollnumber","timestamp","datestamp","company"]]
    except:
        df = pd.DataFrame(columns=["rollnumber","timestamp","datestamp","company"])
        df.to_csv(path, index=False); return df

def load_attendance_with_all_fields(company):
    path = att_csv(company)
    try:
        att_df = pd.read_csv(path)
        if att_df.empty:
            return att_df
        try:
            stu_df = pd.read_csv(STUDENTS_CSV)
            # Normalize rollnumber column name
            if 'rollnumber' not in stu_df.columns:
                for col in stu_df.columns:
                    if 'roll' in col.lower():
                        stu_df = stu_df.rename(columns={col: 'rollnumber'})
                        break

            # Normalize S.No column name to exactly 'S.No' (handles s.no, S.no, S.No, sno etc.)
            for col in stu_df.columns:
                if col.lower().replace('.','').replace(' ','') in ['sno','sno','serialno','serialnumber']:
                    stu_df = stu_df.rename(columns={col: 'S.No'})
                    break

            # Normalize merge key (case-insensitive roll number)
            att_df['_roll_key'] = att_df['rollnumber'].astype(str).str.strip().str.lower()
            stu_df['_roll_key'] = stu_df['rollnumber'].astype(str).str.strip().str.lower()

            # Drop rollnumber from student df to avoid duplicate after merge
            stu_df_merge = stu_df.drop(columns=['rollnumber'])

            merged = att_df.merge(stu_df_merge, on='_roll_key', how='left')
            merged = merged.drop(columns=['_roll_key'])

            if 'company' not in merged.columns:
                merged['company'] = company

            # ── Clean up S.No: keep only one, place it first ──
            # Find all columns that look like S.No (case-insensitive)
            sno_cols = [c for c in merged.columns
                        if c.lower().replace('.','').replace(' ','') in ['sno','serialno','serialnumber']]

            if sno_cols:
                # Keep the first one, rename to 'S.No', drop the rest
                merged = merged.rename(columns={sno_cols[0]: 'S.No'})
                extra_sno = sno_cols[1:]
                if extra_sno:
                    merged = merged.drop(columns=extra_sno)
            else:
                # No S.No in CSV at all - create one
                merged.insert(0, 'S.No', 0)

            # Always reset S.No to 1,2,3... based on report row order
            merged['S.No'] = range(1, len(merged) + 1)

            # Move S.No to first column
            cols = ['S.No'] + [c for c in merged.columns if c != 'S.No']
            merged = merged[cols]

            return merged
        except Exception:
            return att_df
    except Exception:
        return pd.DataFrame()

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

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1,lon1,lat2,lon2 = map(radians,[lat1,lon1,lat2,lon2])
    dlat,dlon = lat2-lat1,lon2-lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

def in_range(user_lat, user_lon):
    d = haversine(COLLEGE_LAT, COLLEGE_LON, user_lat, user_lon)
    return d <= RADIUS_M, d

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

def check_qr_access():
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

# ── GPS page: full visible component, updates URL directly ─
# This is rendered as its own full screen - no iframe blocking issues
# The component has height=400 so it's a real visible element
# JS runs inside the component iframe, but uses window.top to navigate
def check_location_with_js_eval(company):
    """
    Uses streamlit-js-eval to get GPS directly - no iframe, no URL hacks.
    Returns True if verified, False if blocked, None if still waiting.
    """
    st.info(f"🏢 **Company:** {company}")
    st.warning("📍 **Location verification required.**")
    st.info("Tap the button below. Allow location when your browser asks.")

    if st.button("📍 Verify My Location", type="primary", key="gps_btn"):
        with st.spinner("Getting your location (2-5 seconds)..."):
            try:
                loc = get_geolocation()
                if loc and "coords" in loc:
                    lat = loc["coords"]["latitude"]
                    lon = loc["coords"]["longitude"]
                    ok, dist = in_range(lat, lon)
                    if ok:
                        st.session_state.location_verified = True
                        st.session_state.gps_lat = lat
                        st.session_state.gps_lon = lon
                        st.success(f"✅ Location verified! {int(dist)}m from SNIST.")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"🚫 **Blocked** — You are {int(dist)}m away. Must be within {RADIUS_M}m of SNIST.")
                        st.stop()
                else:
                    st.error("❌ Could not get location. Please allow location access and try again.")
            except Exception as e:
                st.error(f"❌ Location error: {str(e)}. Please enable GPS and try again.")
    st.stop()


# ── Student portal ────────────────────────────────────────
def student_portal(company, device_id):
    st.markdown('<div class="header">📱 QR Attendance Portal</div>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")
    st.info(f"🏢 **Company / Drive:** {company}")

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
                st.markdown("---"); st.success(f"📋 **{len(cur)} students**"); st.dataframe(cur, width=700)

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
                        st.success(f"**{len(td)} present**"); st.dataframe(td, width=900, hide_index=True)
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
            mode = st.radio("Company:", ["Select Existing","Enter New"], horizontal=True, key="man_comp_mode", label_visibility="collapsed")
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
    params = st.query_params

    # Device ID: stable UUID for this browser session
    # Single-entry enforcement is done by the attendance CSV check (not device alone)
    if not st.session_state.device_id:
        import uuid
        st.session_state.device_id = "SES_" + uuid.uuid4().hex[:20].upper()

    # ── ADMIN: no checks, stays forever ──────────────────
    if st.session_state.admin_logged_app1:
        company = st.session_state.current_company or urllib.parse.unquote(params.get("company","General"))
        student_portal(company, st.session_state.device_id)
        return

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
        if not JS_EVAL_AVAILABLE:
            st.error("❌ Location library not installed. Add `streamlit-js-eval==0.1.7` to requirements.txt")
            st.stop()
        check_location_with_js_eval(company)
        return  # st.stop() is inside the function

    if loc_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR verified!")

    student_portal(company, st.session_state.device_id)

if __name__ == "__main__":
    main()
