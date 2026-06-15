import streamlit as st
import pandas as pd
import os
from datetime import date, datetime, timedelta
import calendar
import io

# ── Archivos ───────────────────────────────────────────────────────────────────
FILES = {
    "gastos":      ("mis_gastos.csv",      ["Fecha","Concepto","Monto","Tarjeta","Cuotas","Categoria","Compartido","Con quien","Cuanto recupero","Notas"]),
    "ingresos":    ("mis_ingresos.csv",    ["Fecha","Concepto","Monto","Categoria"]),
    "compartidos": ("mis_compartidos.csv", ["Fecha","Concepto","Monto","Con quien","Estado","Notas"]),
    "inversiones": ("mis_inversiones.csv", ["Fecha","Instrumento","Capital","Rendimiento","Moneda","Notas"]),
    "presupuesto": ("mis_presupuesto.csv", ["Categoria","Limite"]),
    "tarjetas":    ("mis_tarjetas.csv",    ["Nombre","Dia cierre","Dia vencimiento","Color"]),
}

CAT_GASTOS = ["🍔 Comida","🚗 Transporte","🎉 Salidas","✈️ Viaje","🏥 Salud",
               "👕 Ropa","📱 Servicios","🏠 Casa","💊 Farmacia","📚 Educación","🎁 Regalos","💳 Otro"]
CAT_ING    = ["💼 Sueldo","💻 Freelance","📈 Inversión","🎁 Regalo","💰 Otro"]
MONEDAS    = ["ARS","USD","EUR"]
COLORES_TARJETA = ["#7c6af7","#4ade80","#f87171","#fbbf24","#60a5fa","#f472b6","#34d399","#fb923c"]
TARJETAS_DEFAULT = ["Visa ICBC","Visa Hipotecario","Master ICBC","Efectivo","Débito","Otro"]

# ── Helpers ────────────────────────────────────────────────────────────────────
def load(key):
    f, cols = FILES[key]
    if os.path.exists(f):
        df = pd.read_csv(f)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols]
    return pd.DataFrame(columns=cols)

def save(key, df):
    f, _ = FILES[key]
    df.to_csv(f, index=False)

def to_num(s):
    return pd.to_numeric(
        pd.Series(s).astype(str).str.replace(r"[^\d\.\-]","",regex=True),
        errors="coerce"
    ).fillna(0)

def mes_actual():
    hoy = date.today()
    return hoy.year, hoy.month

def filtrar_mes(df, y, m):
    if df.empty or "Fecha" not in df.columns:
        return df
    fechas = pd.to_datetime(df["Fecha"], errors="coerce")
    return df[(fechas.dt.year == y) & (fechas.dt.month == m)]

def fmt_ars(n):
    return f"${n:,.0f}".replace(",",".")

def emoji_cat(cat):
    return cat.split(" ")[0] if " " in cat else "💳"

def safe_int(val, default=1):
    try:
        if pd.isna(val) or str(val).strip() in ("","nan","None","N/A"):
            return default
        return int(float(str(val)))
    except (ValueError, TypeError):
        return default

def get_tarjetas_nombres():
    t_df = load("tarjetas")
    if not t_df.empty:
        nombres = t_df["Nombre"].dropna().tolist()
        for d in TARJETAS_DEFAULT:
            if d not in nombres:
                nombres.append(d)
        return nombres
    return TARJETAS_DEFAULT

def get_periodo_tarjeta(tarjeta_nombre, año=None, mes=None):
    t_df = load("tarjetas")
    hoy = date.today()
    if año is None: año = hoy.year
    if mes is None: mes = hoy.month
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return date(año, mes, 1), date(año, mes, calendar.monthrange(año, mes)[1])
    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    dia_cierre = min(safe_int(row.get("Dia cierre", 1), 1), 28)
    mes_ant, año_ant = (12, año-1) if mes == 1 else (mes-1, año)
    ultimo_mes_ant = calendar.monthrange(año_ant, mes_ant)[1]
    inicio = date(año_ant, mes_ant, min(dia_cierre+1, ultimo_mes_ant))
    fin = date(año, mes, min(dia_cierre, calendar.monthrange(año, mes)[1]))
    return inicio, fin

def periodo_actual_de_gasto(fecha_str, tarjeta_nombre):
    try:
        fg = pd.to_datetime(fecha_str).date()
    except:
        hoy = date.today(); return hoy.year, hoy.month
    t_df = load("tarjetas")
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return fg.year, fg.month
    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    dia_cierre = min(safe_int(row.get("Dia cierre", 1), 1), 28)
    if fg.day <= dia_cierre:
        return fg.year, fg.month
    return (fg.year+1, 1) if fg.month == 12 else (fg.year, fg.month+1)

def filtrar_gastos_tarjeta_periodo(gastos_df, tarjeta_nombre, año_periodo, mes_periodo):
    if gastos_df.empty: return gastos_df
    mask = []
    for _, r in gastos_df.iterrows():
        if str(r.get("Tarjeta","")) == tarjeta_nombre:
            ay, am = periodo_actual_de_gasto(str(r.get("Fecha","")), tarjeta_nombre)
            mask.append(ay == año_periodo and am == mes_periodo)
        else:
            mask.append(False)
    return gastos_df[mask]

def get_color_tarjeta(tname, tarjetas_df):
    if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
        c = str(tarjetas_df[tarjetas_df["Nombre"]==tname].iloc[0].get("Color","#7c6af7"))
        return c if c.startswith("#") else "#7c6af7"
    idx = TARJETAS_DEFAULT.index(tname) if tname in TARJETAS_DEFAULT else 0
    return COLORES_TARJETA[idx % len(COLORES_TARJETA)]

# ══════════════════════════════════════════════════════════════════════════════
# SETUP PÁGINA
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Biyuyo", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

*, *::before, *::after { box-sizing: border-box; }

html, body, [class*="css"], .stApp {
    font-family: 'DM Sans', sans-serif !important;
    background-color: #080810 !important;
    color: #dde0f0 !important;
    -webkit-font-smoothing: antialiased;
}

#MainMenu, header, footer { visibility: hidden; }
.block-container {
    padding: 0 0 6rem !important;
    max-width: 430px !important;
    margin: 0 auto !important;
}

