import streamlit as st
import pandas as pd
import os
from datetime import date, datetime, timedelta
import calendar
import json

# ── Archivos ───────────────────────────────────────────────────────────────────
FILES = {
    "gastos":      ("mis_gastos.csv",      ["Fecha","Concepto","Monto","Tarjeta","Cuotas","Categoria","Compartido","Con quien","Cuanto recupero","Notas"]),
    "ingresos":    ("mis_ingresos.csv",    ["Fecha","Concepto","Monto","Categoria"]),
    "compartidos": ("mis_compartidos.csv", ["Fecha","Concepto","Monto","Con quien","Estado","Notas"]),
    "inversiones": ("mis_inversiones.csv", ["Fecha","Instrumento","Capital","Rendimiento","Moneda","Notas"]),
    "presupuesto": ("mis_presupuesto.csv", ["Categoria","Limite"]),
    "tarjetas":    ("mis_tarjetas.csv",    ["Nombre","Dia cierre","Dia vencimiento","Color"]),
}

CAT_GASTOS = ["🍔 Comida","🚗 Transporte","🎉 Entretenimiento","✈️ Viaje","🏥 Salud",
               "👕 Ropa","📱 Servicios","🏠 Casa","💊 Farmacia","📚 Educación","🎁 Regalos","💳 Otro"]
CAT_ING    = ["💼 Sueldo","💻 Freelance","📈 Inversión","🎁 Regalo","💰 Otro"]
ESTADOS    = ["Pendiente","Cobrado","Pagado"]
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
        pd.Series(s).astype(str).str.replace(r"[\$,\s]","",regex=True),
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
    """Convierte a int de forma segura."""
    try:
        if val is None or str(val).strip() in ("", "nan", "None", "N/A"):
            return default
        return int(float(str(val)))
    except (ValueError, TypeError):
        return default

def get_tarjetas_nombres():
    """Devuelve lista de nombres de tarjetas (custom + default)."""
    t_df = load("tarjetas")
    if not t_df.empty:
        nombres = t_df["Nombre"].dropna().tolist()
        # Agregar defaults que no estén
        for d in TARJETAS_DEFAULT:
            if d not in nombres:
                nombres.append(d)
        return nombres
    return TARJETAS_DEFAULT

def get_periodo_tarjeta(tarjeta_nombre, año=None, mes=None):
    """
    Dado un nombre de tarjeta y una fecha (año/mes), calcula el período de facturación.
    Retorna (inicio, fin) del período como date objects.
    Si año/mes son None, usa hoy.
    """
    t_df = load("tarjetas")
    hoy = date.today()
    if año is None:
        año = hoy.year
    if mes is None:
        mes = hoy.month

    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        # Sin configuración: período = mes calendario
        inicio = date(año, mes, 1)
        ultimo = calendar.monthrange(año, mes)[1]
        fin = date(año, mes, ultimo)
        return inicio, fin

    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    dia_cierre = safe_int(row.get("Dia cierre", 1), 1)

    # El período corre desde el día_cierre+1 del mes anterior hasta el día_cierre del mes actual
    # Ejemplo: cierre día 5 → período: del 6/mes-1 al 5/mes
    if dia_cierre >= 28:
        dia_cierre = 28

    # Inicio del período = día_cierre + 1 del mes anterior
    if mes == 1:
        mes_ant = 12
        año_ant = año - 1
    else:
        mes_ant = mes - 1
        año_ant = año

    ultimo_mes_ant = calendar.monthrange(año_ant, mes_ant)[1]
    dia_inicio = min(dia_cierre + 1, ultimo_mes_ant)
    inicio = date(año_ant, mes_ant, dia_inicio)
    fin = date(año, mes, min(dia_cierre, calendar.monthrange(año, mes)[1]))
    return inicio, fin

def gasto_en_periodo(fecha_gasto_str, tarjeta_nombre, año_periodo, mes_periodo):
    """
    Dado un gasto con su fecha y tarjeta, dice si pertenece al período (año_periodo, mes_periodo).
    Tiene en cuenta la lógica de cierre: si cerraste ayer y hacés un gasto hoy, va al próximo resumen.
    """
    try:
        fg = pd.to_datetime(fecha_gasto_str).date()
    except:
        return False
    inicio, fin = get_periodo_tarjeta(tarjeta_nombre, año_periodo, mes_periodo)
    return inicio <= fg <= fin

def periodo_actual_de_gasto(fecha_gasto_str, tarjeta_nombre):
    """
    Dado un gasto, devuelve el (año, mes) del resumen al que pertenece.
    """
    try:
        fg = pd.to_datetime(fecha_gasto_str).date()
    except:
        hoy = date.today()
        return hoy.year, hoy.month

    t_df = load("tarjetas")
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return fg.year, fg.month

    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    dia_cierre = safe_int(row.get("Dia cierre", 1), 1)
    if dia_cierre >= 28:
        dia_cierre = 28

    # Si el día del gasto <= dia_cierre → pertenece al resumen de este mes
    # Si el día del gasto > dia_cierre → pertenece al resumen del mes siguiente
    if fg.day <= dia_cierre:
        return fg.year, fg.month
    else:
        # Mes siguiente
        if fg.month == 12:
            return fg.year + 1, 1
        else:
            return fg.year, fg.month + 1

def filtrar_gastos_tarjeta_periodo(gastos_df, tarjeta_nombre, año_periodo, mes_periodo):
    """Filtra gastos de una tarjeta para un período de resumen específico."""
    if gastos_df.empty:
        return gastos_df
    mask = []
    for _, r in gastos_df.iterrows():
        if str(r.get("Tarjeta","")) == tarjeta_nombre:
            ay, am = periodo_actual_de_gasto(str(r.get("Fecha","")), tarjeta_nombre)
            mask.append(ay == año_periodo and am == mes_periodo)
        else:
            mask.append(False)
    return gastos_df[mask]

# ── Página ─────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="💳 JuanB", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stApp {
    font-family: 'Inter', sans-serif !important;
    background-color: #0f0f14 !important;
    color: #e8e8f0 !important;
}

#MainMenu, header, footer { visibility: hidden; }
.block-container { padding: 0.8rem 0.8rem 5rem !important; max-width: 480px !important; margin: auto; }

/* ── Tabs ── */
[data-baseweb="tab-list"] {
    background: #1a1a24 !important;
    border-radius: 14px !important;
    padding: 4px !important;
    gap: 2px !important;
    border: 1px solid #2a2a38 !important;
}
[data-baseweb="tab"] {
    border-radius: 10px !important;
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    padding: 6px 8px !important;
    color: #888 !important;
    background: transparent !important;
}
[aria-selected="true"][data-baseweb="tab"] {
    background: #7c6af7 !important;
    color: #fff !important;
}

