import streamlit as st
import pandas as pd
import os
from datetime import date

# ── Archivos de datos ──────────────────────────────────────────────────────────
DATA_FILE     = "mis_gastos.csv"
INGRESOS_FILE = "mis_ingresos.csv"

GASTOS_COLS   = ["Tarjeta", "Fecha", "Concepto", "Monto", "Cuotas",
                 "Categoria", "Compartido", "Con quien", "Cuanto recupero"]
INGRESOS_COLS = ["Fecha", "Concepto", "Monto"]

TARJETAS      = ["Visa ICBC", "Visa Hipotecario", "Master ICBC", "Efectivo", "Otro"]
CATEGORIAS    = ["Comida", "Transporte", "Entretenimiento", "Viaje con lauti",
                 "Salud", "Ropa", "Servicios", "Otro"]


# ── Helpers ────────────────────────────────────────────────────────────────────
def load_df(file, cols):
    if os.path.exists(file):
        return pd.read_csv(file)
    return pd.DataFrame(columns=cols)

def to_num(series):
    return pd.to_numeric(
        series.astype(str).str.replace(r"[\$,]", "", regex=True),
        errors="coerce"
    ).fillna(0)

def fix_gastos(df):
    """Normaliza tipos para evitar errores en data_editor."""
    if df.empty:
        return df
    df["Monto"]           = to_num(df["Monto"])
    df["Cuanto recupero"] = to_num(df["Cuanto recupero"])
    df["Cuotas"]          = pd.to_numeric(df["Cuotas"], errors="coerce").fillna(1).astype(int)
    # Compartido → string "Sí" / "No"  (no booleano, evita el crash de CheckboxColumn)
    def norm_comp(v):
        v = str(v).strip().lower()
        return "Sí" if v in ("sí", "si", "true", "1", "yes") else "No"
    df["Compartido"] = df["Compartido"].apply(norm_comp)
    df["Con quien"]  = df["Con quien"].fillna("").astype(str)
    return df


# ── Config página ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Finanzas JuanB", page_icon="💳", layout="wide")

st.markdown("""
<style>
    /* Tipografía y colores generales */
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

    /* Tarjetas de métricas */
    [data-testid="metric-container"] {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 12px;
        padding: 1rem 1.2rem;
    }
    [data-testid="metric-container"] label { color: #a6adc8 !important; font-size: .8rem; }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        color: #cdd6f4 !important; font-size: 1.6rem; font-weight: 700;
    }
    [data-testid="metric-container"] [data-testid="stMetricDelta"] { font-size: .85rem; }

    /* Botón primario */
    .stButton > button[kind="primary"] {
        background: #89b4fa; color: #1e1e2e;
        border: none; border-radius: 8px;
        font-weight: 600; padding: .45rem 1.2rem;
    }
    .stButton > button[kind="primary"]:hover { background: #b4d0ff; }

    /* Header de sección */
    h3 { color: #cdd6f4 !important; margin-bottom: .3rem; }

    /* Fila seleccionada en tabla */
    .stDataFrame tr:hover td { background: #313244 !important; }
</style>
""", unsafe_allow_html=True)

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("💳 Mi Gestor Diario")
st.caption("Hola **Juan Bautista** — registrá, editá y analizá tus movimientos.")
st.divider()

# ── Carga inicial de datos ─────────────────────────────────────────────────────
gastos_df   = fix_gastos(load_df(DATA_FILE,     GASTOS_COLS))
ingresos_df = load_df(INGRESOS_FILE, INGRESOS_COLS)
ingresos_df["Monto"] = to_num(ingresos_df["Monto"])

# ══════════════════════════════════════════════════════════════════════════════
# 1 · RESUMEN FINANCIERO
# ══════════════════════════════════════════════════════════════════════════════
total_ing  = ingresos_df["Monto"].sum()
total_gast = gastos_df["Monto"].sum()
recupero   = gastos_df["Cuanto recupero"].sum()
balance    = total_ing - total_gast + recupero

st.subheader("📈 Resumen")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Ingresos",        f"${total_ing:,.0f}")
c2.metric("Gastos totales",  f"${total_gast:,.0f}", delta=f"-${total_gast:,.0f}", delta_color="inverse")
c3.metric("A recuperar",     f"${recupero:,.0f}")
c4.metric("Balance neto",    f"${balance:,.0f}",
          delta="positivo" if balance >= 0 else "negativo",
          delta_color="normal" if balance >= 0 else "inverse")
st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# 2 · AGREGAR GASTO RÁPIDO  (siempre visible arriba)
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("➕ Nuevo Gasto")
with st.form("nuevo_gasto", clear_on_submit=True):
    col1, col2, col3 = st.columns([1.5, 2, 1])
    tarjeta   = col1.selectbox("Tarjeta",    TARJETAS)
    concepto  = col2.text_input("Concepto",  placeholder="Ej: sushi, nafta...")
    fecha     = col3.date_input("Fecha",     value=date.today())

    col4, col5, col6 = st.columns(3)
    monto     = col4.number_input("Monto ($)",  min_value=0.0, step=100.0)
    cuotas    = col5.number_input("Cuotas",     min_value=1, max_value=48, value=1)
    categoria = col6.selectbox("Categoría",  CATEGORIAS)

    col7, col8, col9 = st.columns(3)
    compartido = col7.selectbox("¿Compartido?", ["No", "Sí"])
    con_quien  = col8.text_input("¿Con quién?", placeholder="Ej: Lauti")
    recupero_v = col9.number_input("A recuperar ($)", min_value=0.0, step=100.0)

    submitted = st.form_submit_button("💾 Guardar gasto", type="primary", use_container_width=True)
    if submitted:
        if not concepto.strip():
            st.warning("Poné un concepto.")
        elif monto <= 0:
            st.warning("El monto tiene que ser mayor a cero.")
        else:
            nuevo = pd.DataFrame([[
                tarjeta, str(fecha), concepto.strip(), monto,
                cuotas, categoria, compartido, con_quien, recupero_v
            ]], columns=GASTOS_COLS)
            gastos_df = fix_gastos(pd.concat([gastos_df, nuevo], ignore_index=True))
            gastos_df.to_csv(DATA_FILE, index=False)
            st.success(f"✅ '{concepto}' anotado por ${monto:,.0f}")
            st.rerun()

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# 3 · TABLA DE GASTOS + ELIMINACIÓN
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("📋 Mis Gastos")

