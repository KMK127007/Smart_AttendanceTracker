import streamlit as st
import pandas as pd
from datetime import datetime, date, timezone, timedelta
import time, qrcode, json, base64, os, warnings
from io import BytesIO
from pathlib import Path

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

def local_css():
    try:
        p = Path(__file__).parent / "style.css"
        if p.exists():
            st.markdown(f"<style>{p.read_text()}</style>", unsafe_allow_html=True)
    except: pass

local_css()

# Files
STUDENTS_CSV     = "students_new.csv"
DEVICE_CSV       = "device_binding.csv"
LOG_CSV          = "activity_log.csv"
QR_SETTINGS_FILE = "qr_settings.json"
COMPANIES_FILE   = "companies.json"

def att_csv(company): return f"attendance_{company.strip().replace(' ','_')}.csv"

# Session state
for k, v in {
    "admin_logged": False, "admin_user": None,
    "qr_active": False, "qr_start_time": None,
    "qr_window_seconds": 60, "qr_location_enabled": False,
    "qr_token": None, "qr_image": None,
    "qr_last_refresh": None, "qr_company": None,
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── Helpers ──────────────────────────────────────────────
def load_companies():
    try:
        with open(COMPANIES_FILE) as f: return json.load(f)
    except: return []

def save_companies(lst):
    with open(COMPANIES_FILE, "w") as f: json.dump(sorted(set(lst)), f)

def add_company(name):
    if not name.strip(): return
    c = load_companies()
    if name.strip() not in c:
        c.append(name.strip()); save_companies(c)

def load_students():
    try:
        df = pd.read_csv(STUDENTS_CSV)
        if 'rollnumber' not in df.columns:
            for col in df.columns:
                if 'roll' in col.lower(): return df.rename(columns={col:'rollnumber'})
        return df
    except: return pd.DataFrame(columns=["rollnumber"])

def save_students(df): df.to_csv(STUDENTS_CSV, index=False)

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

def load_device_binding():
    try: return pd.read_csv(DEVICE_CSV)
    except:
        df = pd.DataFrame(columns=["rollnumber","device_id","bound_at"])
        df.to_csv(DEVICE_CSV, index=False); return df

def save_device_binding(df): df.to_csv(DEVICE_CSV, index=False)

def log_action(action, details=""):
    row = {"timestamp": ist_datetime_str(), "action": action, "details": details}
    try:
        df = pd.read_csv(LOG_CSV) if Path(LOG_CSV).exists() else pd.DataFrame()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LOG_CSV, index=False)
    except: pass

def save_qr_settings(location_enabled, window_seconds, company):
    with open(QR_SETTINGS_FILE, "w") as f:
        json.dump({"location_enabled": location_enabled,
                   "window_seconds": window_seconds,
                   "company": company,
                   "updated_at": ist_datetime_str()}, f)

def make_qr(token):
    url = f"https://smartapp12.streamlit.app?access={token}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode()

# ── Login/Logout ─────────────────────────────────────────
def admin_login():
    st.sidebar.header("🔐 Admin Login")
    u = st.sidebar.text_input("Username", key="adm_u")
    p = st.sidebar.text_input("Password", type="password", key="adm_p")
    if st.sidebar.button("Login"):
        if u in ADMINS and ADMINS[u]["password"] == p:
            st.session_state.admin_logged = True
            st.session_state.admin_user = u
            log_action("admin_login", u); st.rerun()
        else: st.sidebar.error("Invalid credentials ❌")

def admin_logout():
    if st.sidebar.button("🚪 Logout"):
        st.session_state.admin_logged = False
        st.session_state.qr_active = False
        log_action("admin_logout", ""); st.rerun()

