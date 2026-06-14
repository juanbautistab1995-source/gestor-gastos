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
        pd.Series(s).astype(str).str.replace(r"[^\d\.\-]", "", regex=True),
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
        if pd.isna(val) or str(val).strip() in ("", "nan", "None", "N/A"):
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
        inicio = date(año, mes, 1)
        fin = date(año, mes, calendar.monthrange(año, mes)[1])
        return inicio, fin

    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    dia_cierre = min(safe_int(row.get("Dia cierre", 1), 1), 28)

    if mes == 1:
        mes_ant, año_ant = 12, año - 1
    else:
        mes_ant, año_ant = mes - 1, año

    ultimo_mes_ant = calendar.monthrange(año_ant, mes_ant)[1]
    dia_inicio = min(dia_cierre + 1, ultimo_mes_ant)
    inicio = date(año_ant, mes_ant, dia_inicio)
    fin = date(año, mes, min(dia_cierre, calendar.monthrange(año, mes)[1]))
    return inicio, fin

def periodo_actual_de_gasto(fecha_gasto_str, tarjeta_nombre):
    try:
        fg = pd.to_datetime(fecha_gasto_str).date()
    except:
        hoy = date.today()
        return hoy.year, hoy.month

    t_df = load("tarjetas")
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return fg.year, fg.month

    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    dia_cierre = min(safe_int(row.get("Dia cierre", 1), 1), 28)

    if fg.day <= dia_cierre:
        return fg.year, fg.month
    else:
        return (fg.year + 1, 1) if fg.month == 12 else (fg.year, fg.month + 1)

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