/* ── HEADER STRIP ── */
.app-header {
    background: #080810;
    padding: 1rem 1rem 0.5rem;
    position: sticky;
    top: 0;
    z-index: 100;
    border-bottom: 1px solid #14141e;
}
.app-brand {
    font-family: 'DM Mono', monospace;
    font-size: 1.1rem;
    font-weight: 500;
    color: #6c63ff;
    letter-spacing: -0.02em;
}
.app-brand span { color: #dde0f0; }

/* ── TABS ── */
[data-baseweb="tab-list"] {
    background: #0d0d18 !important;
    border-radius: 0 !important;
    padding: 0 0.8rem !important;
    gap: 0 !important;
    border-bottom: 1px solid #14141e !important;
    border-top: none !important;
    border-left: none !important;
    border-right: none !important;
    overflow-x: auto !important;
}
[data-baseweb="tab"] {
    border-radius: 0 !important;
    font-size: 0.75rem !important;
    font-weight: 500 !important;
    padding: 0.8rem 0.9rem !important;
    color: #555 !important;
    background: transparent !important;
    border-bottom: 2px solid transparent !important;
    white-space: nowrap !important;
}
[aria-selected="true"][data-baseweb="tab"] {
    background: transparent !important;
    color: #dde0f0 !important;
    border-bottom: 2px solid #6c63ff !important;
}
[data-testid="stTabContent"] { padding: 0 !important; }

/* ── SECTION WRAPPER ── */
.s { padding: 0 1rem; }

/* ── HERO NÚMERO ── */
.hero-block {
    padding: 1.5rem 1rem 1rem;
    border-bottom: 1px solid #14141e;
    margin-bottom: 0;
}
.hero-eyebrow {
    font-size: 0.65rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #444;
    margin-bottom: 0.25rem;
}
.hero-num {
    font-family: 'DM Mono', monospace;
    font-size: 3rem;
    font-weight: 500;
    line-height: 1;
    letter-spacing: -0.03em;
}
.hero-sub {
    font-size: 0.75rem;
    color: #444;
    margin-top: 0.4rem;
}
.c-pos { color: #39e07a; }
.c-neg { color: #ff5f7e; }
.c-neu { color: #6c63ff; }
.c-yel { color: #f5c542; }
.c-dim { color: #555; }

/* ── STAT ROW ── */
.stat-row {
    display: flex;
    border-bottom: 1px solid #14141e;
}
.stat-cell {
    flex: 1;
    padding: 0.9rem 1rem;
    border-right: 1px solid #14141e;
}
.stat-cell:last-child { border-right: none; }
.stat-label {
    font-size: 0.63rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #444;
    margin-bottom: 0.2rem;
}
.stat-val {
    font-family: 'DM Mono', monospace;
    font-size: 1.1rem;
    font-weight: 500;
    letter-spacing: -0.02em;
}

/* ── TARJETA CHIP ── */
.tarjeta-row {
    display: flex;
    align-items: center;
    padding: 0.9rem 1rem;
    border-bottom: 1px solid #14141e;
    gap: 0.75rem;
}
.tarjeta-pip {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}
.tarjeta-label {
    font-size: 0.82rem;
    font-weight: 500;
    color: #bbb;
    flex: 1;
}
.tarjeta-meta-small {
    font-size: 0.68rem;
    color: #444;
}
.tarjeta-amount {
    font-family: 'DM Mono', monospace;
    font-size: 0.92rem;
    font-weight: 500;
    letter-spacing: -0.02em;
}
.tarjeta-bar-bg {
    width: 60px;
    height: 3px;
    background: #1a1a28;
    border-radius: 99px;
    overflow: hidden;
    flex-shrink: 0;
}
.tarjeta-bar-fill {
    height: 100%;
    border-radius: 99px;
}

/* ── TX ROW ── */
.tx {
    display: flex;
    align-items: center;
    padding: 0.85rem 1rem;
    border-bottom: 1px solid #14141e;
    gap: 0.75rem;
}
.tx-ico {
    width: 34px; height: 34px;
    background: #12121e;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1rem;
    flex-shrink: 0;
}
.tx-main { flex: 1; min-width: 0; }
.tx-name {
    font-size: 0.84rem;
    font-weight: 500;
    color: #dde0f0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.tx-info { font-size: 0.68rem; color: #444; margin-top: 1px; }
.tx-amt {
    font-family: 'DM Mono', monospace;
    font-size: 0.88rem;
    font-weight: 500;
    text-align: right;
    flex-shrink: 0;
    letter-spacing: -0.02em;
}

/* ── SECTION LABEL ── */
.sec {
    padding: 1.1rem 1rem 0.4rem;
    font-size: 0.63rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #333;
    border-bottom: none;
}

/* ── PROG BAR ── */
.prog-wrap { padding: 0.7rem 1rem; border-bottom: 1px solid #14141e; }
.prog-head { display: flex; justify-content: space-between; font-size: 0.78rem; margin-bottom: 0.4rem; color: #bbb; }
.prog-head span:last-child { font-family: 'DM Mono', monospace; font-size: 0.75rem; color: #666; }
.prog-bg { background: #12121e; border-radius: 99px; height: 4px; overflow: hidden; }
.prog-fill { height: 100%; border-radius: 99px; }
.prog-note { font-size: 0.65rem; color: #444; margin-top: 0.3rem; }

/* ── PERÍODO BADGE ── */
.per-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: #0d0d18;
    border: 1px solid #1e1e30;
    border-radius: 6px;
    padding: 3px 9px;
    font-size: 0.68rem;
    color: #555;
    margin: 0.5rem 1rem;
}
.per-badge.open { border-color: #39e07a33; color: #39e07a; }
.per-badge.closed { border-color: #ff5f7e33; color: #ff5f7e; }
.per-badge.future { border-color: #6c63ff33; color: #6c63ff; }

/* ── EMPTY STATE ── */
.empty {
    text-align: center;
    padding: 2.5rem 1rem;
    color: #333;
    font-size: 0.82rem;
    border-bottom: 1px solid #14141e;
}
.empty big { display: block; font-size: 1.8rem; margin-bottom: 0.5rem; opacity: 0.4; }

/* ── CHIP PRÓXIMO PERÍODO ── */
.chip-next {
    display: inline-block;
    background: #6c63ff15;
    border: 1px solid #6c63ff30;
    color: #6c63ff;
    border-radius: 4px;
    font-size: 0.6rem;
    padding: 1px 5px;
    margin-left: 5px;
    vertical-align: middle;
    font-family: 'DM Mono', monospace;
}

/* ── BOTONES STREAMLIT ── */
div[data-testid="stButton"] > button,
div[data-testid="stFormSubmitButton"] > button {
    background: #6c63ff !important;
    color: #fff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    padding: 0.5rem 1rem !important;
    width: 100% !important;
    font-family: 'DM Sans', sans-serif !important;
    letter-spacing: 0 !important;
    transition: background 0.15s !important;
}
div[data-testid="stButton"] > button:hover,
div[data-testid="stFormSubmitButton"] > button:hover {
    background: #5a52e0 !important;
    color: #fff !important;
}
.btn-ghost > div[data-testid="stButton"] > button {
    background: transparent !important;
    border: 1px solid #1e1e30 !important;
    color: #555 !important;
}
.btn-danger > div[data-testid="stButton"] > button {
    background: #ff5f7e18 !important;
    border: 1px solid #ff5f7e30 !important;
    color: #ff5f7e !important;
}

/* ── INPUTS ── */
.stTextInput input, .stNumberInput input, .stDateInput input,
.stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea {
    background: #0d0d18 !important;
    border: 1px solid #1e1e30 !important;
    border-radius: 8px !important;
    color: #dde0f0 !important;
    font-size: 0.88rem !important;
    font-family: 'DM Sans', sans-serif !important;
}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
    border-color: #6c63ff !important;
    box-shadow: 0 0 0 3px #6c63ff18 !important;
}
label[data-testid="stWidgetLabel"] p {
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    color: #444 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
}

/* ── EXPANDER ── */
[data-testid="stExpander"] {
    background: #0a0a14 !important;
    border: 1px solid #14141e !important;
    border-radius: 10px !important;
    margin: 0 1rem 0.5rem !important;
}
[data-testid="stExpander"] summary {
    font-size: 0.8rem !important;
    font-weight: 600 !important;
    color: #666 !important;
    padding: 0.7rem 0.9rem !important;
}

/* ── FORM CONTAINER ── */
[data-testid="stForm"] {
    background: #0a0a14 !important;
    border: 1px solid #14141e !important;
    border-radius: 12px !important;
    padding: 1rem !important;
    margin: 0.5rem 1rem !important;
}

/* ── RADIO ── */
.stRadio > div { gap: 0.5rem !important; }
.stRadio label { font-size: 0.82rem !important; color: #888 !important; }

/* ── TOTAL STRIP ── */
.total-strip {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.8rem 1rem;
    background: #0a0a14;
    border-top: 1px solid #14141e;
    border-bottom: 1px solid #14141e;
    margin-top: 0.2rem;
}
.total-strip-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.1em; color: #444; }
.total-strip-val { font-family: 'DM Mono', monospace; font-size: 1rem; font-weight: 500; }

/* ── DATA EDITOR override ── */
[data-testid="stDataEditor"] {
    border: 1px solid #1e1e30 !important;
    border-radius: 8px !important;
    overflow: hidden !important;
    margin: 0 1rem !important;
}

/* ── ACCIONES FLOTANTE ── */
.acciones-panel {
    background: #0d0d18;
    border-bottom: 2px solid #6c63ff;
    padding: 0.75rem 1rem 0;
    margin-bottom: 0;
}

/* ── INFO BANNER ── */
.info-strip {
    background: #6c63ff10;
    border-left: 2px solid #6c63ff;
    padding: 0.6rem 1rem;
    font-size: 0.75rem;
    color: #888;
    margin: 0.5rem 1rem;
    border-radius: 0 6px 6px 0;
}

/* ── PENDIENTE COBRAR ── */
.pend-row {
    display: flex;
    align-items: center;
    padding: 0.85rem 1rem;
    border-bottom: 1px solid #14141e;
    gap: 0.75rem;
    background: #f5c54208;
}
</style>
""", unsafe_allow_html=True)

# ── Datos ──────────────────────────────────────────────────────────────────────
if "gasto_limit" not in st.session_state: st.session_state.gasto_limit = 30
if "menu_accion" not in st.session_state: st.session_state.menu_accion = False
if "tipo_accion" not in st.session_state: st.session_state.tipo_accion = "Gasto"

gastos_df   = load("gastos")
ingresos_df = load("ingresos")
comp_df     = load("compartidos")
inv_df      = load("inversiones")
pres_df     = load("presupuesto")
tarjetas_df = load("tarjetas")

for col in ["Monto","Cuanto recupero"]:
    gastos_df[col] = to_num(gastos_df[col])
ingresos_df["Monto"] = to_num(ingresos_df["Monto"])
comp_df["Monto"]     = to_num(comp_df["Monto"])

y, m = mes_actual()
nombre_mes = calendar.month_name[m].capitalize()
gastos_mes   = filtrar_mes(gastos_df, y, m)
ingresos_mes = filtrar_mes(ingresos_df, y, m)

total_ing   = ingresos_mes["Monto"].sum()
total_gast  = gastos_mes["Monto"].sum()
recupero    = gastos_mes["Cuanto recupero"].sum()
remanente   = total_ing - total_gast + recupero

TARJETAS = get_tarjetas_nombres()

# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class='app-header'>
    <div style='display:flex;justify-content:space-between;align-items:center'>
        <div class='app-brand'>biyuyo<span>.</span></div>
        <div style='font-size:0.65rem;color:#333;font-family:"DM Mono",monospace'>ARS · 2026</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── ACCIONES RÁPIDAS ──────────────────────────────────────────────────────────
col_l, col_r = st.columns([3,1])
with col_r:
    label_btn = "✕ Cerrar" if st.session_state.menu_accion else "+ Agregar"
    if st.button(label_btn, key="fab"):
        st.session_state.menu_accion = not st.session_state.menu_accion
        st.rerun()

if st.session_state.menu_accion:
    st.markdown("<div class='acciones-panel'>", unsafe_allow_html=True)
    tipo = st.radio("", ["Gasto","Ingreso","Tarjeta"], horizontal=True,
                    key="tipo_acc", label_visibility="collapsed")

    if tipo == "Gasto":
        with st.form("fq_g", clear_on_submit=True):
            c1,c2 = st.columns(2)
            q_c = c1.text_input("Concepto")
            q_m = c2.number_input("Monto $", min_value=0.0, step=100.0)
            c3,c4 = st.columns(2)
            q_t = c3.selectbox("Tarjeta", TARJETAS)
            q_k = c4.selectbox("Categoría", CAT_GASTOS)
            c5,c6 = st.columns(2)
            q_f = c5.date_input("Fecha", value=date.today())
            q_cu = c6.number_input("Cuotas", min_value=1, max_value=48, value=1)
            # preview período
            ay_p, am_p = periodo_actual_de_gasto(str(q_f), q_t)
            if ay_p != y or am_p != m:
                mn = calendar.month_name[am_p][:3]
                st.markdown(f"<div class='info-strip'>📅 Va al resumen de <strong>{mn} {ay_p}</strong></div>", unsafe_allow_html=True)
            ca, cb = st.columns([3,1])
            if ca.form_submit_button("Guardar gasto"):
                if q_c.strip() and q_m > 0:
                    nv = pd.DataFrame([[str(q_f),q_c.strip(),q_m,q_t,q_cu,q_k,"No","",0,""]], columns=gastos_df.columns)
                    gastos_df = pd.concat([gastos_df,nv], ignore_index=True)
                    save("gastos", gastos_df)
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")
            if cb.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()

    elif tipo == "Ingreso":
        with st.form("fq_i", clear_on_submit=True):
            c1,c2 = st.columns(2)
            i_c = c1.text_input("Concepto")
            i_m = c2.number_input("Monto $", min_value=0.0, step=1000.0)
            c3,c4 = st.columns(2)
            i_k = c3.selectbox("Categoría", CAT_ING)
            i_f = c4.date_input("Fecha", value=date.today())
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Guardar ingreso"):
                if i_c.strip() and i_m > 0:
                    nv = pd.DataFrame([[str(i_f),i_c.strip(),i_m,i_k]], columns=ingresos_df.columns)
                    ingresos_df = pd.concat([ingresos_df,nv], ignore_index=True)
                    save("ingresos", ingresos_df)
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")
            if cb.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()

    elif tipo == "Tarjeta":
        with st.form("fq_t", clear_on_submit=True):
            t_n = st.text_input("Nombre de la tarjeta")
            c1,c2 = st.columns(2)
            t_dc = c1.number_input("Día cierre", min_value=1, max_value=28, value=5)
            t_dv = c2.number_input("Día vencimiento", min_value=1, max_value=31, value=20)
            t_col = st.selectbox("Color", COLORES_TARJETA)
            st.markdown(f"<div style='width:18px;height:18px;border-radius:50%;background:{t_col}'></div>", unsafe_allow_html=True)
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Crear tarjeta"):
                if t_n.strip():
                    base = tarjetas_df[tarjetas_df["Nombre"] != t_n.strip()].copy()
                    fila = pd.DataFrame([[t_n.strip(),t_dc,t_dv,t_col]], columns=["Nombre","Dia cierre","Dia vencimiento","Color"])
                    tarjetas_df = pd.concat([base,fila], ignore_index=True)
                    save("tarjetas", tarjetas_df)
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Ingresá un nombre.")
            if cb.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

# ── TABS ──────────────────────────────────────────────────────────────────────
tabs = st.tabs(["Inicio","Gastos","Tarjetas","Ingresos","Compartidos","Inversiones","Presupuesto"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 0 — INICIO
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    # Hero: remanente
    color_hero = "c-pos" if remanente >= 0 else "c-neg"
    st.markdown(f"""
    <div class='hero-block'>
        <div class='hero-eyebrow'>{nombre_mes} {y} · remanente</div>
        <div class='hero-num {color_hero}'>{fmt_ars(remanente)}</div>
        <div class='hero-sub'>ingresos − gastos + recupero</div>
    </div>""", unsafe_allow_html=True)

    # Stat row
    st.markdown(f"""
    <div class='stat-row'>
        <div class='stat-cell'>
            <div class='stat-label'>Entró</div>
            <div class='stat-val c-pos'>{fmt_ars(total_ing)}</div>
        </div>
        <div class='stat-cell'>
            <div class='stat-label'>Salió</div>
            <div class='stat-val c-neg'>{fmt_ars(total_gast)}</div>
        </div>
        {"<div class='stat-cell'><div class='stat-label'>Recuperás</div><div class='stat-val c-yel'>" + fmt_ars(recupero) + "</div></div>" if recupero > 0 else ""}
    </div>""", unsafe_allow_html=True)

    # Tarjetas del período actual
    st.markdown("<div class='sec'>Esta quincena / período</div>", unsafe_allow_html=True)
    tarjetas_con_gasto = {}
    for tname in TARJETAS:
        if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
            gf = filtrar_gastos_tarjeta_periodo(gastos_df, tname, y, m)
        else:
            gf = gastos_mes[gastos_mes["Tarjeta"] == tname] if not gastos_mes.empty else pd.DataFrame(columns=gastos_df.columns)
        gf_monto = gf["Monto"].sum() if not gf.empty else 0
        if gf_monto > 0:
            tarjetas_con_gasto[tname] = gf_monto

    if tarjetas_con_gasto:
        max_t = max(tarjetas_con_gasto.values())
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = get_color_tarjeta(tname, tarjetas_df)
            pct = int(total_t / max_t * 100)
            meta_html = ""
            if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
                row_i = tarjetas_df[tarjetas_df["Nombre"] == tname].iloc[0]
                dc = safe_int(row_i.get("Dia cierre",""), 0)
                meta_html = f"<div class='tarjeta-meta-small'>cierra día {dc}</div>" if dc else ""
            bar_fill = f"<div class='tarjeta-bar-fill' style='width:{pct}%;background:{color}'></div>"
            st.markdown(
                "<div class='tarjeta-row'>"
                f"<div class='tarjeta-pip' style='background:{color}'></div>"
                f"<div style='flex:1'><div class='tarjeta-label'>{tname}</div>{meta_html}</div>"
                f"<div class='tarjeta-bar-bg'>{bar_fill}</div>"
                f"<div class='tarjeta-amount c-neg'>{fmt_ars(total_t)}</div>"
                "</div>",
                unsafe_allow_html=True
            )
    else:
        st.markdown("<div class='empty'><big>💸</big>Todavía no hay gastos este período.</div>", unsafe_allow_html=True)

    # Categorías
    if not gastos_mes.empty:
        st.markdown("<div class='sec'>Por categoría</div>", unsafe_allow_html=True)
        cat_sum = gastos_mes.groupby("Categoria")["Monto"].sum().sort_values(ascending=False).head(6)
        max_c = cat_sum.max() if cat_sum.max() > 0 else 1
        pal = ["#6c63ff","#39e07a","#ff5f7e","#f5c542","#60a5fa","#f472b6"]
        for i, (cat, val) in enumerate(cat_sum.items()):
            pct = int(val / max_c * 100)
            col = pal[i % len(pal)]
            fill = f"<div class='prog-fill' style='width:{pct}%;background:{col}'></div>"
            st.markdown(
                "<div class='prog-wrap'>"
                f"<div class='prog-head'><span>{cat}</span><span>{fmt_ars(val)}</span></div>"
                f"<div class='prog-bg'>{fill}</div>"
                "</div>",
                unsafe_allow_html=True
            )

    # Últimos movimientos
    st.markdown("<div class='sec'>Últimos movimientos</div>", unsafe_allow_html=True)
    recientes = gastos_df.sort_values("Fecha", ascending=False).head(6)
    if recientes.empty:
        st.markdown("<div class='empty'><big>📋</big>Sin movimientos todavía.</div>", unsafe_allow_html=True)
    else:
        for _, r in recientes.iterrows():
            ico = emoji_cat(str(r.get("Categoria","💳")))
            fecha_str = str(r.get("Fecha",""))[:10]
            tname_r = str(r.get("Tarjeta",""))
            ay, am = periodo_actual_de_gasto(str(r.get("Fecha","")), tname_r)
            chip = ""
            if ay != y or am != m:
                chip = f"<span class='chip-next'>→{calendar.month_name[am][:3]}</span>"
            cuotas_v = safe_int(r.get("Cuotas",1),1)
            cuotas_t = f" · {cuotas_v}c" if cuotas_v > 1 else ""
            st.markdown(
                "<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}{chip}</div>"
                f"<div class='tx-info'>{fecha_str} · {tname_r}{cuotas_t}</div>"
                "</div>"
                f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                "</div>",
                unsafe_allow_html=True
            )

    # Pendientes
    pend = comp_df[comp_df["Estado"] == "Pendiente"] if not comp_df.empty else pd.DataFrame()
    if not pend.empty:
        st.markdown("<div class='sec'>Te deben</div>", unsafe_allow_html=True)
        for _, r in pend.iterrows():
            st.markdown(
                "<div class='pend-row'>"
                "<div class='tx-ico'>🤝</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                f"<div class='tx-info'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>"
                "</div>"
                f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Monto',0))}</div>"
                "</div>",
                unsafe_allow_html=True
            )
        st.markdown(
            f"<div class='total-strip'>"
            "<span class='total-strip-label'>Total pendiente</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(pend['Monto'].sum())}</span>"
            "</div>",
            unsafe_allow_html=True
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — GASTOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    # Carga masiva
    with st.expander("📥 Importar desde CSV"):
        st.markdown("<div class='info-strip'>Pegá el texto CSV del chat. Las columnas que falten se completan automáticamente.</div>", unsafe_allow_html=True)
        csv_text = st.text_area("", placeholder="Fecha,Concepto,Monto,Tarjeta...", height=120, label_visibility="collapsed")
        if st.button("Importar gastos", key="import_csv"):
            if csv_text.strip():
                try:
                    nuevos = pd.read_csv(io.StringIO(csv_text.strip()))
                    for col in gastos_df.columns:
                        if col not in nuevos.columns:
                            nuevos[col] = 0 if col in ["Monto","Cuanto recupero"] else ("No" if col=="Compartido" else "")
                    nuevos = nuevos[gastos_df.columns]
                    gastos_df = pd.concat([gastos_df, nuevos], ignore_index=True)
                    save("gastos", gastos_df)
                    st.success(f"{len(nuevos)} movimientos importados.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
            else:
                st.warning("Pegá el CSV primero.")

    # Formulario manual
    with st.expander("✏️ Carga manual"):
        with st.form("f_gasto_full", clear_on_submit=True):
            g_c = st.text_input("Concepto", placeholder="Ej: almuerzo, nafta, cuota…")
            c1,c2 = st.columns(2)
            g_m = c1.number_input("Monto $", min_value=0.0, step=500.0)
            g_cu = c2.number_input("Cuotas", min_value=1, max_value=48, value=1)
            c3,c4 = st.columns(2)
            g_t = c3.selectbox("Tarjeta", TARJETAS)
            g_k = c4.selectbox("Categoría", CAT_GASTOS)
            c5,c6 = st.columns(2)
            g_f = c5.date_input("Fecha", value=date.today())
            g_comp = c6.selectbox("Compartido", ["No","Sí"])
            c7,c8 = st.columns(2)
            g_quien = c7.text_input("Con quién", placeholder="Nombre")
            g_rec = c8.number_input("Recuperás $", min_value=0.0, step=100.0) if g_comp == "Sí" else 0.0
            g_nota = st.text_input("Nota", placeholder="Opcional")
            if st.form_submit_button("Guardar gasto"):
                if g_c.strip() and g_m > 0:
                    nv = pd.DataFrame([[str(g_f),g_c.strip(),g_m,g_t,g_cu,g_k,g_comp,g_quien,g_rec,g_nota]], columns=gastos_df.columns)
                    gastos_df = pd.concat([gastos_df,nv], ignore_index=True)
                    save("gastos", gastos_df)
                    if g_comp == "Sí" and g_rec > 0:
                        nvc = pd.DataFrame([[str(g_f),g_c.strip(),g_rec,g_quien,"Pendiente",""]], columns=comp_df.columns)
                        comp_df = pd.concat([comp_df,nvc], ignore_index=True)
                        comp_df["Monto"] = to_num(comp_df["Monto"])
                        save("compartidos", comp_df)
                    st.success(f"Guardado: {g_c} — {fmt_ars(g_m)}")
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")

    # Eliminar
    with st.expander("🗑️ Eliminar gastos"):
        del_q = st.text_input("Buscar concepto", key="del_g", placeholder="Escribí parte del concepto…")
        if del_q:
            cands = gastos_df[gastos_df["Concepto"].str.contains(del_q, case=False, na=False)]
            if cands.empty:
                st.caption("Sin resultados.")
            else:
                for idx, r in cands.iterrows():
                    ca, cb = st.columns([5,1])
                    ca.markdown(f"**{r['Concepto']}** · {r['Fecha']} · {fmt_ars(r['Monto'])}")
                    with cb:
                        if st.button("✕", key=f"dg_{idx}"):
                            gastos_df = gastos_df.drop(index=idx).reset_index(drop=True)
                            save("gastos", gastos_df)
                            st.rerun()

    # Listado
    st.markdown("<div class='sec'>Todos los movimientos</div>", unsafe_allow_html=True)
    df_show = gastos_df.sort_values("Fecha", ascending=False)
    if df_show.empty:
        st.markdown("<div class='empty'><big>📋</big>Nada cargado todavía.</div>", unsafe_allow_html=True)
    else:
        for _, r in df_show.head(st.session_state.gasto_limit).iterrows():
            ico = emoji_cat(str(r.get("Categoria","💳")))
            fstr = str(r.get("Fecha",""))[:10]
            cuotas_v = safe_int(r.get("Cuotas",1),1)
            cuotas_t = f" · {cuotas_v}c" if cuotas_v > 1 else ""
            comp_t = f" · {r.get('Con quien','')}" if r.get("Compartido","No") == "Sí" else ""
            st.markdown(
                "<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                f"<div class='tx-info'>{fstr} · {r.get('Tarjeta','')}{cuotas_t}{comp_t}</div>"
                "</div>"
                f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                "</div>",
                unsafe_allow_html=True
            )
        if len(df_show) > st.session_state.gasto_limit:
            if st.button("Ver más ▼", key="mas_g"):
                st.session_state.gasto_limit += 25
                st.rerun()
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total general</span>"
            f"<span class='total-strip-val c-neg'>{fmt_ars(df_show['Monto'].sum())}</span>"
            "</div>",
            unsafe_allow_html=True
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TARJETAS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    tarjetas_df = load("tarjetas")

    # Editor ABM
    st.markdown("<div class='sec'>Configuración de tarjetas</div>", unsafe_allow_html=True)
    if not tarjetas_df.empty:
        edited_t = st.data_editor(
            tarjetas_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Nombre":           st.column_config.TextColumn("Nombre", width="medium"),
                "Dia cierre":       st.column_config.NumberColumn("Cierra", min_value=1, max_value=28),
                "Dia vencimiento":  st.column_config.NumberColumn("Vence", min_value=1, max_value=31),
                "Color":            st.column_config.SelectboxColumn("Color", options=COLORES_TARJETA),
            },
            key="editor_tarjetas"
        )
        if st.button("Guardar cambios en tarjetas", key="save_t"):
            save("tarjetas", edited_t)
            st.success("Configuración guardada.")
            st.rerun()
    else:
        st.markdown("<div class='empty'><big>💳</big>No hay tarjetas. Usá + Agregar para crear la primera.</div>", unsafe_allow_html=True)

    st.markdown("<div class='sec'>Gastos por tarjeta y período</div>", unsafe_allow_html=True)

    # Selector tarjeta + período
    c1, c2 = st.columns(2)
    t_sel = c1.selectbox("Tarjeta", TARJETAS, key="t_sel_tab")
    periodos = []
    for delta in range(-5, 2):
        pm, py = m+delta, y
        while pm <= 0: pm += 12; py -= 1
        while pm > 12: pm -= 12; py += 1
        periodos.append((py, pm))
    opciones_per = [f"{calendar.month_name[pm][:3]} {py}" for py,pm in periodos]
    per_sel = c2.selectbox("Período", opciones_per, index=5, key="per_sel_tab")
    sel_py, sel_pm = periodos[opciones_per.index(per_sel)]

    inicio_p, fin_p = get_periodo_tarjeta(t_sel, sel_py, sel_pm)
    df_per = filtrar_gastos_tarjeta_periodo(gastos_df, t_sel, sel_py, sel_pm)
    total_per = df_per["Monto"].sum() if not df_per.empty else 0

    # Badge período
    hoy_d = date.today()
    if fin_p < hoy_d:
        badge_class, badge_ico = "closed", "🔒"
    elif inicio_p > hoy_d:
        badge_class, badge_ico = "future", "🔮"
    else:
        dias_r = (fin_p - hoy_d).days
        badge_class = "open"
        badge_ico = f"🟢 cierra en {dias_r}d"
    st.markdown(
        f"<div class='per-badge {badge_class}'>{badge_ico} · {inicio_p.strftime('%d/%m')} → {fin_p.strftime('%d/%m')}</div>",
        unsafe_allow_html=True
    )

    # Total período
    color_t_sel = get_color_tarjeta(t_sel, tarjetas_df)
    st.markdown(
        "<div class='total-strip'>"
        f"<span class='total-strip-label'>{t_sel} · {per_sel}</span>"
        f"<span class='total-strip-val' style='color:{color_t_sel}'>−{fmt_ars(total_per)}</span>"
        "</div>",
        unsafe_allow_html=True
    )

    if not df_per.empty:
        # Editor de gastos del período — FIX: columnas Compartido y Con quien editables
        df_per_ed = df_per.copy()
        df_per_ed["_idx"] = df_per_ed.index
        edited_per = st.data_editor(
            df_per_ed.drop(columns=["_idx"]),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Fecha":          st.column_config.DateColumn("Fecha"),
                "Monto":          st.column_config.NumberColumn("Monto $", format="$%d"),
                "Cuotas":         st.column_config.NumberColumn("Cuotas", min_value=1, max_value=48),
                "Compartido":     st.column_config.SelectboxColumn("Compartido", options=["No","Sí"]),
                "Con quien":      st.column_config.TextColumn("Con quién"),
                "Cuanto recupero":st.column_config.NumberColumn("Recupero $", format="$%d"),
                "Notas":          st.column_config.TextColumn("Notas"),
                "Tarjeta":        st.column_config.SelectboxColumn("Tarjeta", options=TARJETAS),
                "Categoria":      st.column_config.SelectboxColumn("Categoría", options=CAT_GASTOS),
            },
            key=f"editor_gastos_{t_sel}_{sel_py}_{sel_pm}"
        )
        if st.button("Guardar cambios en gastos", key="save_per"):
            for orig_idx in df_per_ed["_idx"]:
                if orig_idx in gastos_df.index:
                    gastos_df = gastos_df.drop(index=orig_idx)
            gastos_df = pd.concat([gastos_df, edited_per], ignore_index=True)
            save("gastos", gastos_df)
            st.success("Gastos actualizados.")
            st.rerun()
    else:
        st.markdown("<div class='empty'><big>💳</big>Sin gastos en este período.</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — INGRESOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    with st.form("f_ing", clear_on_submit=True):
        c1,c2 = st.columns(2)
        i_c = c1.text_input("Concepto", placeholder="Sueldo, venta…")
        i_m = c2.number_input("Monto $", min_value=0.0, step=1000.0)
        c3,c4 = st.columns(2)
        i_k = c3.selectbox("Categoría", CAT_ING)
        i_f = c4.date_input("Fecha", value=date.today())
        if st.form_submit_button("Registrar ingreso"):
            if i_c.strip() and i_m > 0:
                nv = pd.DataFrame([[str(i_f),i_c.strip(),i_m,i_k]], columns=ingresos_df.columns)
                ingresos_df = pd.concat([ingresos_df,nv], ignore_index=True)
                ingresos_df["Monto"] = to_num(ingresos_df["Monto"])
                save("ingresos", ingresos_df)
                st.success(f"Registrado: {i_c}")
                st.rerun()
            else:
                st.warning("Completá concepto y monto.")

    st.markdown(f"<div class='sec'>{nombre_mes} {y}</div>", unsafe_allow_html=True)
    ing_show = ingresos_mes.sort_values("Fecha", ascending=False)
    if ing_show.empty:
        st.markdown("<div class='empty'><big>💰</big>Sin ingresos este mes.</div>", unsafe_allow_html=True)
    else:
        for _, r in ing_show.iterrows():
            st.markdown(
                "<div class='tx'>"
                f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💰')))}</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]} · {r.get('Categoria','')}</div>"
                "</div>"
                f"<div class='tx-amt c-pos'>+{fmt_ars(r.get('Monto',0))}</div>"
                "</div>",
                unsafe_allow_html=True
            )
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total mes</span>"
            f"<span class='total-strip-val c-pos'>{fmt_ars(ing_show['Monto'].sum())}</span>"
            "</div>",
            unsafe_allow_html=True
        )
    with st.expander("🗑️ Eliminar ingreso"):
        del_i = st.text_input("Concepto", key="del_i")
        if del_i:
            cands = ingresos_df[ingresos_df["Concepto"].str.contains(del_i, case=False, na=False)]
            for idx, r in cands.iterrows():
                ca,cb = st.columns([5,1])
                ca.markdown(f"**{r['Concepto']}** · {r['Fecha']} · {fmt_ars(r['Monto'])}")
                with cb:
                    if st.button("✕", key=f"di_{idx}"):
                        ingresos_df = ingresos_df.drop(index=idx).reset_index(drop=True)
                        save("ingresos", ingresos_df)
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — COMPARTIDOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    with st.form("f_comp", clear_on_submit=True):
        c1,c2 = st.columns(2)
        co_c = c1.text_input("Concepto", placeholder="Cena, hotel…")
        co_m = c2.number_input("Cuánto te deben $", min_value=0.0, step=100.0)
        c3,c4 = st.columns(2)
        co_q = c3.text_input("Quién te debe")
        co_f = c4.date_input("Fecha", value=date.today())
        co_n = st.text_input("Nota", placeholder="Opcional")
        if st.form_submit_button("Registrar deuda"):
            if co_c.strip() and co_m > 0 and co_q.strip():
                nv = pd.DataFrame([[str(co_f),co_c.strip(),co_m,co_q.strip(),"Pendiente",co_n]], columns=comp_df.columns)
                comp_df = pd.concat([comp_df,nv], ignore_index=True)
                comp_df["Monto"] = to_num(comp_df["Monto"])
                save("compartidos", comp_df)
                st.success(f"{co_q} te debe {fmt_ars(co_m)}")
                st.rerun()
            else:
                st.warning("Completá todos los campos.")

    comp_df = load("compartidos"); comp_df["Monto"] = to_num(comp_df["Monto"])
    pends = comp_df[comp_df["Estado"] == "Pendiente"].sort_values("Fecha", ascending=False)
    cobs  = comp_df[comp_df["Estado"] != "Pendiente"].sort_values("Fecha", ascending=False)

    st.markdown("<div class='sec'>Pendientes de cobrar</div>", unsafe_allow_html=True)
    if pends.empty:
        st.markdown("<div class='empty'><big>🎉</big>Todo cobrado.</div>", unsafe_allow_html=True)
    else:
        for idx, r in pends.iterrows():
            ca, cb = st.columns([5,2])
            with ca:
                st.markdown(
                    "<div class='pend-row'>"
                    "<div class='tx-ico'>🤝</div>"
                    "<div class='tx-main'>"
                    f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                    f"<div class='tx-info'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>"
                    "</div>"
                    f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Monto',0))}</div>"
                    "</div>",
                    unsafe_allow_html=True
                )
            with cb:
                if st.button("✓ Cobrado", key=f"cob_{idx}"):
                    comp_df.at[idx,"Estado"] = "Cobrado"
                    save("compartidos", comp_df)
                    st.rerun()
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total a cobrar</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(pends['Monto'].sum())}</span>"
            "</div>",
            unsafe_allow_html=True
        )

    if not cobs.empty:
        with st.expander(f"Historial cobrado ({len(cobs)})"):
            for _, r in cobs.iterrows():
                st.markdown(
                    "<div class='tx'>"
                    "<div class='tx-ico'>✅</div>"
                    "<div class='tx-main'>"
                    f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                    f"<div class='tx-info'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>"
                    "</div>"
                    f"<div class='tx-amt c-dim'>{fmt_ars(r.get('Monto',0))}</div>"
                    "</div>",
                    unsafe_allow_html=True
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — INVERSIONES
# ══════════════════════════════════════════════════════════════════════════════
with tabs[5]:
    with st.form("f_inv", clear_on_submit=True):
        inv_n = st.text_input("Instrumento", placeholder="Plazo fijo, CEDEAR, dólares…")
        c1,c2 = st.columns(2)
        inv_cap = c1.number_input("Capital", min_value=0.0, step=1000.0)
        inv_r   = c2.number_input("Rendimiento", min_value=0.0, step=100.0)
        c3,c4 = st.columns(2)
        inv_mon = c3.selectbox("Moneda", MONEDAS)
        inv_f   = c4.date_input("Fecha", value=date.today())
        inv_nota = st.text_input("Nota", placeholder="Tasa, vencimiento…")
        if st.form_submit_button("Registrar"):
            if inv_n.strip() and inv_cap > 0:
                inv_df = load("inversiones")
                nv = pd.DataFrame([[str(inv_f),inv_n.strip(),inv_cap,inv_r,inv_mon,inv_nota]], columns=inv_df.columns)
                inv_df = pd.concat([inv_df,nv], ignore_index=True)
                save("inversiones", inv_df)
                st.success(f"Registrado: {inv_n}")
                st.rerun()
            else:
                st.warning("Nombre y capital requeridos.")

    inv_df = load("inversiones")
    inv_df["Capital"] = to_num(inv_df.get("Capital", pd.Series()))
    inv_df["Rendimiento"] = to_num(inv_df.get("Rendimiento", pd.Series()))

    if not inv_df.empty:
        tc = inv_df["Capital"].sum(); tr = inv_df["Rendimiento"].sum()
        st.markdown(
            "<div class='stat-row'>"
            f"<div class='stat-cell'><div class='stat-label'>Capital</div><div class='stat-val c-neu'>{fmt_ars(tc)}</div></div>"
            f"<div class='stat-cell'><div class='stat-label'>Rendimiento</div><div class='stat-val c-pos'>+{fmt_ars(tr)}</div></div>"
            "</div>",
            unsafe_allow_html=True
        )
        st.markdown("<div class='sec'>Portafolio</div>", unsafe_allow_html=True)
        for _, r in inv_df.sort_values("Fecha", ascending=False).iterrows():
            st.markdown(
                "<div class='tx'>"
                "<div class='tx-ico'>📈</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Instrumento','—')}</div>"
                f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]} · {r.get('Moneda','ARS')} · {r.get('Notas','')}</div>"
                "</div>"
                "<div style='text-align:right'>"
                f"<div class='tx-amt c-neu'>{fmt_ars(r.get('Capital',0))}</div>"
                f"<div style='font-size:0.68rem;color:#39e07a;font-family:DM Mono,monospace'>+{fmt_ars(r.get('Rendimiento',0))}</div>"
                "</div>"
                "</div>",
                unsafe_allow_html=True
            )
    else:
        st.markdown("<div class='empty'><big>📈</big>Sin inversiones todavía.</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — PRESUPUESTO
# ══════════════════════════════════════════════════════════════════════════════
with tabs[6]:
    with st.expander("⚙️ Configurar límites"):
        with st.form("f_pres"):
            c1,c2 = st.columns(2)
            p_cat = c1.selectbox("Categoría", CAT_GASTOS)
            p_lim = c2.number_input("Límite mensual $", min_value=0.0, step=1000.0)
            if st.form_submit_button("Guardar límite"):
                if p_lim > 0:
                    pres_df_new = pres_df[pres_df["Categoria"] != p_cat].copy()
                    pres_df_new = pd.concat([pres_df_new, pd.DataFrame([[p_cat,p_lim]], columns=["Categoria","Limite"])], ignore_index=True)
                    save("presupuesto", pres_df_new)
                    st.success(f"Límite {fmt_ars(p_lim)} para {p_cat}")
                    st.rerun()

    pres_df = load("presupuesto")
    st.markdown(f"<div class='sec'>Control — {nombre_mes} {y}</div>", unsafe_allow_html=True)
    if pres_df.empty:
        st.markdown("<div class='empty'><big>🎯</big>Configurá límites arriba.</div>", unsafe_allow_html=True)
    else:
        pres_df["Limite"] = to_num(pres_df["Limite"])
        gastado_cat = gastos_mes.groupby("Categoria")["Monto"].sum()
        for _, row in pres_df.sort_values("Categoria").iterrows():
            cat = row["Categoria"]; lim = row["Limite"]
            gast = gastado_cat.get(cat, 0)
            pct = min(int(gast/lim*100), 100) if lim > 0 else 0
            sobra = lim - gast
            col = "#39e07a" if pct < 70 else "#f5c542" if pct < 90 else "#ff5f7e"
            ico = "✓" if pct < 90 else "⚠" if pct < 100 else "✕"
            note = (f"Excedido {fmt_ars(abs(sobra))}" if sobra < 0 else f"Disponible {fmt_ars(sobra)}")
            fill = f"<div class='prog-fill' style='width:{pct}%;background:{col}'></div>"
            st.markdown(
                "<div class='prog-wrap'>"
                f"<div class='prog-head'><span>{ico} {cat}</span><span>{fmt_ars(gast)} / {fmt_ars(lim)}</span></div>"
                f"<div class='prog-bg'>{fill}</div>"
                f"<div class='prog-note' style='color:{col}'>{note}</div>"
                "</div>",
                unsafe_allow_html=True
            )
        t_lim = pres_df["Limite"].sum()
        t_gast = sum(gastado_cat.get(c,0) for c in pres_df["Categoria"])
        st.markdown(
            "<div class='total-strip'>"
            f"<span class='total-strip-label'>Presupuestado total</span>"
            f"<span class='total-strip-val'>{fmt_ars(t_gast)} / {fmt_ars(t_lim)}</span>"
            "</div>",
            unsafe_allow_html=True
        )
