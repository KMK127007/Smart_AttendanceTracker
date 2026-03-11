import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta
import time, urllib.parse
from math import radians, sin, cos, sqrt, atan2
from supabase import create_client, Client

# streamlit-js-eval for GPS
try:
    from streamlit_js_eval import streamlit_js_eval
    JS_EVAL_AVAILABLE = True
except ImportError:
    JS_EVAL_AVAILABLE = False

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))
def ist_now(): return datetime.now(IST)
def ist_time_str(): return ist_now().strftime("%H:%M:%S")
def ist_date_str(): return ist_now().strftime("%d-%m-%Y")
def ist_datetime_str(): return ist_now().strftime("%d-%m-%Y %H:%M:%S")

# Supabase client
try:
    supabase: Client = create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["key"]
    )
except Exception as e:
    st.error(f"Supabase connection error: {e}")
    st.stop()

# Admin credentials
try:
    ADMINS = {st.secrets["admin_user"]["username"]: {"password": st.secrets["admin_user"]["password"]}}
except KeyError as e:
    st.error(f"Missing secret: {e}"); st.stop()

COLLEGE_LAT = 17.4558417
COLLEGE_LON = 78.6670873
RADIUS_M    = 500

# Session state defaults
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

# ── Supabase Functions ────────────────────────────────────
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
    """Check/create device binding"""
    if not device_id:
        return False, "❌ Device ID missing. Please refresh."
    roll_lower = rollnumber.strip().lower()
    try:
        # Check if device already used
        dev_check = supabase.table('device_binding').select('rollnumber').eq('device_id', device_id).execute()
        if dev_check.data:
            bound_roll = dev_check.data[0]['rollnumber']
            if bound_roll != roll_lower:
                return False, f"❌ This device is already used by **{bound_roll.upper()}**. One device = one student only."
            return True, "ok"
        
        # Check if roll already on different device
        roll_check = supabase.table('device_binding').select('device_id').eq('rollnumber', roll_lower).execute()
        if roll_check.data:
            return False, "❌ Your roll number is already registered on a different device. Contact admin to unbind."
        
        # Create new binding
        supabase.table('device_binding').insert({
            'rollnumber': roll_lower,
            'device_id': device_id,
            'bound_at': ist_datetime_str()
        }).execute()
        return True, "ok"
    except Exception as e:
        return False, f"❌ Device binding error: {str(e)}"

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
                window_secs = int(params.get("window", "30"))
                if elapsed <= window_secs:
                    st.session_state.qr_access_granted = True
                    st.session_state.current_company = company
                    st.session_state.loc_required = loc_enabled
                    return True, None
                return False, f"⏰ QR expired ({elapsed}s old). Ask admin for the latest QR."
            except: return False, "Invalid QR format."
    if st.session_state.qr_access_granted: return True, None
    return False, "Please scan the QR code shown by your admin."

def mark_attendance(rollnumber, company, device_id):
    """Mark attendance with all security checks"""
    try:
        # Check if student exists
        student_check = supabase.table('students').select('rollnumber').eq('rollnumber', rollnumber.strip().lower()).execute()
        if not student_check.data:
            return False, f"❌ Roll number '{rollnumber}' not found."
        
        # Device binding check
        ok, msg = check_device_binding(rollnumber, device_id)
        if not ok: return False, msg
        
        # Check if already marked for this company
        dup_check = supabase.table('attendance').select('id').eq('rollnumber', rollnumber.strip().lower()).eq('company', company).execute()
        if dup_check.data:
            return False, f"⚠️ Attendance already marked for {company}."
        
        # Insert attendance
        supabase.table('attendance').insert({
            'rollnumber': rollnumber.strip().lower(),
            'company': company,
            'timestamp': ist_time_str(),
            'datestamp': ist_date_str(),
            'device_id': device_id
        }).execute()
        
        return True, "✅ Attendance marked successfully!"
    except Exception as e:
        return False, f"❌ Error: {str(e)}"

