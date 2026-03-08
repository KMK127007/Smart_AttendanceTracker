import streamlit as st
import qrcode, base64, time
from io import BytesIO
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

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

# Session state defaults
for k, v in {
    "admin_logged": False, "admin_user": None,
    "qr_active": False, "qr_start_time": None,
    "qr_window_seconds": 60, "qr_location_enabled": False,
    "qr_token": None, "qr_image": None,
    "qr_last_refresh": None, "qr_company": None,
    "qr_refresh_seconds": 30,
}.items():
    if k not in st.session_state: st.session_state[k] = v

# ── Supabase Functions ────────────────────────────────────
def log_action(action, details="", username=None):
    """Log admin action"""
    try:
        supabase.table('admin_logs').insert({
            'action': action,
            'details': details,
            'username': username or st.session_state.admin_user
        }).execute()
    except: pass

def load_companies():
    """Get list of companies"""
    try:
        response = supabase.table('companies').select('name').order('name').execute()
        return [row['name'] for row in response.data]
    except:
        return []

def add_company(name):
    """Add company if doesn't exist"""
    try:
        supabase.table('companies').insert({'name': name}).execute()
    except:
        pass  # Already exists

def make_qr(token, company, loc_enabled, refresh_secs=30):
    import urllib.parse
    company_enc = urllib.parse.quote(company)
    url = f"https://smartapp12.streamlit.app?access={token}&company={company_enc}&loc={1 if loc_enabled else 0}&window={refresh_secs}"
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode()

# ── Login ──────────────────────────────────────────────────
def admin_login():
    st.markdown('<div style="text-align:center; padding:20px"><h2>🔐 Admin Login</h2></div>', unsafe_allow_html=True)
    u = st.text_input("Username", key="login_u")
    p = st.text_input("Password", type="password", key="login_p")
    if st.button("Login", type="primary"):
        if u in ADMINS and ADMINS[u]["password"] == p:
            st.session_state.admin_logged = True
            st.session_state.admin_user = u
            log_action("admin_login", u); st.rerun()
        else: st.error("❌ Invalid credentials")

def admin_logout():
    if st.sidebar.button("🚪 Logout"):
        st.session_state.admin_logged = False
        st.session_state.qr_active = False
        log_action("admin_logout"); st.rerun()