# Filtros rápidos en línea
f1, f2, f3 = st.columns(3)
tarj_filter = f1.selectbox("Filtrar tarjeta",    ["Todas"] + TARJETAS,    key="ft")
cat_filter  = f2.selectbox("Filtrar categoría",  ["Todas"] + CATEGORIAS,  key="fc")
comp_filter = f3.selectbox("¿Compartido?",       ["Todos", "Sí", "No"],   key="fco")

df_view = gastos_df.copy()
if tarj_filter != "Todas":
    df_view = df_view[df_view["Tarjeta"] == tarj_filter]
if cat_filter != "Todas":
    df_view = df_view[df_view["Categoria"] == cat_filter]
if comp_filter != "Todos":
    df_view = df_view[df_view["Compartido"] == comp_filter]

# Tabla editable (sin CheckboxColumn para "Compartido" → se usa SelectboxColumn)
edited_gastos = st.data_editor(
    df_view.reset_index(drop=True),
    column_config={
        "Tarjeta":        st.column_config.SelectboxColumn("Tarjeta",      options=TARJETAS,   width="medium"),
        "Categoria":      st.column_config.SelectboxColumn("Categoría",    options=CATEGORIAS, width="medium"),
        "Compartido":     st.column_config.SelectboxColumn("Compartido",   options=["No", "Sí"], width="small"),
        "Monto":          st.column_config.NumberColumn("Monto ($)",       format="$%d",       width="small"),
        "Cuotas":         st.column_config.NumberColumn("Cuotas",          min_value=1, max_value=48, width="small"),
        "Cuanto recupero":st.column_config.NumberColumn("A recuperar ($)", format="$%d",       width="small"),
        "Fecha":          st.column_config.TextColumn("Fecha",             width="small"),
        "Concepto":       st.column_config.TextColumn("Concepto",          width="large"),
        "Con quien":      st.column_config.TextColumn("Con quién",         width="medium"),
    },
    num_rows="dynamic",
    use_container_width=True,
    height=420,
    key="tabla_gastos",
)

b1, b2 = st.columns([1, 5])
if b1.button("💾 Guardar cambios", type="primary"):
    fixed = fix_gastos(edited_gastos.copy())
    fixed.to_csv(DATA_FILE, index=False)
    st.success("Cambios guardados.")
    st.rerun()

# Subtotal filtrado
sub = to_num(edited_gastos["Monto"]).sum()
st.caption(f"**Subtotal mostrado:** ${sub:,.0f} en {len(edited_gastos)} registros")

# Eliminar filas por número (más seguro que fila interactiva en mobile)
with st.expander("🗑️ Eliminar gastos"):
    st.markdown("Ingresá los **números de fila** (columna más a la izquierda) separados por coma:")
    idx_input = st.text_input("Filas a eliminar", placeholder="Ej: 0, 3, 7")
    if st.button("Eliminar seleccionados", type="primary"):
        try:
            idxs = [int(x.strip()) for x in idx_input.split(",") if x.strip()]
            gastos_df = fix_gastos(
                gastos_df.drop(index=idxs).reset_index(drop=True)
            )
            gastos_df.to_csv(DATA_FILE, index=False)
            st.success(f"Filas {idxs} eliminadas.")
            st.rerun()
        except Exception:
            st.error("Revisá que los números sean válidos.")

st.divider()


# ══════════════════════════════════════════════════════════════════════════════
# 4 · INGRESOS
# ══════════════════════════════════════════════════════════════════════════════
st.subheader("💰 Ingresos")

with st.form("nuevo_ingreso", clear_on_submit=True):
    ic1, ic2, ic3 = st.columns([2, 1, 1])
    i_concepto = ic1.text_input("Concepto", placeholder="Ej: sueldo, freelance...")
    i_monto    = ic2.number_input("Monto ($)", min_value=0.0, step=100.0)
    i_fecha    = ic3.date_input("Fecha", value=date.today())
    if st.form_submit_button("➕ Agregar ingreso", type="primary"):
        if i_monto > 0 and i_concepto.strip():
            nuevo_i = pd.DataFrame([[str(i_fecha), i_concepto.strip(), i_monto]], columns=INGRESOS_COLS)
            ingresos_df = pd.concat([ingresos_df, nuevo_i], ignore_index=True)
            ingresos_df["Monto"] = to_num(ingresos_df["Monto"])
            ingresos_df.to_csv(INGRESOS_FILE, index=False)
            st.success("Ingreso registrado.")
            st.rerun()

edited_ingresos = st.data_editor(
    ingresos_df,
    column_config={
        "Monto": st.column_config.NumberColumn("Monto ($)", format="$%d"),
        "Fecha": st.column_config.TextColumn("Fecha"),
    },
    num_rows="dynamic",
    use_container_width=True,
    height=250,
)
if st.button("💾 Guardar ingresos", type="primary"):
    edited_ingresos["Monto"] = to_num(edited_ingresos["Monto"])
    edited_ingresos.to_csv(INGRESOS_FILE, index=False)
    st.success("Ingresos guardados.")
    st.rerun()