def check_location_with_js_eval(company):
    """GPS with button control to prevent 1000 simultaneous calls"""
    st.info(f"🏢 **Company:** {company}")
    st.warning("📍 **Location verification required.**")
    st.info("📍 Tap the button below, then Allow location when your browser asks.")

    if st.button("📍 Verify My Location", type="primary", key="start_gps_btn"):
        st.session_state["gps_requested"] = True
        st.rerun()

    if not st.session_state.get("gps_requested", False):
        st.stop()

    retry_key = f"gps_{st.session_state.get('gps_retry', 0)}"
    
    with st.spinner("Getting your location..."):
        gps_result = streamlit_js_eval(
            js_expressions="""
            new Promise((resolve) => {
                if (!navigator.geolocation) {
                    resolve({error: {code: 99}});
                    return;
                }
                navigator.geolocation.getCurrentPosition(
                    (pos) => resolve({coords: {latitude: pos.coords.latitude, longitude: pos.coords.longitude}}),
                    (err) => resolve({error: {code: err.code}}),
                    {enableHighAccuracy: false, timeout: 6000, maximumAge: 60000}
                );
            })
            """,
            want_output=True,
            key=retry_key
        )

    if gps_result is None:
        time.sleep(0.3)
        st.rerun()
        return

    st.session_state["gps_requested"] = False

    if "error" in gps_result:
        code = str(gps_result["error"].get("code", "?"))
        msgs = {"1": "❌ Permission denied. Enable location in browser settings.",
                "2": "❌ GPS unavailable. Enable WiFi or GPS on your device.",
                "3": "❌ Location timed out. Enable WiFi or GPS and try again.",
                "99": "❌ GPS not supported on this browser."}
        st.error(msgs.get(code, f"❌ Location error (code {code})."))
        if st.button("🔄 Try Again", key="retry_btn"):
            st.session_state["gps_retry"] = st.session_state.get("gps_retry", 0) + 1
            st.session_state["gps_requested"] = True
            st.rerun()
        st.stop()

    if "coords" in gps_result:
        lat, lon = gps_result["coords"]["latitude"], gps_result["coords"]["longitude"]
        ok, dist = in_range(lat, lon)
        if ok:
            st.session_state.location_verified = True
            st.session_state.gps_lat = lat
            st.session_state.gps_lon = lon
            st.rerun()
        else:
            st.error("🚫 Blocked — Location out of college.")
            st.stop()
    else:
        st.error("❌ Could not read location. Please try again.")
        st.stop()