# ── Admin Panel ───────────────────────────────────────────
def admin_panel():
    st.markdown('<div class="header">🎯 QR Attendance System — Admin Panel</div>', unsafe_allow_html=True)
    st.write(f"Logged in as **{st.session_state.admin_user}**")
    st.markdown("---")

    tabs = st.tabs(["📱 Generate QR", "👥 Students", "📊 Attendance", "✍️ Manual Entry", "📱 Devices", "📋 Logs"])

    # ── TAB 1: Generate QR ────────────────────────────────
    with tabs[0]:
        st.markdown("### 📱 QR Code Generator")

        # ── Stage 1 ──
        st.markdown("#### 📂 Stage 1 — Verify Students Database")
        students = load_students()
        if not students.empty and 'rollnumber' in students.columns:
            st.success(f"✅ **{len(students)} students** loaded in smartapp12")
            st.caption("To update: login to smartapp12 as admin → Upload Students CSV")
        else:
            st.warning("⚠️ No students loaded yet. Upload CSV in smartapp12 admin panel first.")

        st.markdown("---")

        # ── Stage 2 ──
        st.markdown("#### ⚙️ Stage 2 — Configure Settings")

        col_left, col_right = st.columns(2)

        with col_left:
            # Time window
            st.markdown("**⏱️ Time Window**")
            time_opts = {"1 minute":60,"3 minutes":180,"5 minutes":300,"10 minutes":600,"15 minutes":900,"30 minutes":1800}
            sel_time = st.selectbox("QR session duration:", list(time_opts.keys()), key="tw_sel")
            sel_secs = time_opts[sel_time]
            st.caption(f"QR auto-refreshes every 30s within {sel_time}")

            st.markdown("---")

            # Company dropdown
            st.markdown("**🏢 Company / Drive**")
            companies = load_companies()
            mode = st.radio("", ["Select Existing Company", "Create New Company"], horizontal=True, key="comp_mode")
            sel_company = None

            if mode == "Select Existing Company":
                if companies:
                    sel_company = st.selectbox("Select:", companies, key="comp_sel")
                else:
                    st.warning("No companies yet. Switch to 'Create New Company'.")

            else:  # Create new
                new_name = st.text_input("Company Name:", placeholder="e.g. TCS, Infosys, Wipro", key="new_comp")
                if new_name.strip():
                    sel_company = new_name.strip()
                    if st.button("➕ Save Company", key="save_comp_btn"):
                        add_company(new_name.strip())
                        st.success(f"✅ '{new_name.strip()}' saved!")
                        st.rerun()

            if sel_company:
                st.success(f"🏢 **{sel_company}**")

        with col_right:
            st.markdown("**📍 Location Verification**")
            loc_enabled = st.toggle("Enable Location Check", value=False, key="loc_toggle",
                                    help="Students must be within 500m of SNIST to mark attendance")
            if loc_enabled:
                st.success("📍 **ENABLED**")
                st.info("📌 SNIST\nLat: 17.4553223\nLon: 78.6664965\nRadius: 500m")
            else:
                st.info("📍 **DISABLED**\nStudents mark from anywhere")

        st.markdown("---")

        # ── Stage 3 ──
        st.markdown("#### 🔲 Stage 3 — Generate QR")

        can_go = not students.empty and sel_company is not None
        if not can_go:
            if students.empty: st.warning("⚠️ No students loaded.")
            if sel_company is None: st.warning("⚠️ Select or create a company.")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔲 Start QR Session", type="primary", key="start_qr", disabled=not can_go):
                save_qr_settings(loc_enabled, sel_secs, sel_company)
                add_company(sel_company)
                ts = int(time.time())
                token = f"qr_{ts}"
                st.session_state.update({
                    "qr_active": True, "qr_start_time": ts,
                    "qr_window_seconds": sel_secs, "qr_location_enabled": loc_enabled,
                    "qr_company": sel_company, "qr_token": token,
                    "qr_image": make_qr(token), "qr_last_refresh": ts,
                })
                log_action("start_qr", f"{sel_company} | {sel_time} | loc:{loc_enabled}")
                st.rerun()
        with c2:
            if st.session_state.qr_active:
                if st.button("⏹️ Stop Session", key="stop_qr"):
                    st.session_state.qr_active = False
                    log_action("stop_qr", ""); st.rerun()

        # ── Live QR display ──
        if st.session_state.qr_active:
            now = int(time.time())
            total_elapsed = now - st.session_state.qr_start_time
            remaining = st.session_state.qr_window_seconds - total_elapsed

            if remaining <= 0:
                st.error("⏰ Session expired. Start a new one.")
                st.session_state.qr_active = False; st.rerun()

            since_refresh = now - st.session_state.qr_last_refresh
            if since_refresh >= 30:
                new_token = f"qr_{now}"
                st.session_state.qr_token = new_token
                st.session_state.qr_image = make_qr(new_token)
                st.session_state.qr_last_refresh = now
                log_action("qr_refresh", st.session_state.qr_company)

            st.markdown("---")
            st.markdown("### 📱 Active QR Code")

            m, s = int(remaining // 60), int(remaining % 60)
            next_in = max(0, 30 - int(since_refresh))

            qr_col, info_col = st.columns([2, 1])
            with qr_col:
                st.markdown(f'<img src="data:image/png;base64,{st.session_state.qr_image}" width="280"/>', unsafe_allow_html=True)
            with info_col:
                st.metric("⏱️ Session Remaining", f"{m}m {s}s")
                st.metric("🔄 Next QR in", f"{next_in}s")
                st.info(f"🏢 **{st.session_state.qr_company}**")
                st.success("📍 ON") if st.session_state.qr_location_enabled else st.info("📍 OFF")
                st.caption("Auto-refreshes every 30s ✅")

            time.sleep(1); st.rerun()

    # ── TAB 2: Students ───────────────────────────────────
    with tabs[1]:
        st.markdown("### 👥 Students")
        df = load_students()
        if not df.empty:
            st.success(f"**{len(df)} students**")
            st.dataframe(df, width=1000)
            st.download_button("⬇️ Download", df.to_csv(index=False).encode(), "students_new.csv", "text/csv", key="dl_stu")

            st.markdown("---")
            nr = st.text_input("Add Roll Number:", key="add_roll")
            if st.button("➕ Add", key="add_stu"):
                if nr:
                    if nr.lower() in df['rollnumber'].str.lower().values:
                        st.warning("Already exists!")
                    else:
                        df = pd.concat([df, pd.DataFrame([{'rollnumber': nr.strip()}])], ignore_index=True)
                        save_students(df); st.success(f"✅ Added '{nr}'"); st.rerun()

            td = st.selectbox("Remove:", [""] + df['rollnumber'].tolist(), key="del_roll")
            if td and st.button("🗑️ Remove", key="del_stu"):
                df = df[df['rollnumber'] != td]
                save_students(df); st.success(f"✅ Removed '{td}'"); st.rerun()
        else:
            st.info("No students. Upload CSV in smartapp12 admin.")

    # ── TAB 3: Attendance ─────────────────────────────────
    with tabs[2]:
        st.markdown("### 📊 Attendance by Company")
        companies = load_companies()
        if not companies:
            st.info("No companies yet.")
        else:
            sc = st.selectbox("Company:", companies, key="att_comp")
            df = load_attendance(sc)
            today = ist_date_str()
            today_df = df[df['datestamp'] == today] if not df.empty else pd.DataFrame()

            st.success(f"📅 Today ({today}) — **{len(today_df)} present**") if not today_df.empty else st.info("No attendance today.")
            if not today_df.empty:
                st.dataframe(today_df, width=1000)
                st.download_button("⬇️ Today's", today_df.to_csv(index=False).encode(), f"att_{sc}_{today}.csv", "text/csv", key="dl_td")

            st.markdown("---")
            if not df.empty:
                st.markdown(f"### All Records — {sc}")
                st.dataframe(df, width=1000)
                st.info(f"**{len(df)} total records**")
                st.download_button("⬇️ All Records", df.to_csv(index=False).encode(), f"att_{sc}_all.csv", "text/csv", key="dl_all")
            else:
                st.info(f"No records for {sc}.")

    # ── TAB 4: Manual Entry ───────────────────────────────
    with tabs[3]:
        st.markdown("### ✍️ Manual Attendance Entry")
        st.info("💡 Use when a student missed the QR scan.")
        companies = load_companies()
        students = load_students()

        if not companies:
            st.warning("No companies yet. Start a QR session first.")
        else:
            sc = st.selectbox("Company:", companies, key="man_comp")
            df = load_attendance(sc)

            if not students.empty:
                st.markdown("#### Select from list")
                sr = st.selectbox("Roll Number:", [""] + students['rollnumber'].tolist(), key="man_roll")
                md = st.date_input("Date:", value=date.today(), key="man_date")
                if sr and st.button("✅ Mark", type="primary", key="man_mark"):
                    ds = md.strftime("%d-%m-%Y")
                    already = df[(df['rollnumber'].str.lower()==sr.lower())&(df['datestamp']==ds)] if not df.empty else pd.DataFrame()
                    if not already.empty: st.warning(f"Already marked {sr} on {ds} for {sc}")
                    else:
                        df = pd.concat([df, pd.DataFrame([{'rollnumber':sr,'timestamp':ist_time_str(),'datestamp':ds,'company':sc}])], ignore_index=True)
                        df.to_csv(att_csv(sc), index=False)
                        st.success(f"✅ {sr} marked for {sc} on {ds}"); log_action("manual", f"{sr}-{sc}-{ds}"); st.rerun()

            st.markdown("---")
            st.markdown("#### Enter manually")
            mr = st.text_input("Roll Number:", key="man_r2")
            md2 = st.date_input("Date:", value=date.today(), key="man_d2")
            if st.button("✅ Mark", key="man_mark2"):
                if mr:
                    ds = md2.strftime("%d-%m-%Y")
                    already = df[(df['rollnumber'].str.lower()==mr.lower())&(df['datestamp']==ds)] if not df.empty else pd.DataFrame()
                    if not already.empty: st.warning(f"Already marked {mr} on {ds} for {sc}")
                    else:
                        df = pd.concat([df, pd.DataFrame([{'rollnumber':mr.strip(),'timestamp':ist_time_str(),'datestamp':ds,'company':sc}])], ignore_index=True)
                        df.to_csv(att_csv(sc), index=False)
                        st.success(f"✅ {mr} marked for {sc} on {ds}"); log_action("manual_custom", f"{mr}-{sc}-{ds}"); st.rerun()
                else: st.warning("Enter roll number")

    # ── TAB 5: Device Bindings ────────────────────────────
    with tabs[4]:
        st.markdown("### 📱 Device Bindings")
        st.info("One device per student.")
        df = load_device_binding()
        if not df.empty:
            st.dataframe(df, width=1000)
            st.info(f"**{len(df)} devices bound**")
            tu = st.selectbox("Unbind:", [""] + df['rollnumber'].tolist(), key="unbind")
            if tu and st.button("🔓 Unbind", key="unbind_btn"):
                df = df[df['rollnumber'] != tu]
                save_device_binding(df); st.success(f"✅ Unbound '{tu}'"); st.rerun()
        else: st.info("No devices bound yet.")

    # ── TAB 6: Logs ───────────────────────────────────────
    with tabs[5]:
        st.markdown("### 📋 Activity Logs")
        if Path(LOG_CSV).exists():
            df = pd.read_csv(LOG_CSV)
            st.dataframe(df.sort_values("timestamp", ascending=False).head(100), width=1000)
        else: st.info("No logs yet.")

def main():
    st.set_page_config(page_title="QR Attendance Admin", page_icon="🎯", layout="wide")
    st.sidebar.title("🎯 QR Attendance System")
    if st.session_state.admin_logged:
        admin_logout(); admin_panel()
    else:
        admin_login()
        st.markdown('<div class="header">🎯 Smart QR Attendance System</div>', unsafe_allow_html=True)
        st.markdown("""
        ### Features:
        - ✅ Company-wise attendance tracking
        - ✅ Custom time window (1–30 mins)
        - ✅ Auto-refreshing QR every 30s
        - ✅ Optional location check (SNIST)
        - ✅ Single device per student
        - ✅ Manual attendance override
        
        **👈 Login from sidebar!**
        """)

if __name__ == "__main__":
    main()