# ── Página Configuración ────────────────────────────────────────────────────────
st.set_page_config(page_title="💳 JuanB Gestor", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stApp {
    font-family: 'Inter', sans-serif !important;
    background-color: #0f0f14 !important;
    color: #e8e8f0 !important;
}

#MainMenu, header, footer { visibility: hidden; }
.block-container { padding: 1rem 0.8rem 5rem !important; max-width: 480px !important; margin: auto; }

/* Menú principal de Tabs */
[data-baseweb="tab-list"] { background: #1a1a24 !important; border-radius: 14px !important; padding: 4px !important; gap: 2px !important; border: 1px solid #2a2a38 !important; }
[data-baseweb="tab"] { border-radius: 10px !important; font-size: 0.72rem !important; font-weight: 600 !important; padding: 6px 8px !important; color: #888 !important; }
[aria-selected="true"][data-baseweb="tab"] { background: #7c6af7 !important; color: #fff !important; }

/* Tarjetas Visuales */
.card { background: #1a1a24; border: 1px solid #2a2a38; border-radius: 16px; padding: 1rem 1.1rem; margin-bottom: 0.6rem; }
.card-label { font-size: 0.72rem; font-weight: 600; color: #888; text-transform: uppercase; margin-bottom: 2px; }
.card-value { font-size: 1.9rem; font-weight: 800; line-height: 1.1; }
.green { color: #4ade80; } .red { color: #f87171; } .purple { color: #a78bfa; } .yellow { color: #fbbf24; }

.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; margin-bottom: 0.6rem; }
.card-sm { background: #1a1a24; border: 1px solid #2a2a38; border-radius: 14px; padding: 0.8rem 0.9rem; }
.card-sm .card-value { font-size: 1.3rem; font-weight: 700; }

.tarjeta-card { background: #1a1a24; border: 1px solid #2a2a38; border-radius: 14px; padding: 0.85rem 1rem; margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.8rem; }
.tarjeta-nombre { font-size: 0.88rem; font-weight: 700; color: #e8e8f0; }
.tarjeta-monto { margin-left: auto; font-size: 1.05rem; font-weight: 800; text-align: right; }

/* Listado de movimientos */
.tx-row { display: flex; align-items: center; gap: 0.7rem; padding: 0.7rem 0; border-bottom: 1px solid #1e1e2c; }
.tx-icon { width: 38px; height: 38px; background: #22223a; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; flex-shrink: 0; }
.tx-concepto { font-size: 0.87rem; font-weight: 600; color: #e8e8f0; }
.tx-meta { font-size: 0.72rem; color: #666; }
.tx-monto { margin-left: auto; font-size: 0.95rem; font-weight: 700; text-align: right; }

/* Formularios e inputs */
.stTextInput input, .stNumberInput input, .stDateInput input, .stSelectbox div[data-baseweb="select"] > div, .stTextArea textarea { background: #1e1e2c !important; border: 1px solid #2e2e44 !important; border-radius: 10px !important; color: #e8e8f0 !important; font-size: 0.9rem !important; }
label[data-testid="stWidgetLabel"] p { font-size: 0.75rem !important; font-weight: 600 !important; color: #888 !important; text-transform: uppercase; }

/* ── FIX BOTONES BLANCOS ── */
div[data-testid="stButton"] > button, div[data-testid="stFormSubmitButton"] > button {
    background-color: #7c6af7 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    transition: all 0.2s ease !important;
}
div[data-testid="stButton"] > button:hover, div[data-testid="stFormSubmitButton"] > button:hover {
    background-color: #6b59e6 !important;
    color: #ffffff !important;
}

.sec-title { font-size: 0.72rem; font-weight: 700; color: #666; text-transform: uppercase; letter-spacing: 0.1em; margin: 1.1rem 0 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "gasto_limit" not in st.session_state: st.session_state.gasto_limit = 25
if "menu_accion" not in st.session_state: st.session_state.menu_accion = False
if "tipo_accion" not in st.session_state: st.session_state.tipo_accion = "Gasto"

# ── Cargar datos ───────────────────────────────────────────────────────────────
gastos_df   = load("gastos")
ingresos_df = load("ingresos")
comp_df     = load("compartidos")
inv_df      = load("inversiones")
pres_df     = load("presupuesto")
tarjetas_df = load("tarjetas")

# Asegurar tipos numéricos para no tener errores de sumas
for df_target, col in [(gastos_df, "Monto"), (gastos_df, "Cuanto recupero"), (ingresos_df, "Monto"), (comp_df, "Monto")]:
    df_target[col] = to_num(df_target[col])

y, m = mes_actual()
nombre_mes = calendar.month_name[m].capitalize()

gastos_mes   = filtrar_mes(gastos_df, y, m)
ingresos_mes = filtrar_mes(ingresos_df, y, m)

total_ing   = ingresos_mes["Monto"].sum()
total_gast  = gastos_mes["Monto"].sum()
recupero    = gastos_mes["Cuanto recupero"].sum()
remanente   = total_ing - total_gast + recupero

TARJETAS = get_tarjetas_nombres()

# ══════════════════════════════════════════════════════════════════════════════
# MENÚ FLOTANTE DE ACCIÓN RÁPIDA
# ══════════════════════════════════════════════════════════════════════════════
col_espacio, col_boton = st.columns([3, 1])
with col_boton:
    if st.button("➕ Acciones", use_container_width=True):
        st.session_state.menu_accion = not st.session_state.menu_accion

if st.session_state.menu_accion:
    st.markdown("<div style='background:#1a1a24; border: 1px solid #7c6af7; border-radius: 12px; padding: 1rem; margin-bottom: 1rem;'>", unsafe_allow_html=True)
    
    st.session_state.tipo_accion = st.radio("¿Qué querés agregar?", ["Gasto", "Ingreso", "Tarjeta"], horizontal=True, label_visibility="collapsed")
    
    if st.session_state.tipo_accion == "Gasto":
        with st.form("f_quick_gasto", clear_on_submit=True):
            q_concepto = st.text_input("Concepto")
            q_monto    = st.number_input("Monto $", min_value=0.0, step=100.0)
            c1, c2 = st.columns(2)
            q_tarjeta  = c1.selectbox("Tarjeta", TARJETAS)
            q_cat      = c2.selectbox("Categoría", CAT_GASTOS)
            c3, c4 = st.columns(2)
            q_fecha    = c3.date_input("Fecha", value=date.today())
            q_cuotas   = c4.number_input("Cuotas", min_value=1, max_value=48, value=1)

            col_guardar, col_cerrar = st.columns([3, 1])
            if col_guardar.form_submit_button("💾 Guardar Gasto", use_container_width=True):
                if q_concepto.strip() and q_monto > 0:
                    nuevo = pd.DataFrame([[str(q_fecha), q_concepto.strip(), q_monto, q_tarjeta, q_cuotas, q_cat, "No", "", 0, ""]], columns=gastos_df.columns)
                    gastos_df = pd.concat([gastos_df, nuevo], ignore_index=True)
                    save("gastos", gastos_df)
                    st.session_state.menu_accion = False
                    st.rerun()
            if col_cerrar.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()

    elif st.session_state.tipo_accion == "Ingreso":
        with st.form("f_quick_ingreso", clear_on_submit=True):
            i_concepto = st.text_input("Concepto (Ej: Sueldo, Venta)")
            i_monto    = st.number_input("Monto $", min_value=0.0, step=1000.0)
            c1, c2 = st.columns(2)
            i_cat      = c1.selectbox("Categoría", CAT_ING)
            i_fecha    = c2.date_input("Fecha", value=date.today())
            
            col_guardar, col_cerrar = st.columns([3, 1])
            if col_guardar.form_submit_button("💰 Guardar Ingreso", use_container_width=True):
                if i_concepto.strip() and i_monto > 0:
                    nuevo = pd.DataFrame([[str(i_fecha), i_concepto.strip(), i_monto, i_cat]], columns=ingresos_df.columns)
                    ingresos_df = pd.concat([ingresos_df, nuevo], ignore_index=True)
                    save("ingresos", ingresos_df)
                    st.session_state.menu_accion = False
                    st.rerun()
            if col_cerrar.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()

    elif st.session_state.tipo_accion == "Tarjeta":
        with st.form("f_quick_tarjeta", clear_on_submit=True):
            t_nombre = st.text_input("Nombre de Tarjeta")
            c1, c2 = st.columns(2)
            t_cierre = c1.number_input("Día de cierre", min_value=1, max_value=28, value=5)
            t_vence  = c2.number_input("Día de vencimiento", min_value=1, max_value=31, value=20)
            t_color  = st.selectbox("Color", COLORES_TARJETA)
            
            col_guardar, col_cerrar = st.columns([3, 1])
            if col_guardar.form_submit_button("💳 Crear Tarjeta", use_container_width=True):
                if t_nombre.strip():
                    tarj_new = tarjetas_df[tarjetas_df["Nombre"] != t_nombre.strip()].copy()
                    fila = pd.DataFrame([[t_nombre.strip(), t_cierre, t_vence, t_color]], columns=["Nombre","Dia cierre","Dia vencimiento","Color"])
                    tarjetas_df = pd.concat([tarj_new, fila], ignore_index=True)
                    save("tarjetas", tarjetas_df)
                    st.session_state.menu_accion = False
                    st.rerun()
            if col_cerrar.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()
                
    st.markdown("</div>", unsafe_allow_html=True)


# ── Tabs Principales ───────────────────────────────────────────────────────────
tabs = st.tabs(["🏠 Home", "➕ Gastos", "💳 Tarjetas", "💰 Ingresos", "🤝 Compart.", "📈 Inv.", "🎯 Pres."])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 0: HOME
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    st.markdown(f"<div style='font-size:1.05rem;font-weight:700;color:#888;margin-bottom:0.8rem'>📅 {nombre_mes} {y}</div>", unsafe_allow_html=True)
    color_rem = "green" if remanente >= 0 else "red"
    st.markdown(f"<div class='card'><div class='card-label'>Remanente del mes</div><div class='card-value {color_rem}'>{fmt_ars(remanente)}</div><div class='card-sub'>Ingresos − Gastos + Lo que recuperás</div></div>", unsafe_allow_html=True)
    
    st.markdown(f"<div class='grid2'><div class='card-sm'><div class='card-label'>Ingresos</div><div class='card-value green'>{fmt_ars(total_ing)}</div></div><div class='card-sm'><div class='card-label'>Gastos</div><div class='card-value red'>{fmt_ars(total_gast)}</div></div></div>", unsafe_allow_html=True)

    st.markdown("<div class='sec-title'>Total Acumulado por Tarjeta</div>", unsafe_allow_html=True)
    tarjetas_con_gasto = {}
    for tname in TARJETAS:
        gf = gastos_df[gastos_df["Tarjeta"] == tname]
        total_t = gf["Monto"].sum() if not gf.empty else 0
        if total_t > 0:
            tarjetas_con_gasto[tname] = total_t

    if tarjetas_con_gasto:
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = "#7c6af7"
            if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
                color = str(tarjetas_df[tarjetas_df["Nombre"] == tname].iloc[0].get("Color", color))
            
            st.markdown(f"""
            <div class='tarjeta-card'>
                <div style='width:10px;height:10px;border-radius:50%;background:{color}'></div>
                <div style='flex:1'><div class='tarjeta-nombre'>{tname}</div></div>
                <div class='tarjeta-monto' style='color:{color}'>−{fmt_ars(total_t)}</div>
            </div>""", unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#555;font-size:0.85rem;text-align:center;'>Sin gastos cargados.</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: GASTOS (Vista Optimizada + Carga Masiva)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    
    # ── CARGA MASIVA CSV ──
    with st.expander("📥 Carga Masiva (Pegar texto CSV / TXT)"):
        st.info("Pegá acá el texto CSV que yo te arme desde el chat. El sistema acomoda automáticamente las columnas y te guarda todo de un saque.")
        csv_text = st.text_area("Texto CSV:", height=150)
        
        if st.button("🚀 Procesar e Importar Gastos", use_container_width=True):
            if csv_text.strip():
                try:
                    nuevos_datos = pd.read_csv(io.StringIO(csv_text.strip()))
                    
                    # Rellenar las columnas que exige la base por si faltan en el CSV
                    for col in gastos_df.columns:
                        if col not in nuevos_datos.columns:
                            if col in ["Monto", "Cuanto recupero"]:
                                nuevos_datos[col] = 0
                            elif col == "Compartido":
                                nuevos_datos[col] = "No"
                            else:
                                nuevos_datos[col] = ""
                    
                    # Filtramos y ordenamos según la base principal
                    nuevos_datos = nuevos_datos[gastos_df.columns]
                    
                    # Añadir al dataframe original y guardar
                    gastos_df = pd.concat([gastos_df, nuevos_datos], ignore_index=True)
                    save("gastos", gastos_df)
                    st.success(f"¡Éxito! Se agregaron {len(nuevos_datos)} movimientos a la base de datos.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Hubo un error al leer el texto. Asegurate de que sea el formato que te pasé. Detalle: {e}")
            else:
                st.warning("¡Primero pegá el texto antes de procesar!")

    st.divider()

    # ── LISTADO DE MOVIMIENTOS ──
    st.markdown("<div class='sec-title'>Historial de Movimientos</div>", unsafe_allow_html=True)
    df_show = gastos_df.sort_values("Fecha", ascending=False)
    
    if df_show.empty:
        st.markdown("<div style='color:#555;text-align:center;padding:1.5rem'>Aún no hay gastos registrados.</div>", unsafe_allow_html=True)
    else:
        df_limit = df_show.head(st.session_state.gasto_limit)
        for idx, r in df_limit.iterrows():
            icono = emoji_cat(str(r.get("Categoria","💳")))
            fecha_str = str(r.get("Fecha",""))[:10]
            st.markdown(f"""
            <div class='tx-row'>
                <div class='tx-icon'>{icono}</div>
                <div style='flex:1;min-width:0'>
                    <div class='tx-concepto' style='white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{r.get('Concepto','—')}</div>
                    <div class='tx-meta'>{fecha_str} · {r.get('Tarjeta','')}</div>
                </div>
                <div class='tx-monto red'>−{fmt_ars(r.get('Monto',0))}</div>
            </div>""", unsafe_allow_html=True)

        if len(df_show) > st.session_state.gasto_limit:
            if st.button("Cargar más movimientos ▼", use_container_width=True):
                st.session_state.gasto_limit += 25
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: TARJETAS (Editor y ABM completo)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown("<div class='sec-title'>Modificar y Configurar Mis Tarjetas</div>", unsafe_allow_html=True)
    st.info("Podés editar el nombre, fechas y color directamente en la tabla. Para eliminar una tarjeta, seleccioná la fila y apretá borrar en tu teclado.")
    
    if not tarjetas_df.empty:
        edited_tarjetas = st.data_editor(
            tarjetas_df,
            num_rows="dynamic",
            use_container_width=True
        )
        if st.button("💾 Guardar Configuración de Tarjetas", use_container_width=True):
            save("tarjetas", edited_tarjetas)
            st.success("¡Tarjetas actualizadas correctamente!")
            st.rerun()
    else:
        st.warning("No tenés tarjetas creadas. Usá el botón '➕ Acciones' de arriba.")

    st.divider()

    st.markdown("<div class='sec-title'>Modificar Gastos de Tarjeta en un Período</div>", unsafe_allow_html=True)
    col_sel1, col_sel2 = st.columns(2)
    t_sel = col_sel1.selectbox("Elegí Tarjeta a editar", TARJETAS)
    
    periodos = []
    for delta in range(-5, 2):
        pm, py = m + delta, y
        while pm <= 0: pm += 12; py -= 1
        while pm > 12: pm -= 12; py += 1
        periodos.append((py, pm))
    
    opciones = [f"{calendar.month_name[pm].capitalize()} {py}" for py, pm in periodos]
    per_sel = col_sel2.selectbox("Período a editar", opciones, index=5)
    
    sel_py, sel_pm = periodos[opciones.index(per_sel)]
    df_filtrado_editor = filtrar_gastos_tarjeta_periodo(gastos_df, t_sel, sel_py, sel_pm)

    if not df_filtrado_editor.empty:
        total_periodo_sel = df_filtrado_editor["Monto"].sum()
        
        st.markdown(f"""
        <div style='background: #2a1010; border: 1px solid #f87171; border-radius: 10px; padding: 15px; margin-bottom: 15px; text-align: center;'>
            <div style='color: #f87171; font-size: 0.85rem; font-weight: bold; text-transform: uppercase;'>Total acumulado en {t_sel} ({per_sel})</div>
            <div style='color: #fff; font-size: 1.8rem; font-weight: 800;'>{fmt_ars(total_periodo_sel)}</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.caption("Doble clic en la celda para editar el gasto. Al guardar pisa la base de datos.")
        
        df_filtrado_editor['idx_original'] = df_filtrado_editor.index
        
        edited_df = st.data_editor(
            df_filtrado_editor.drop(columns=['idx_original']),
            column_config={"Monto": st.column_config.NumberColumn("Monto", format="$%d")},
            num_rows="dynamic",
            use_container_width=True
        )

        if st.button("💾 Guardar Cambios en Gastos de Tarjeta", use_container_width=True):
            for original_idx in df_filtrado_editor['idx_original']:
                gastos_df = gastos_df.drop(index=original_idx)
            
            gastos_df = pd.concat([gastos_df, edited_df], ignore_index=True)
            save("gastos", gastos_df)
            st.success("¡Gastos actualizados y base guardada!")
            st.rerun()
    else:
        st.markdown("<div style='color:#555;font-size:0.85rem;'>No hay gastos para esta tarjeta en este período.</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TABS RESTANTES (Sin cambios estructurales profundos)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]: # Ingresos
    st.info("Mantené tus ingresos acá para calcular el remanente en Home.")

with tabs[4]: # Compartidos
    st.info("Gestioná quién te debe plata acá.")

with tabs[5]: # Inversiones
    st.info("Control de tus ahorros e inversiones.")

with tabs[6]: # Presupuesto
    st.info("Límites mensuales para no pasarte.")