# ── Student portal ────────────────────────────────────────
def student_portal(company, device_id):
    st.markdown('<h1 style="text-align:center">📱 QR Attendance Portal</h1>', unsafe_allow_html=True)
    st.markdown("### Mark Your Attendance")
    st.info(f"🏢 **Company / Drive:** {company}")

    roll = st.text_input("Roll Number", key="qr_roll", placeholder="e.g. 22311a0138")
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
        admin_tabs = st.tabs(["📂 Upload Students","📊 Today's Attendance","📋 All Records","✍️ Manual Entry","📱 Device Bindings"])

        with admin_tabs[0]:
            st.markdown("### 📂 Upload Students")
            st.info("Upload Excel/CSV with student data. Will bulk insert to database.")
            uf = st.file_uploader("Upload File", type=["csv", "xlsx"], key="stu_upload")
            if uf:
                try:
                    if uf.name.endswith('.csv'):
                        df = pd.read_csv(uf)
                    else:
                        df = pd.read_excel(uf)
                    
                    # Normalize columns
                    if 'Roll No' in df.columns:
                        df = df.rename(columns={'Roll No': 'rollnumber'})
                    elif 'rollnumber' not in df.columns:
                        st.error("❌ Must have 'Roll No' or 'rollnumber' column!")
                        st.stop()
                    
                    df['rollnumber'] = df['rollnumber'].astype(str).str.strip().str.lower()
                    
                    # Map columns to database schema
                    col_map = {
                        'S.No.': 'sno',
                        'Name': 'name',
                        'Course': 'course',
                        'Mobile': 'mobile',
                        'Email ID': 'email',
                        'Gender': 'gender',
                        'Current Term Score': 'current_term_score',
                        'Xth percentage': 'xth_percentage',
                        'XIIth percentage': 'xiith_percentage',
                        'Backlogs': 'backlogs'
                    }
                    df = df.rename(columns=col_map)
                    
                    # Select only columns that exist in DB
                    db_cols = ['rollnumber', 'name', 'course', 'mobile', 'email', 'gender', 
                               'current_term_score', 'xth_percentage', 'xiith_percentage', 'backlogs']
                    upload_cols = [c for c in db_cols if c in df.columns]
                    df_upload = df[upload_cols]
                    
                    st.success(f"✅ Found {len(df_upload)} students")
                    st.dataframe(df_upload.head(10), use_container_width=True)
                    
                    if st.button("📤 Upload to Database", key="do_upload"):
                        with st.spinner(f"Uploading {len(df_upload)} students..."):
                            # Replace NaN with None for JSON compliance
                            df_clean = df_upload.where(pd.notnull(df_upload), None)
                            data = df_clean.to_dict('records')
                            
                            # Clean mobile numbers
                            for student in data:
                                # Mobile as string (only if not None)
                                if student.get('mobile') is not None:
                                    try:
                                        if isinstance(student['mobile'], float):
                                            student['mobile'] = str(int(student['mobile']))
                                        else:
                                            student['mobile'] = str(student['mobile']).strip()
                                    except (ValueError, TypeError):
                                        student['mobile'] = None
                            
                            try:
                                # Batch insert (500 at a time)
                                batch_size = 500
                                success_count = 0
                                for i in range(0, len(data), batch_size):
                                    batch = data[i:i+batch_size]
                                    supabase.table('students').upsert(batch, on_conflict='rollnumber').execute()
                                    success_count += len(batch)
                                    st.info(f"Uploaded {success_count}/{len(data)} students...")
                                st.success(f"✅ {len(data)} students uploaded successfully!")
                            except Exception as e:
                                st.error(f"❌ Error: {str(e)}")
                except Exception as e:
                    st.error(f"❌ Error reading file: {str(e)}")

        with admin_tabs[1]:
            st.markdown("### 📅 Today's Attendance")
            try:
                companies = supabase.table('companies').select('name').execute()
                if companies.data:
                    comp = st.selectbox("Company:", [c['name'] for c in companies.data], key="today_comp")
                    today = ist_date_str()
                    
                    # Get attendance with student details
                    att = supabase.table('attendance').select('*').eq('company', comp).eq('datestamp', today).execute()
                    if att.data:
                        att_df = pd.DataFrame(att.data)
                        
                        # Get student details
                        rolls = att_df['rollnumber'].unique().tolist()
                        students = supabase.table('students').select('*').in_('rollnumber', rolls).execute()
                        stu_df = pd.DataFrame(students.data) if students.data else pd.DataFrame()
                        
                        if not stu_df.empty:
                            merged = att_df.merge(stu_df, on='rollnumber', how='left')
                            merged.insert(0, 'S.No', range(1, len(merged) + 1))
                            st.success(f"**{len(merged)} present**")
                            st.dataframe(merged, use_container_width=True, hide_index=True)
                            st.download_button("⬇️ Download", merged.to_csv(index=False).encode(), f"attendance_{comp}_{today}.csv", "text/csv")
                        else:
                            st.dataframe(att_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No attendance today.")
            except Exception as e:
                st.error(f"Error: {e}")

        with admin_tabs[2]:
            st.markdown("### 📋 All Attendance Records")
            try:
                companies = supabase.table('companies').select('name').execute()
                if companies.data:
                    for comp_row in companies.data:
                        comp = comp_row['name']
                        att = supabase.table('attendance').select('*').eq('company', comp).execute()
                        if att.data:
                            att_df = pd.DataFrame(att.data)
                            rolls = att_df['rollnumber'].unique().tolist()
                            students = supabase.table('students').select('*').in_('rollnumber', rolls).execute()
                            stu_df = pd.DataFrame(students.data) if students.data else pd.DataFrame()
                            
                            if not stu_df.empty:
                                merged = att_df.merge(stu_df, on='rollnumber', how='left')
                                merged.insert(0, 'S.No', range(1, len(merged) + 1))
                            else:
                                merged = att_df
                            
                            c1,c2,c3 = st.columns([2,1,1])
                            with c1: st.write(f"🏢 **{comp}**")
                            with c2: st.write(f"{len(merged)} records")
                            with c3: st.download_button("⬇️", merged.to_csv(index=False).encode(), f"attendance_{comp}.csv", "text/csv", key=f"dl_{comp}")
                            st.markdown("---")
            except Exception as e:
                st.error(f"Error: {e}")

        with admin_tabs[3]:
            st.markdown("### ✍️ Manual Entry")
            try:
                students = supabase.table('students').select('rollnumber').execute()
                rolls = [s['rollnumber'] for s in students.data] if students.data else []
                
                man_roll = st.selectbox("Roll Number:", [""] + rolls, key="man_roll") if rolls else st.text_input("Roll:", key="man_roll_txt")
                
                companies = supabase.table('companies').select('name').execute()
                comps = [c['name'] for c in companies.data] if companies.data else []
                
                mode = st.radio("Company:", ["Select Existing","Enter New"], horizontal=True, key="man_mode")
                man_company = None
                if mode == "Select Existing":
                    if comps: man_company = st.selectbox("Select:", comps, key="man_comp_sel")
                if mode == "Enter New":
                    nc = st.text_input("Company Name:", key="man_new_comp")
                    if nc.strip(): man_company = nc.strip()
                
                from datetime import date
                man_date = st.date_input("Date:", value=date.today(), key="man_date")
                
                if st.button("✅ Mark", type="primary", key="man_mark"):
                    if man_roll and man_company:
                        ds = man_date.strftime("%d-%m-%Y")
                        try:
                            supabase.table('attendance').insert({
                                'rollnumber': str(man_roll).strip().lower(),
                                'company': man_company,
                                'timestamp': ist_time_str(),
                                'datestamp': ds,
                                'device_id': 'MANUAL_ADMIN'
                            }).execute()
                            st.success(f"✅ {man_roll} marked for {man_company} on {ds}!")
                        except Exception as e:
                            st.error(f"❌ Error: {str(e)}")
                    else:
                        st.warning("Enter both roll and company.")
            except Exception as e:
                st.error(f"Error: {e}")

        with admin_tabs[4]:
            st.markdown("### 📱 Device Bindings")
            st.info("One device = one student. Unbind here if student changes device.")
            try:
                bindings = supabase.table('device_binding').select('*').execute()
                if bindings.data:
                    df = pd.DataFrame(bindings.data)
                    st.dataframe(df, use_container_width=True)
                    st.info(f"**{len(df)} devices bound**")
                    
                    to_unbind = st.selectbox("Roll to Unbind:", [""]+df['rollnumber'].tolist(), key="unbind_sel")
                    if to_unbind and st.button("🔓 Unbind", key="unbind_btn"):
                        supabase.table('device_binding').delete().eq('rollnumber', to_unbind).execute()
                        st.success(f"✅ '{to_unbind}' unbound.")
                        st.rerun()
                else:
                    st.info("No devices bound yet.")
            except Exception as e:
                st.error(f"Error: {e}")

    st.markdown("---")
    st.caption("📱 Smart Attendance Tracker — QR Portal | Powered by Streamlit + Supabase")

# ── Main ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="QR Attendance Portal", page_icon="📱", layout="centered")

    # Device ID from session (simple UUID)
    if not st.session_state.device_id:
        import uuid
        st.session_state.device_id = "SES_" + uuid.uuid4().hex[:20].upper()

    # ADMIN: no checks
    if st.session_state.admin_logged_app1:
        company = st.session_state.current_company or "General"
        student_portal(company, st.session_state.device_id)
        return

    # STUDENT: QR check
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

    # Location check
    if loc_required and not st.session_state.location_verified:
        if not JS_EVAL_AVAILABLE:
            st.error("❌ Location library not installed. Add `streamlit-js-eval==0.1.7` to requirements.txt")
            st.stop()
        check_location_with_js_eval(company)
        return

    if loc_required:
        st.success("✅ QR & Location verified!")
    else:
        st.success("✅ QR verified!")

    student_portal(company, st.session_state.device_id)

if __name__ == "__main__":
    main()