# ── Admin Panel ───────────────────────────────────────────
def admin_panel():
    st.markdown('<h1 style="text-align:center">🎯 QR Attendance System — Admin Panel</h1>', unsafe_allow_html=True)
    st.write(f"Logged in as **{st.session_state.admin_user}**")
    st.markdown("---")

    tabs = st.tabs(["📱 Generate QR", "📋 Logs"])

    # ── TAB 1: Generate QR ────────────────────────────────
    with tabs[0]:
        st.markdown("### 📱 QR Code Generator")

        # Stage 1
        st.markdown("#### 📂 Stage 1 — Students Database")
        st.info("Upload the students CSV/Excel in **smartapp12** (Admin → Upload CSV tab) before generating QR.")

        st.markdown("---")

        # Stage 2
        st.markdown("#### ⚙️ Stage 2 — Configure Settings")

        # Location toggle first (outside columns)
        st.markdown("**📍 Location Verification**")
        loc_enabled = st.toggle("Enable Location Check", value=False, key="loc_toggle",
                                help="Students must be within 500m of SNIST")
        if loc_enabled:
            st.success("📍 **ENABLED**")
            st.info("📌 SNIST\nLat: 17.4558417\nLon: 78.6670873\nRadius: 500m")
        else:
            st.info("📍 **DISABLED** — Students mark from anywhere")

        st.markdown("---")

        st.markdown("**⏱️ Time Window**")
        time_opts = {"1 minute":60,"3 minutes":180,"5 minutes":300,"10 minutes":600,"15 minutes":900,"30 minutes":1800}
        sel_time = st.selectbox("QR session duration:", list(time_opts.keys()), key="tw_sel")
        sel_secs = time_opts[sel_time]

        if loc_enabled:
            st.markdown("**🔄 QR Refresh Rate**")
            refresh_opts = {
                "60 seconds": 60,
                "90 seconds": 90,
                "2 minutes":  120,
                "3 minutes":  180,
                "5 minutes":  300,
            }
            sel_refresh_label = st.selectbox(
                "QR refresh interval (location ON):",
                list(refresh_opts.keys()),
                index=0,   # default: 60 seconds
                key="refresh_sel",
            )
            sel_refresh_secs = refresh_opts[sel_refresh_label]
            st.caption(f"QR refreshes every **{sel_refresh_label}**")
        else:
            sel_refresh_secs = 30
            st.caption(f"QR auto-refreshes every 30s within {sel_time}")

        st.markdown("---")

        st.markdown("**🏢 Company / Drive**")
        companies = load_companies()
        mode = st.radio("", ["Select Existing", "Create New"], horizontal=True, key="comp_mode")
        sel_company = None

        if mode == "Select Existing":
            if companies:
                sel_company = st.selectbox("Select:", companies, key="comp_sel")
            else:
                st.warning("No companies yet. Switch to 'Create New'.")

        if mode == "Create New":
            new_name = st.text_input("Company Name:", placeholder="e.g. TCS, Infosys", key="new_comp")
            if new_name.strip():
                sel_company = new_name.strip()
                if st.button("➕ Save Company", key="save_comp"):
                    add_company(new_name.strip())
                    st.success(f"✅ '{new_name.strip()}' saved!")
                    st.rerun()

        if sel_company:
            st.success(f"🏢 **{sel_company}**")

        st.markdown("---")

        # Stage 3
        st.markdown("#### 🔲 Stage 3 — Generate QR")

        can_go = sel_company is not None
        if not can_go:
            st.warning("⚠️ Select or create a company first.")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔲 Start QR Session", type="primary", key="start_qr", disabled=not can_go):
                add_company(sel_company)
                ts = int(time.time())
                token = f"qr_{ts}"
                st.session_state.update({
                    "qr_active": True, "qr_start_time": ts,
                    "qr_window_seconds": sel_secs,
                    "qr_refresh_seconds": sel_refresh_secs,
                    "qr_location_enabled": loc_enabled,
                    "qr_company": sel_company,
                    "qr_token": token,
                    "qr_image": make_qr(token, sel_company, loc_enabled, sel_refresh_secs),
                    "qr_last_refresh": ts,
                })
                log_action("start_qr", f"{sel_company} | {sel_time} | loc:{loc_enabled} | refresh:{sel_refresh_secs}s")
                st.rerun()

        with c2:
            if st.session_state.qr_active:
                if st.button("⏹️ Stop Session", key="stop_qr"):
                    st.session_state.qr_active = False
                    log_action("stop_qr"); st.rerun()

        # Live QR display
        if st.session_state.qr_active:
            now = int(time.time())
            total_elapsed = now - st.session_state.qr_start_time
            remaining = st.session_state.qr_window_seconds - total_elapsed
            refresh_secs = st.session_state.get("qr_refresh_seconds", 30)

            if remaining <= 0:
                st.error("⏰ Session expired. Start a new one.")
                st.session_state.qr_active = False; st.rerun()

            since_refresh = now - st.session_state.qr_last_refresh
            if since_refresh >= refresh_secs:
                new_token = f"qr_{now}"
                st.session_state.qr_token = new_token
                st.session_state.qr_image = make_qr(new_token, st.session_state.qr_company, st.session_state.qr_location_enabled, refresh_secs)
                st.session_state.qr_last_refresh = now
                log_action("qr_refresh", st.session_state.qr_company)

            st.markdown("---")
            st.markdown("### 📱 Active QR Code")

            m, s = int(remaining // 60), int(remaining % 60)
            next_in = max(0, refresh_secs - int(since_refresh))

            qr_col, info_col = st.columns([2, 1])
            with qr_col:
                st.markdown(f'<img src="data:image/png;base64,{st.session_state.qr_image}" width="280"/>', unsafe_allow_html=True)
            with info_col:
                st.metric("⏱️ Session Remaining", f"{m}m {s}s")
                st.metric("🔄 Next QR in", f"{next_in}s")
                st.info(f"🏢 **{st.session_state.qr_company}**")
                if st.session_state.qr_location_enabled:
                    st.success("📍 Location: ON")
                    st.caption(f"Refresh every {refresh_secs}s ✅")
                else:
                    st.info("📍 Location: OFF")
                    st.caption("Refresh every 30s ✅")

            time.sleep(1)
            st.rerun()

    # ── TAB 2: Logs ───────────────────────────────────────
    with tabs[1]:
        st.markdown("### 📋 Admin Activity Logs")
        try:
            response = supabase.table('admin_logs').select('*').order('timestamp', desc=True).limit(50).execute()
            if response.data:
                import pandas as pd
                df = pd.DataFrame(response.data)
                st.dataframe(df[['timestamp', 'action', 'details', 'username']], use_container_width=True)
            else:
                st.info("No logs yet.")
        except Exception as e:
            st.error(f"Error loading logs: {e}")

# ── Main ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="QR Admin Panel", page_icon="🎯", layout="centered")
    
    if not st.session_state.admin_logged:
        admin_login()
    else:
        admin_logout()
        admin_panel()

if __name__ == "__main__":
    main()