/* ── Cards ── */
.card {
    background: #1a1a24;
    border: 1px solid #2a2a38;
    border-radius: 16px;
    padding: 1rem 1.1rem 0.9rem;
    margin-bottom: 0.6rem;
}
.card-label {
    font-size: 0.72rem;
    font-weight: 600;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 2px;
}
.card-value {
    font-size: 1.9rem;
    font-weight: 800;
    line-height: 1.1;
}
.card-sub { font-size: 0.75rem; color: #666; margin-top: 3px; }
.green  { color: #4ade80; }
.red    { color: #f87171; }
.purple { color: #a78bfa; }
.yellow { color: #fbbf24; }
.white  { color: #e8e8f0; }

/* ── Grid ── */
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; margin-bottom: 0.6rem; }
.card-sm {
    background: #1a1a24;
    border: 1px solid #2a2a38;
    border-radius: 14px;
    padding: 0.8rem 0.9rem;
}
.card-sm .card-value { font-size: 1.3rem; font-weight: 700; }

/* ── Tarjeta visual ── */
.tarjeta-card {
    background: #1a1a24;
    border: 1px solid #2a2a38;
    border-radius: 14px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.8rem;
}
.tarjeta-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
}
.tarjeta-nombre {
    font-size: 0.88rem;
    font-weight: 700;
    color: #e8e8f0;
}
.tarjeta-meta {
    font-size: 0.72rem;
    color: #666;
    margin-top: 1px;
}
.tarjeta-monto {
    margin-left: auto;
    font-size: 1.05rem;
    font-weight: 800;
    text-align: right;
    flex-shrink: 0;
}

/* ── Fila transacción ── */
.tx-row {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.7rem 0;
    border-bottom: 1px solid #1e1e2c;
}
.tx-icon {
    width: 38px; height: 38px;
    background: #22223a;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.1rem; flex-shrink: 0;
}
.tx-concepto { font-size: 0.87rem; font-weight: 600; color: #e8e8f0; }
.tx-meta     { font-size: 0.72rem; color: #666; }
.tx-monto    { margin-left: auto; font-size: 0.95rem; font-weight: 700; text-align: right; flex-shrink: 0; }

/* ── Barra progreso ── */
.prog-wrap { margin-bottom: 0.9rem; }
.prog-header { display: flex; justify-content: space-between; font-size: 0.8rem; margin-bottom: 4px; }
.prog-bar-bg { background: #22223a; border-radius: 99px; height: 8px; overflow: hidden; }
.prog-bar-fill { height: 100%; border-radius: 99px; transition: width 0.4s ease; }

/* ── Botón FAB (+) ── */
.fab-container {
    position: fixed;
    bottom: 1.5rem;
    right: 1.2rem;
    z-index: 9999;
}
.fab-btn {
    width: 54px;
    height: 54px;
    border-radius: 50%;
    background: linear-gradient(135deg, #7c6af7, #a78bfa);
    border: none;
    color: white;
    font-size: 1.6rem;
    font-weight: 300;
    line-height: 1;
    cursor: pointer;
    box-shadow: 0 4px 20px #7c6af755, 0 2px 8px #0008;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.fab-btn:hover {
    transform: scale(1.08);
    box-shadow: 0 6px 28px #7c6af788, 0 2px 10px #000a;
}
.fab-btn:active { transform: scale(0.96); }

/* ── Modal overlay ── */
.modal-overlay {
    position: fixed;
    inset: 0;
    background: #000000cc;
    backdrop-filter: blur(4px);
    z-index: 9998;
}

/* ── Botón principal ── */
.stButton > button {
    background: #7c6af7 !important;
    color: #fff !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    font-size: 0.9rem !important;
    padding: 0.6rem 1rem !important;
    width: 100% !important;
    transition: background 0.2s !important;
}
.stButton > button:hover { background: #6b59e6 !important; }
.stButton > button:active { background: #5a48d5 !important; }

.btn-danger > button {
    background: #3a1a1a !important;
    color: #f87171 !important;
    border: 1px solid #4a2020 !important;
}
.btn-sm > button {
    padding: 0.35rem 0.6rem !important;
    font-size: 0.78rem !important;
    border-radius: 8px !important;
}

/* ── Inputs ── */
.stTextInput input, .stNumberInput input, .stDateInput input,
.stSelectbox div[data-baseweb="select"] > div {
    background: #1e1e2c !important;
    border: 1px solid #2e2e44 !important;
    border-radius: 10px !important;
    color: #e8e8f0 !important;
    font-size: 0.9rem !important;
}
.stTextInput input:focus, .stNumberInput input:focus {
    border-color: #7c6af7 !important;
    box-shadow: 0 0 0 2px #7c6af720 !important;
}
label[data-testid="stWidgetLabel"] p {
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    color: #888 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

/* ── Expander ── */
[data-testid="stExpander"] {
    background: #1a1a24 !important;
    border: 1px solid #2a2a38 !important;
    border-radius: 14px !important;
}

hr { border-color: #1e1e2c !important; }

.sec-title {
    font-size: 0.72rem;
    font-weight: 700;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin: 1.1rem 0 0.5rem;
}

.badge { display: inline-block; padding: 2px 8px; border-radius: 99px; font-size: 0.68rem; font-weight: 700; }
.badge-pend  { background: #2a2010; color: #fbbf24; }
.badge-ok    { background: #0f2a1a; color: #4ade80; }

.periodo-badge {
    display: inline-block;
    background: #22223a;
    border: 1px solid #2a2a38;
    border-radius: 8px;
    padding: 3px 10px;
    font-size: 0.72rem;
    font-weight: 600;
    color: #a78bfa;
    margin-bottom: 0.5rem;
}

[data-testid="stForm"] {
    background: #1a1a24;
    border: 1px solid #2a2a38;
    border-radius: 16px;
    padding: 1rem !important;
}

/* Chip próximo periodo */
.chip-prox {
    font-size: 0.68rem;
    background: #1a1a30;
    border: 1px solid #7c6af740;
    color: #a78bfa;
    border-radius: 99px;
    padding: 2px 8px;
    display: inline-block;
    margin-left: 6px;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "show_quick_add" not in st.session_state:
    st.session_state.show_quick_add = False
if "active_tab" not in st.session_state:
    st.session_state.active_tab = 0

# ── Cargar datos ───────────────────────────────────────────────────────────────
gastos_df   = load("gastos")
ingresos_df = load("ingresos")
comp_df     = load("compartidos")
inv_df      = load("inversiones")
pres_df     = load("presupuesto")
tarjetas_df = load("tarjetas")

gastos_df["Monto"]            = to_num(gastos_df["Monto"])
gastos_df["Cuanto recupero"]  = to_num(gastos_df["Cuanto recupero"])
ingresos_df["Monto"]          = to_num(ingresos_df["Monto"])
comp_df["Monto"]              = to_num(comp_df["Monto"])
inv_df["Capital"]             = to_num(inv_df.get("Capital", pd.Series()))
inv_df["Rendimiento"]         = to_num(inv_df.get("Rendimiento", pd.Series()))

y, m = mes_actual()
nombre_mes = calendar.month_name[m].capitalize()

gastos_mes   = filtrar_mes(gastos_df, y, m)
ingresos_mes = filtrar_mes(ingresos_df, y, m)

total_ing   = ingresos_mes["Monto"].sum()
total_gast  = gastos_mes["Monto"].sum()
recupero    = gastos_mes["Cuanto recupero"].sum()
remanente   = total_ing - total_gast + recupero

TARJETAS = get_tarjetas_nombres()

# ── FAB: botón + flotante ──────────────────────────────────────────────────────
# El FAB se muestra siempre. Al clickearlo activa el quick-add form.
st.markdown("""
<div class='fab-container'>
    <div style='position:relative'>
        <div style='
            width:54px;height:54px;border-radius:50%;
            background:linear-gradient(135deg,#7c6af7,#a78bfa);
            display:flex;align-items:center;justify-content:center;
            font-size:1.7rem;color:white;
            box-shadow:0 4px 20px #7c6af755,0 2px 8px #0008;
            cursor:pointer;font-weight:300;line-height:1;
        '>＋</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── Quick Add (toggle via botón en sidebar / session state) ───────────────────
col_fab1, col_fab2 = st.columns([5, 1])
with col_fab2:
    if st.button("＋", key="fab_open", help="Agregar gasto rápido"):
        st.session_state.show_quick_add = not st.session_state.show_quick_add
        st.rerun()

# Modal de carga rápida
if st.session_state.show_quick_add:
    st.markdown("""
    <div style='
        background:#1a1a28;
        border:1px solid #7c6af740;
        border-radius:20px;
        padding:1.2rem 1rem 0.8rem;
        margin-bottom:1rem;
        box-shadow:0 8px 40px #7c6af730;
    '>
    <div style='font-size:0.95rem;font-weight:700;color:#a78bfa;margin-bottom:0.8rem'>⚡ Gasto rápido</div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("f_quick", clear_on_submit=True):
        q_concepto = st.text_input("Concepto", placeholder="¿En qué gastaste?")
        q_monto    = st.number_input("Monto $", min_value=0.0, step=100.0)
        col1, col2 = st.columns(2)
        q_tarjeta  = col1.selectbox("Tarjeta", TARJETAS)
        q_cat      = col2.selectbox("Categoría", CAT_GASTOS)
        col3, col4 = st.columns(2)
        q_fecha    = col3.date_input("Fecha", value=date.today())
        q_cuotas   = col4.number_input("Cuotas", min_value=1, max_value=48, value=1)

        # Mostrar a qué período pertenece
        ay, am = periodo_actual_de_gasto(str(q_fecha), q_tarjeta)
        if ay != y or am != m:
            nombre_per = f"{calendar.month_name[am].capitalize()} {ay}"
            st.markdown(f"<div style='font-size:0.75rem;color:#a78bfa;margin:-0.3rem 0 0.4rem'>📅 Va al resumen de <strong>{nombre_per}</strong></div>", unsafe_allow_html=True)

        col_ok, col_x = st.columns([3,1])
        submitted_q = col_ok.form_submit_button("💾 Anotar", use_container_width=True)
        cancel_q    = col_x.form_submit_button("✕ Cerrar", use_container_width=True)

        if submitted_q:
            if not q_concepto.strip():
                st.warning("Falta el concepto.")
            elif q_monto <= 0:
                st.warning("Monto mayor a $0.")
            else:
                nuevo = pd.DataFrame([[str(q_fecha), q_concepto.strip(), q_monto, q_tarjeta,
                                       q_cuotas, q_cat, "No", "", 0, ""]],
                                     columns=gastos_df.columns)
                gastos_df = pd.concat([gastos_df, nuevo], ignore_index=True)
                save("gastos", gastos_df)
                st.session_state.show_quick_add = False
                st.success(f"✅ {q_concepto} — {fmt_ars(q_monto)}")
                st.rerun()
        if cancel_q:
            st.session_state.show_quick_add = False
            st.rerun()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tabs = st.tabs(["🏠 Home", "➕ Gastos", "💰 Ingresos", "🤝 Compart.", "📈 Inv.", "🎯 Pres.", "💳 Tarjetas"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 0: HOME
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    st.markdown(f"<div style='font-size:1.05rem;font-weight:700;color:#888;margin-bottom:0.8rem'>📅 {nombre_mes} {y}</div>", unsafe_allow_html=True)

    # Remanente
    color_rem = "green" if remanente >= 0 else "red"
    st.markdown(f"""
    <div class='card'>
        <div class='card-label'>Remanente del mes</div>
        <div class='card-value {color_rem}'>{fmt_ars(remanente)}</div>
        <div class='card-sub'>Ingresos − Gastos + Lo que recuperás</div>
    </div>""", unsafe_allow_html=True)

    # Grid ingresos / gastos
    st.markdown(f"""
    <div class='grid2'>
        <div class='card-sm'>
            <div class='card-label'>Ingresos</div>
            <div class='card-value green'>{fmt_ars(total_ing)}</div>
        </div>
        <div class='card-sm'>
            <div class='card-label'>Gastos</div>
            <div class='card-value red'>{fmt_ars(total_gast)}</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if recupero > 0:
        st.markdown(f"""
        <div class='card-sm' style='margin-bottom:0.6rem'>
            <div class='card-label'>💸 A recuperar de compartidos</div>
            <div class='card-value yellow'>{fmt_ars(recupero)}</div>
        </div>""", unsafe_allow_html=True)

    # ── Resumen por tarjeta (NUEVO) ──────────────────────────────────────────
    st.markdown("<div class='sec-title'>Resumen por tarjeta — mes actual</div>", unsafe_allow_html=True)

    tarjetas_con_gasto = {}
    for tname in TARJETAS:
        # Para tarjetas configuradas, usar lógica de período
        t_df_check = load("tarjetas")
        if not t_df_check.empty and tname in t_df_check["Nombre"].values:
            gf = filtrar_gastos_tarjeta_periodo(gastos_df, tname, y, m)
        else:
            # Sin config: filtrar por mes calendario
            gf = gastos_mes[gastos_mes["Tarjeta"] == tname] if not gastos_mes.empty else pd.DataFrame()

        total_t = gf["Monto"].sum() if not gf.empty else 0
        if total_t > 0:
            tarjetas_con_gasto[tname] = total_t

    if not gastos_df.empty and tarjetas_con_gasto:
        # Color por tarjeta
        color_map = {}
        t_df_colors = load("tarjetas")
        for tname, total_t in tarjetas_con_gasto.items():
            color = "#7c6af7"
            if not t_df_colors.empty and tname in t_df_colors["Nombre"].values:
                row_c = t_df_colors[t_df_colors["Nombre"] == tname].iloc[0]
                color = str(row_c.get("Color", "#7c6af7")) or "#7c6af7"
            else:
                # Asignar color por índice
                idx = list(TARJETAS).index(tname) if tname in TARJETAS else 0
                color = COLORES_TARJETA[idx % len(COLORES_TARJETA)]
            color_map[tname] = color

        max_t = max(tarjetas_con_gasto.values()) if tarjetas_con_gasto else 1
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = color_map[tname]
            pct = int(total_t / max_t * 100)

            # Info de cierre
            t_df_info = load("tarjetas")
            meta_info = ""
            if not t_df_info.empty and tname in t_df_info["Nombre"].values:
                row_i = t_df_info[t_df_info["Nombre"] == tname].iloc[0]
                dc = safe_int(row_i.get("Dia cierre", ""), 0)
                dv = safe_int(row_i.get("Dia vencimiento", ""), 0)
                if dc > 0:
                    meta_info = f"Cierre día {dc}"
                if dv > 0:
                    meta_info += f" · Vence día {dv}"

            # Separar el HTML condicional ANTES del f-string para evitar que Streamlit lo escape
            meta_html = f"<div class='tarjeta-meta'>{meta_info}</div>" if meta_info else ""

            html_tarjeta = (
                "<div class='tarjeta-card'>"
                f"<div class='tarjeta-dot' style='background:{color}'></div>"
                f"<div style='flex:1;min-width:0'>"
                f"<div class='tarjeta-nombre'>{tname}</div>"
                f"{meta_html}"
                f"<div style='margin-top:6px;background:#22223a;border-radius:99px;height:5px;overflow:hidden'>"
                f"<div style='width:{pct}%;height:100%;background:{color};border-radius:99px'></div>"
                "</div>"
                "</div>"
                f"<div class='tarjeta-monto' style='color:{color}'>−{fmt_ars(total_t)}</div>"
                "</div>"
            )
            st.markdown(html_tarjeta, unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:0.8rem'>Sin gastos este mes.</div>", unsafe_allow_html=True)

    # Gráfico por categoría
    if not gastos_mes.empty:
        st.markdown("<div class='sec-title'>Gastos por categoría</div>", unsafe_allow_html=True)
        cat_sum = gastos_mes.groupby("Categoria")["Monto"].sum().sort_values(ascending=False).head(6)
        max_val = cat_sum.max() if cat_sum.max() > 0 else 1
        colors  = ["#7c6af7","#a78bfa","#c4b5fd","#4ade80","#fbbf24","#f87171"]
        for i, (cat, val) in enumerate(cat_sum.items()):
            pct = int(val / max_val * 100)
            color = colors[i % len(colors)]
            st.markdown(f"""
            <div class='prog-wrap'>
                <div class='prog-header'>
                    <span>{cat}</span>
                    <span style='font-weight:700'>{fmt_ars(val)}</span>
                </div>
                <div class='prog-bar-bg'>
                    <div class='prog-bar-fill' style='width:{pct}%;background:{color}'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    # Últimos movimientos
    st.markdown("<div class='sec-title'>Últimos movimientos</div>", unsafe_allow_html=True)
    recientes = gastos_df.sort_values("Fecha", ascending=False).head(5)
    if recientes.empty:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:1rem'>Todavía no hay gastos cargados.</div>", unsafe_allow_html=True)
    else:
        for _, r in recientes.iterrows():
            icono = emoji_cat(str(r.get("Categoria","💳")))
            fecha_str = str(r.get("Fecha",""))[:10]
            tname_r = str(r.get("Tarjeta",""))

            # Ver si este gasto es del período actual o del próximo
            ay, am = periodo_actual_de_gasto(str(r.get("Fecha","")), tname_r)
            chip = ""
            if ay != y or am != m:
                chip = f"<span class='chip-prox'>→ {calendar.month_name[am][:3]}</span>"

            cuotas_v = safe_int(r.get("Cuotas", 1), 1)
            cuotas_txt = f" · {cuotas_v}c" if cuotas_v > 1 else ""

            st.markdown(f"""
            <div class='tx-row'>
                <div class='tx-icon'>{icono}</div>
                <div>
                    <div class='tx-concepto'>{r.get('Concepto','—')}{chip}</div>
                    <div class='tx-meta'>{fecha_str} · {tname_r}{cuotas_txt}</div>
                </div>
                <div class='tx-monto red'>−{fmt_ars(r.get('Monto',0))}</div>
            </div>""", unsafe_allow_html=True)

    # Pendientes de cobrar
    pend = comp_df[comp_df["Estado"] == "Pendiente"] if not comp_df.empty else pd.DataFrame()
    if not pend.empty:
        st.markdown("<div class='sec-title'>💸 Pendiente de cobrar</div>", unsafe_allow_html=True)
        total_pend = pend["Monto"].sum()
        for _, r in pend.iterrows():
            st.markdown(f"""
            <div class='tx-row'>
                <div class='tx-icon'>🤝</div>
                <div>
                    <div class='tx-concepto'>{r.get('Concepto','—')}</div>
                    <div class='tx-meta'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>
                </div>
                <div class='tx-monto yellow'>{fmt_ars(r.get('Monto',0))}</div>
            </div>""", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:right;font-size:0.8rem;color:#fbbf24;font-weight:700;margin-top:4px'>Total pendiente: {fmt_ars(total_pend)}</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: GASTOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    with st.form("f_gasto", clear_on_submit=True):
        st.markdown("<div style='font-weight:700;font-size:0.95rem;margin-bottom:0.7rem'>Nuevo gasto</div>", unsafe_allow_html=True)
        concepto  = st.text_input("Concepto", placeholder="Ej: medialunas, nafta, Spotify…")
        monto     = st.number_input("Monto $", min_value=0.0, step=500.0)
        col1, col2 = st.columns(2)
        tarjeta   = col1.selectbox("Tarjeta", TARJETAS)
        categoria = col2.selectbox("Categoría", CAT_GASTOS)
        col3, col4 = st.columns(2)
        fecha     = col3.date_input("Fecha", value=date.today())
        cuotas    = col4.number_input("Cuotas", min_value=1, max_value=48, value=1)
        col5, col6 = st.columns(2)
        compartido = col5.selectbox("¿Compartido?", ["No","Sí"])
        con_quien  = col6.text_input("¿Con quién?", placeholder="Nombre")
        recupero_v = 0.0
        if compartido == "Sí":
            recupero_v = st.number_input("¿Cuánto recuperás? $", min_value=0.0, step=100.0)
        notas = st.text_input("Nota rápida (opcional)", placeholder="Ej: cumple de Lau…")

        # Preview período
        ay_p, am_p = periodo_actual_de_gasto(str(fecha), tarjeta)
        if ay_p != y or am_p != m:
            st.markdown(f"<div style='font-size:0.75rem;color:#a78bfa;margin-bottom:0.3rem'>📅 Este gasto va al resumen de <strong>{calendar.month_name[am_p].capitalize()} {ay_p}</strong></div>", unsafe_allow_html=True)

        if st.form_submit_button("💾 Anotar gasto"):
            if not concepto.strip():
                st.warning("Falta el concepto.")
            elif monto <= 0:
                st.warning("El monto tiene que ser mayor a $0.")
            else:
                nuevo = pd.DataFrame([[str(fecha), concepto.strip(), monto, tarjeta,
                                       cuotas, categoria, compartido, con_quien,
                                       recupero_v, notas]], columns=gastos_df.columns)
                gastos_df = pd.concat([gastos_df, nuevo], ignore_index=True)
                save("gastos", gastos_df)
                if compartido == "Sí" and recupero_v > 0:
                    nuevo_c = pd.DataFrame([[str(fecha), concepto.strip(), recupero_v,
                                             con_quien, "Pendiente", ""]], columns=comp_df.columns)
                    comp_df_upd = pd.concat([comp_df, nuevo_c], ignore_index=True)
                    comp_df_upd["Monto"] = to_num(comp_df_upd["Monto"])
                    save("compartidos", comp_df_upd)
                st.success(f"✅ {concepto} — {fmt_ars(monto)} anotado")
                st.rerun()

    st.markdown(f"<div class='sec-title'>Gastos de {nombre_mes}</div>", unsafe_allow_html=True)
    cats_usadas = ["Todas"] + sorted(gastos_mes["Categoria"].dropna().unique().tolist())
    cat_sel = st.selectbox("Filtrar categoría", cats_usadas, label_visibility="collapsed")
    df_show = gastos_mes if cat_sel == "Todas" else gastos_mes[gastos_mes["Categoria"] == cat_sel]
    df_show = df_show.sort_values("Fecha", ascending=False)

    if df_show.empty:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:1.5rem'>Sin gastos este mes.</div>", unsafe_allow_html=True)
    else:
        for idx, r in df_show.iterrows():
            icono = emoji_cat(str(r.get("Categoria","💳")))
            fecha_str = str(r.get("Fecha",""))[:10]
            cuotas_val = safe_int(r.get("Cuotas", 1), 1)
            cuotas_txt = f" · {cuotas_val}c" if cuotas_val > 1 else ""
            comp_txt   = f" · con {r.get('Con quien','')}" if r.get("Compartido","No") == "Sí" else ""
            st.markdown(f"""
            <div class='tx-row'>
                <div class='tx-icon'>{icono}</div>
                <div style='flex:1;min-width:0'>
                    <div class='tx-concepto' style='white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{r.get('Concepto','—')}</div>
                    <div class='tx-meta'>{fecha_str} · {r.get('Tarjeta','')}{cuotas_txt}{comp_txt}</div>
                </div>
                <div class='tx-monto red'>−{fmt_ars(r.get('Monto',0))}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div style='text-align:right;font-size:0.85rem;font-weight:700;
            color:#f87171;margin-top:0.8rem;padding-top:0.5rem;
            border-top:1px solid #1e1e2c'>
            Total: {fmt_ars(df_show['Monto'].sum())}
        </div>""", unsafe_allow_html=True)

    with st.expander("🗑️ Eliminar gastos"):
        st.caption("Escribí el concepto exacto (o parte) para borrar:")
        del_text = st.text_input("Buscar concepto a eliminar", key="del_gasto")
        if del_text:
            candidatos = gastos_df[gastos_df["Concepto"].str.contains(del_text, case=False, na=False)]
            if candidatos.empty:
                st.caption("No encontré nada.")
            else:
                for idx, r in candidatos.iterrows():
                    c1, c2 = st.columns([4,1])
                    c1.markdown(f"**{r['Concepto']}** — {r['Fecha']} — {fmt_ars(r['Monto'])}")
                    with c2:
                        if st.button("✕", key=f"del_{idx}"):
                            gastos_df = gastos_df.drop(index=idx).reset_index(drop=True)
                            save("gastos", gastos_df)
                            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: INGRESOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    with st.form("f_ingreso", clear_on_submit=True):
        st.markdown("<div style='font-weight:700;font-size:0.95rem;margin-bottom:0.7rem'>Nuevo ingreso</div>", unsafe_allow_html=True)
        i_concepto  = st.text_input("Concepto", placeholder="Ej: sueldo, pago freelance…")
        i_monto     = st.number_input("Monto $", min_value=0.0, step=1000.0)
        col1, col2  = st.columns(2)
        i_cat       = col1.selectbox("Categoría", CAT_ING)
        i_fecha     = col2.date_input("Fecha", value=date.today())
        if st.form_submit_button("💚 Registrar ingreso"):
            if i_monto > 0 and i_concepto.strip():
                nuevo_i = pd.DataFrame([[str(i_fecha), i_concepto.strip(), i_monto, i_cat]],
                                       columns=ingresos_df.columns)
                ingresos_df = pd.concat([ingresos_df, nuevo_i], ignore_index=True)
                ingresos_df["Monto"] = to_num(ingresos_df["Monto"])
                save("ingresos", ingresos_df)
                st.success(f"✅ {i_concepto} — {fmt_ars(i_monto)}")
                st.rerun()
            else:
                st.warning("Completá concepto y monto.")

    st.markdown(f"<div class='sec-title'>Ingresos de {nombre_mes}</div>", unsafe_allow_html=True)
    ing_show = ingresos_mes.sort_values("Fecha", ascending=False)
    if ing_show.empty:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:1.5rem'>Sin ingresos registrados este mes.</div>", unsafe_allow_html=True)
    else:
        for _, r in ing_show.iterrows():
            st.markdown(f"""
            <div class='tx-row'>
                <div class='tx-icon'>{emoji_cat(str(r.get('Categoria','💰')))}</div>
                <div>
                    <div class='tx-concepto'>{r.get('Concepto','—')}</div>
                    <div class='tx-meta'>{str(r.get('Fecha',''))[:10]} · {r.get('Categoria','')}</div>
                </div>
                <div class='tx-monto green'>+{fmt_ars(r.get('Monto',0))}</div>
            </div>""", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align:right;font-size:0.85rem;font-weight:700;color:#4ade80;margin-top:0.8rem;padding-top:0.5rem;border-top:1px solid #1e1e2c'>Total: {fmt_ars(ing_show['Monto'].sum())}</div>", unsafe_allow_html=True)

    with st.expander("🗑️ Eliminar ingreso"):
        del_ing = st.text_input("Buscar concepto", key="del_ing")
        if del_ing:
            cand = ingresos_df[ingresos_df["Concepto"].str.contains(del_ing, case=False, na=False)]
            for idx, r in cand.iterrows():
                c1, c2 = st.columns([4,1])
                c1.markdown(f"**{r['Concepto']}** — {r['Fecha']} — {fmt_ars(r['Monto'])}")
                with c2:
                    if st.button("✕", key=f"deli_{idx}"):
                        ingresos_df = ingresos_df.drop(index=idx).reset_index(drop=True)
                        save("ingresos", ingresos_df)
                        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3: COMPARTIDOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    with st.form("f_comp", clear_on_submit=True):
        st.markdown("<div style='font-weight:700;font-size:0.95rem;margin-bottom:0.7rem'>Registrar gasto compartido</div>", unsafe_allow_html=True)
        c_concepto = st.text_input("Concepto", placeholder="Ej: cena, hotel…")
        c_monto    = st.number_input("¿Cuánto te deben? $", min_value=0.0, step=100.0)
        col1, col2 = st.columns(2)
        c_quien    = col1.text_input("¿Quién te debe?")
        c_fecha    = col2.date_input("Fecha", value=date.today())
        c_notas    = st.text_input("Notas", placeholder="Opcional")
        if st.form_submit_button("💸 Registrar deuda"):
            if c_monto > 0 and c_concepto.strip() and c_quien.strip():
                nuevo_c = pd.DataFrame([[str(c_fecha), c_concepto.strip(), c_monto,
                                         c_quien.strip(), "Pendiente", c_notas]],
                                       columns=comp_df.columns)
                comp_df_new = pd.concat([comp_df, nuevo_c], ignore_index=True)
                comp_df_new["Monto"] = to_num(comp_df_new["Monto"])
                save("compartidos", comp_df_new)
                st.success(f"✅ {c_quien} te debe {fmt_ars(c_monto)}")
                st.rerun()
            else:
                st.warning("Completá todos los campos.")

    st.markdown("<div class='sec-title'>Pendientes de cobrar</div>", unsafe_allow_html=True)
    comp_df = load("compartidos"); comp_df["Monto"] = to_num(comp_df["Monto"])
    pendientes = comp_df[comp_df["Estado"] == "Pendiente"].sort_values("Fecha", ascending=False)
    cobrados   = comp_df[comp_df["Estado"] != "Pendiente"].sort_values("Fecha", ascending=False)

    if pendientes.empty:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:1rem'>Todo cobrado 🎉</div>", unsafe_allow_html=True)
    else:
        for idx, r in pendientes.iterrows():
            col1, col2 = st.columns([5,2])
            with col1:
                st.markdown(f"""
                <div class='tx-row' style='border:0;padding:0.4rem 0'>
                    <div class='tx-icon'>🤝</div>
                    <div>
                        <div class='tx-concepto'>{r.get('Concepto','—')}</div>
                        <div class='tx-meta'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>
                    </div>
                    <div class='tx-monto yellow'>{fmt_ars(r.get('Monto',0))}</div>
                </div>""", unsafe_allow_html=True)
            with col2:
                if st.button("✅ Cobrado", key=f"cob_{idx}"):
                    comp_df.at[idx, "Estado"] = "Cobrado"
                    save("compartidos", comp_df)
                    st.rerun()

    if not cobrados.empty:
        with st.expander(f"Historial cobrado ({len(cobrados)})"):
            for _, r in cobrados.iterrows():
                st.markdown(f"""
                <div class='tx-row'>
                    <div class='tx-icon'>✅</div>
                    <div>
                        <div class='tx-concepto'>{r.get('Concepto','—')}</div>
                        <div class='tx-meta'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>
                    </div>
                    <div class='tx-monto' style='color:#666'>{fmt_ars(r.get('Monto',0))}</div>
                </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4: INVERSIONES
# ══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    with st.form("f_inv", clear_on_submit=True):
        st.markdown("<div style='font-weight:700;font-size:0.95rem;margin-bottom:0.7rem'>Nueva inversión / ahorro</div>", unsafe_allow_html=True)
        inv_nombre = st.text_input("Instrumento", placeholder="Ej: Plazo fijo, CEDEAR, dólares…")
        col1, col2 = st.columns(2)
        inv_cap    = col1.number_input("Capital invertido", min_value=0.0, step=1000.0)
        inv_rend   = col2.number_input("Rendimiento / ganancia", min_value=0.0, step=100.0)
        col3, col4 = st.columns(2)
        inv_mon    = col3.selectbox("Moneda", MONEDAS)
        inv_fecha  = col4.date_input("Fecha", value=date.today())
        inv_notas  = st.text_input("Notas", placeholder="Vence el…, tasa…")
        if st.form_submit_button("📈 Registrar"):
            if inv_cap > 0 and inv_nombre.strip():
                nuevo_inv = pd.DataFrame([[str(inv_fecha), inv_nombre.strip(), inv_cap,
                                           inv_rend, inv_mon, inv_notas]],
                                         columns=inv_df.columns)
                inv_df_new = pd.concat([inv_df, nuevo_inv], ignore_index=True)
                inv_df_new["Capital"] = to_num(inv_df_new["Capital"])
                inv_df_new["Rendimiento"] = to_num(inv_df_new["Rendimiento"])
                save("inversiones", inv_df_new)
                st.success(f"✅ {inv_nombre} por {fmt_ars(inv_cap)} {inv_mon}")
                st.rerun()

    inv_df = load("inversiones")
    inv_df["Capital"] = to_num(inv_df["Capital"])
    inv_df["Rendimiento"] = to_num(inv_df["Rendimiento"])

    if not inv_df.empty:
        st.markdown("<div class='sec-title'>Portafolio</div>", unsafe_allow_html=True)
        total_cap  = inv_df["Capital"].sum()
        total_rend = inv_df["Rendimiento"].sum()
        st.markdown(f"""
        <div class='grid2'>
            <div class='card-sm'>
                <div class='card-label'>Capital total</div>
                <div class='card-value purple'>{fmt_ars(total_cap)}</div>
            </div>
            <div class='card-sm'>
                <div class='card-label'>Rendimiento</div>
                <div class='card-value green'>+{fmt_ars(total_rend)}</div>
            </div>
        </div>""", unsafe_allow_html=True)

        for _, r in inv_df.sort_values("Fecha", ascending=False).iterrows():
            st.markdown(f"""
            <div class='tx-row'>
                <div class='tx-icon'>📈</div>
                <div>
                    <div class='tx-concepto'>{r.get('Instrumento','—')}</div>
                    <div class='tx-meta'>{str(r.get('Fecha',''))[:10]} · {r.get('Moneda','ARS')} · {r.get('Notas','')}</div>
                </div>
                <div style='margin-left:auto;text-align:right;flex-shrink:0'>
                    <div class='tx-monto purple'>{fmt_ars(r.get('Capital',0))}</div>
                    <div style='font-size:0.72rem;color:#4ade80'>+{fmt_ars(r.get('Rendimiento',0))}</div>
                </div>
            </div>""", unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:2rem'>Sin inversiones registradas.</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5: PRESUPUESTO
# ══════════════════════════════════════════════════════════════════════════════
with tabs[5]:
    with st.expander("⚙️ Configurar límites mensuales"):
        with st.form("f_pres"):
            pres_cat = st.selectbox("Categoría", CAT_GASTOS)
            pres_lim = st.number_input("Límite mensual $", min_value=0.0, step=1000.0)
            if st.form_submit_button("Guardar límite"):
                if pres_lim > 0:
                    pres_df_new = pres_df[pres_df["Categoria"] != pres_cat].copy()
                    fila = pd.DataFrame([[pres_cat, pres_lim]], columns=["Categoria","Limite"])
                    pres_df_new = pd.concat([pres_df_new, fila], ignore_index=True)
                    save("presupuesto", pres_df_new)
                    st.success(f"Límite de {fmt_ars(pres_lim)} para {pres_cat}")
                    st.rerun()

    st.markdown(f"<div class='sec-title'>Control de presupuesto — {nombre_mes}</div>", unsafe_allow_html=True)
    pres_df = load("presupuesto")
    if pres_df.empty:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:2rem'>Configurá límites arriba para ver el control.</div>", unsafe_allow_html=True)
    else:
        pres_df["Limite"] = to_num(pres_df["Limite"])
        gastado_cat = gastos_mes.groupby("Categoria")["Monto"].sum()

        for _, row in pres_df.sort_values("Categoria").iterrows():
            cat    = row["Categoria"]
            limite = row["Limite"]
            gast   = gastado_cat.get(cat, 0)
            pct    = min(int(gast / limite * 100), 100) if limite > 0 else 0
            sobra  = limite - gast
            color  = "#4ade80" if pct < 70 else "#fbbf24" if pct < 90 else "#f87171"
            estado = "✅" if pct < 90 else "⚠️" if pct < 100 else "🚨"
            st.markdown(f"""
            <div class='prog-wrap'>
                <div class='prog-header'>
                    <span>{estado} {cat}</span>
                    <span style='color:{color};font-weight:700'>{fmt_ars(gast)} / {fmt_ars(limite)}</span>
                </div>
                <div class='prog-bar-bg'>
                    <div class='prog-bar-fill' style='width:{pct}%;background:{color}'></div>
                </div>
                <div style='font-size:0.72rem;color:#666;margin-top:3px'>
                    {'🚨 Excedido en ' + fmt_ars(abs(sobra)) if sobra < 0 else 'Disponible: ' + fmt_ars(sobra)}
                </div>
            </div>""", unsafe_allow_html=True)

        total_lim  = pres_df["Limite"].sum()
        total_gast_pres = sum(gastado_cat.get(c, 0) for c in pres_df["Categoria"])
        st.markdown(f"""
        <div class='card' style='margin-top:0.8rem'>
            <div class='card-label'>Total presupuestado</div>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-top:4px'>
                <div style='font-size:1.1rem;font-weight:700;color:#f87171'>{fmt_ars(total_gast_pres)} gastados</div>
                <div style='font-size:1.1rem;font-weight:700;color:#888'>de {fmt_ars(total_lim)}</div>
            </div>
        </div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6: TARJETAS (ABM)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[6]:
    tarjetas_df = load("tarjetas")

    # ── Agregar / editar tarjeta ─────────────────────────────────────────────
    with st.expander("➕ Agregar / editar tarjeta", expanded=tarjetas_df.empty):
        with st.form("f_tarjeta", clear_on_submit=True):
            st.markdown("<div style='font-weight:700;font-size:0.95rem;margin-bottom:0.7rem'>Nueva tarjeta</div>", unsafe_allow_html=True)
            t_nombre = st.text_input("Nombre", placeholder="Ej: Visa ICBC, Master Galicia…")
            col1, col2 = st.columns(2)
            t_cierre  = col1.number_input("Día de cierre", min_value=1, max_value=28, value=5)
            t_vence   = col2.number_input("Día de vencimiento", min_value=1, max_value=31, value=20)
            t_color   = st.selectbox("Color identificador", COLORES_TARJETA,
                                     format_func=lambda x: x)
            st.markdown(f"<div style='width:24px;height:24px;border-radius:50%;background:{t_color};margin-bottom:0.5rem'></div>", unsafe_allow_html=True)

            if st.form_submit_button("💾 Guardar tarjeta"):
                if not t_nombre.strip():
                    st.warning("Ingresá un nombre.")
                else:
                    tarjetas_df_new = tarjetas_df[tarjetas_df["Nombre"] != t_nombre.strip()].copy()
                    fila = pd.DataFrame([[t_nombre.strip(), t_cierre, t_vence, t_color]],
                                        columns=["Nombre","Dia cierre","Dia vencimiento","Color"])
                    tarjetas_df_new = pd.concat([tarjetas_df_new, fila], ignore_index=True)
                    save("tarjetas", tarjetas_df_new)
                    st.success(f"✅ {t_nombre} guardada")
                    st.rerun()

    # ── Lista de tarjetas ────────────────────────────────────────────────────
    tarjetas_df = load("tarjetas")
    st.markdown("<div class='sec-title'>Mis tarjetas</div>", unsafe_allow_html=True)

    if tarjetas_df.empty:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;padding:1.5rem'>No hay tarjetas configuradas. Agregá una arriba.</div>", unsafe_allow_html=True)
    else:
        for idx, t in tarjetas_df.iterrows():
            tname  = str(t.get("Nombre",""))
            color  = str(t.get("Color","#7c6af7"))
            dc     = safe_int(t.get("Dia cierre", 1), 1)
            dv     = safe_int(t.get("Dia vencimiento", 20), 20)

            # Calcular período actual y monto
            inicio_p, fin_p = get_periodo_tarjeta(tname, y, m)
            gf_t = filtrar_gastos_tarjeta_periodo(gastos_df, tname, y, m)
            total_t = gf_t["Monto"].sum() if not gf_t.empty else 0

            st.markdown(f"""
            <div class='tarjeta-card' style='border-left: 3px solid {color}'>
                <div style='width:12px;height:12px;border-radius:50%;background:{color};flex-shrink:0'></div>
                <div style='flex:1'>
                    <div class='tarjeta-nombre'>{tname}</div>
                    <div class='tarjeta-meta'>Cierra día {dc} · Vence día {dv}</div>
                    <div class='tarjeta-meta' style='color:#555'>Período: {inicio_p.strftime("%d/%m")} → {fin_p.strftime("%d/%m")}</div>
                </div>
                <div style='text-align:right'>
                    <div class='tarjeta-monto' style='color:{color}'>−{fmt_ars(total_t)}</div>
                    <div style='font-size:0.68rem;color:#555'>{nombre_mes}</div>
                </div>
            </div>""", unsafe_allow_html=True)

            # ── Vista por período ──────────────────────────────────────────
            with st.expander(f"📊 Ver períodos de {tname}"):

                # Selector de período (últimos 6 meses + próximo)
                periodos = []
                for delta in range(-5, 2):
                    pm = m + delta
                    py = y
                    while pm <= 0:
                        pm += 12
                        py -= 1
                    while pm > 12:
                        pm -= 12
                        py += 1
                    periodos.append((py, pm))

                opciones = [f"{calendar.month_name[pm].capitalize()} {py}" for py, pm in periodos]
                idx_actual = 5  # índice del mes actual en la lista
                sel_per = st.selectbox("Período", opciones,
                                       index=idx_actual,
                                       key=f"per_{tname}_{idx}")

                sel_py, sel_pm = periodos[opciones.index(sel_per)]
                inicio_sel, fin_sel = get_periodo_tarjeta(tname, sel_py, sel_pm)
                gf_sel = filtrar_gastos_tarjeta_periodo(gastos_df, tname, sel_py, sel_pm)
                total_sel = gf_sel["Monto"].sum() if not gf_sel.empty else 0

                # Info período
                hoy_d = date.today()
                es_futuro = (sel_py > y) or (sel_py == y and sel_pm > m)
                es_cerrado = fin_sel < hoy_d

                if es_futuro:
                    st.markdown(f"<div class='periodo-badge'>🔮 Período futuro · {inicio_sel.strftime('%d/%m')} → {fin_sel.strftime('%d/%m')}</div>", unsafe_allow_html=True)
                elif es_cerrado:
                    st.markdown(f"<div class='periodo-badge'>🔒 Cerrado · {inicio_sel.strftime('%d/%m')} → {fin_sel.strftime('%d/%m')} · Venció día {dv}</div>", unsafe_allow_html=True)
                else:
                    dias_restantes = (fin_sel - hoy_d).days
                    st.markdown(f"<div class='periodo-badge'>🟢 Abierto · Cierra en {dias_restantes} día{'s' if dias_restantes != 1 else ''} ({fin_sel.strftime('%d/%m')})</div>", unsafe_allow_html=True)

                # Total
                st.markdown(f"""
                <div style='display:flex;justify-content:space-between;align-items:center;
                    padding:0.8rem;background:#0f0f14;border-radius:12px;margin-bottom:0.6rem'>
                    <span style='font-size:0.8rem;color:#888;font-weight:600'>TOTAL PERÍODO</span>
                    <span style='font-size:1.3rem;font-weight:800;color:{color}'>−{fmt_ars(total_sel)}</span>
                </div>""", unsafe_allow_html=True)

                if gf_sel.empty:
                    st.markdown("<div style='color:#555;font-size:0.82rem;text-align:center;padding:0.8rem'>Sin gastos en este período.</div>", unsafe_allow_html=True)
                else:
                    gf_sel_sorted = gf_sel.sort_values("Fecha", ascending=False)
                    for _, gr in gf_sel_sorted.iterrows():
                        icono = emoji_cat(str(gr.get("Categoria","💳")))
                        fecha_str = str(gr.get("Fecha",""))[:10]
                        cuotas_v = safe_int(gr.get("Cuotas",1),1)
                        cuotas_t = f" · {cuotas_v}c" if cuotas_v > 1 else ""
                        st.markdown(f"""
                        <div class='tx-row'>
                            <div class='tx-icon' style='width:32px;height:32px;font-size:0.9rem'>{icono}</div>
                            <div style='flex:1;min-width:0'>
                                <div class='tx-concepto' style='font-size:0.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{gr.get('Concepto','—')}</div>
                                <div class='tx-meta'>{fecha_str}{cuotas_t}</div>
                            </div>
                            <div class='tx-monto red' style='font-size:0.88rem'>−{fmt_ars(gr.get('Monto',0))}</div>
                        </div>""", unsafe_allow_html=True)

            # Botón eliminar tarjeta
            col_del = st.columns([4,1])[1]
            with col_del:
                if st.button("✕", key=f"del_t_{idx}", help=f"Eliminar {tname}"):
                    tarjetas_df = tarjetas_df.drop(index=idx).reset_index(drop=True)
                    save("tarjetas", tarjetas_df)
                    st.rerun()

    # ── Info general ─────────────────────────────────────────────────────────
    st.markdown("""
    <div style='margin-top:1.2rem;padding:0.9rem;background:#1a1a24;border:1px solid #2a2a38;border-radius:14px'>
        <div style='font-size:0.72rem;font-weight:700;color:#666;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem'>¿Cómo funciona la lógica de períodos?</div>
        <div style='font-size:0.78rem;color:#888;line-height:1.6'>
            Si tu tarjeta cierra el día <strong style='color:#a78bfa'>5</strong>, un gasto del día 3 va al resumen de <strong style='color:#a78bfa'>este mes</strong>, pero un gasto del día 6 va al resumen del <strong style='color:#a78bfa'>mes siguiente</strong>.<br><br>
            En el home siempre ves el mes actual. Acá podés explorar períodos anteriores y futuros por tarjeta.
        </div>
    </div>""", unsafe_allow_html=True)
