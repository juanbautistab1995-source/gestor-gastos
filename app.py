import streamlit as st
import pandas as pd
import os
from datetime import date, datetime, timedelta
import calendar
import io
import requests
import re

# Configuración de Archivos y Esquemas de Datos
FILES = {
    "gastos": ("mis_gastos.csv", ["Fecha", "Concepto", "Monto", "Tarjeta", "Categoria", "Cuotas", "Compartido", "Con quien", "Cuanto recupero", "Notas"]),
    "ingresos": ("mis_ingresos.csv", ["Fecha", "Concepto", "Monto", "Categoria"]),
    "compartidos": ("mis_compartidos.csv", ["Fecha", "Concepto", "Monto", "Con quien", "Estado"]),
    "inversiones": ("mis_inversiones.csv", ["Fecha", "Instrumento", "Capital", "Rendimiento", "Moneda", "Nota"]),
    "presupuesto": ("mis_presupuesto.csv", ["Categoria", "Limite"]),
    "tarjetas": ("mis_tarjetas.csv", ["Nombre", "Dia cierre", "Dia vencimiento", "Color", "Cierre anterior", "Proximo cierre", "Dias entre cierres"])
}

CAT_GASTOS = ["Comida", "Transporte", "Ropa", "Servicios", "Salidas", "Viaje", "Salud", "Casa", "Farmacia", "Educación", "Regalo", "Otro"]
CAT_ING = ["Sueldo", "Freelance", "Inversión", "Otro"]
MONEDAS = ["ARS", "USD", "EUR"]
COLORES_TARJETA = ["#7c6af7", "#4ade80", "#f87171", "#fbbf24", "#60a5fa", "#f472b6"]
TARJETAS_DEFAULT = ["Visa ICBC", "Visa Hipotecario", "Master ICBC", "Efectivo", "Débito"]

MESES_ES = {
    "ene": "01", "feb": "02", "mar": "03", "abr": "04", "may": "05", "jun": "06",
    "jul": "07", "ago": "08", "sep": "09", "oct": "10", "nov": "11", "dic": "12"
}

VALORES_NULOS_LITERALES = {"none", "nan", "nat", "<na>", "null"}

def _parsear_fecha_es(s):
    s = str(s).strip()
    if not s or s.lower() in ("nat", "nan", "none", "s/f", "", "pd.nat"):
        return None
    s_lower = s.lower()
    for mes_es, mes_num in MESES_ES.items():
        if f"-{mes_es}-" in s_lower:
            try:
                partes = s_lower.split("-")
                dia = partes[0].zfill(2)
                anio = partes[2][:4]
                return f"{anio}-{mes_num}-{dia}"
            except:
                pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except:
            pass
    try:
        r = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isnull(r):
            return None
        return r.strftime("%Y-%m-%d")
    except:
        return None

def fmt_fecha(d):
    try:
        if pd.isnull(d):
            return str(date.today())
    except (TypeError, ValueError):
        pass
    if d is None:
        return str(date.today())
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    parsed = _parsear_fecha_es(d)
    return parsed if parsed else str(date.today())

def normalizar_fecha_existente(s):
    parsed = _parsear_fecha_es(s)
    return parsed if parsed else ""

def limpiar_nulos_literales(df):
    for col in df.columns:
        if df[col].dtype != object:
            continue
        mask = df[col].astype(str).str.strip().str.lower().isin(VALORES_NULOS_LITERALES)
        if mask.any():
            df.loc[mask, col] = ""
    return df

def load(key):
    f, cols = FILES[key]
    if os.path.exists(f):
        df = pd.read_csv(f, dtype=str).fillna("")
        df = limpiar_nulos_literales(df)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df = df[cols]
        if "Fecha" in df.columns:
            df["Fecha"] = df["Fecha"].apply(normalizar_fecha_existente)
        return df
    return pd.DataFrame(columns=cols)

def save(key, df):
    f, _ = FILES[key]
    df = limpiar_nulos_literales(df.copy())
    df.to_csv(f, index=False)

def to_num(series):
    return pd.to_numeric(
        pd.Series(series).astype(str).str.replace(r"[^\d\.\-]", "", regex=True),
        errors="coerce"
    ).fillna(0)

def mes_actual():
    hoy = date.today()
    return hoy.year, hoy.month

def filtrar_mes(df, y, m):
    if df.empty or "Fecha" not in df.columns:
        return df
    fechas = pd.to_datetime(df["Fecha"], errors="coerce")
    return df[(fechas.dt.year == y) & (fechas.dt.month == m)].copy()

def fmt_ars(n):
    try:
        return f"${float(n):,.0f}".replace(",", ".")
    except:
        return "$0"

def emoji_cat(cat):
    s = str(cat)
    return s.split(" ")[0] if " " in s else ""

def safe_int(val, default=1):
    try:
        v = str(val).strip()
        if v in ("", "nan", "None", "N/A", "none"):
            return default
        return int(float(v))
    except:
        return default

def parsear_cuotas(val):
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "n/a", ""):
        return 1, 1
    m = re.search(r'(\d+)\s*/\s*(\d+)', s)
    if m:
        actual = int(m.group(1))
        total = int(m.group(2))
        if total < 1: total = 1
        if actual < 1: actual = 1
        if actual > total: actual = total
        return actual, total
    try:
        n = int(float(s))
        if n < 1: n = 1
        return 1, n
    except:
        return 1, 1

def fmt_cuotas(actual, total):
    if total <= 1:
        return "1"
    return f"Cuota {actual}/{total}"

def get_tarjetas_nombres():
    nombres = list(TARJETAS_DEFAULT)
    t_df = load("tarjetas")
    if not t_df.empty:
        for n in t_df["Nombre"].dropna().tolist():
            n = str(n).strip()
            if n and n not in nombres:
                nombres.append(n)
    g_df = load("gastos")
    if not g_df.empty and "Tarjeta" in g_df.columns:
        for t in g_df["Tarjeta"].dropna().unique():
            t = str(t).strip()
            if t and t not in ("nan", "None", "") and t not in nombres:
                nombres.append(t)
    return nombres

def _generar_fechas_cierre(tarjeta_row, rango_dias=400):
    proximo_raw = str(tarjeta_row.get("Proximo cierre", "")).strip()
    if not proximo_raw or proximo_raw.lower() in ("nan", "none", "s/f", ""):
        return None
    try:
        proximo = datetime.strptime(proximo_raw[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        try:
            proximo = pd.to_datetime(proximo_raw, dayfirst=True).date()
        except Exception:
            return None
    anterior_raw = str(tarjeta_row.get("Cierre anterior", "")).strip()
    anterior = None
    if anterior_raw and anterior_raw.lower() not in ("nan", "none", "s/f", ""):
        try:
            anterior = datetime.strptime(anterior_raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            try:
                anterior = pd.to_datetime(anterior_raw, dayfirst=True).date()
            except Exception:
                anterior = None
    if anterior and anterior < proximo:
        intervalo = (proximo - anterior).days
    else:
        intervalo = safe_int(tarjeta_row.get("Dias entre cierres", 31), 31)
    if intervalo <= 0:
        intervalo = 31
    hoy = date.today()
    fechas = [proximo]
    if anterior:
        fechas.append(anterior)
    f = anterior if anterior else proximo
    while f > hoy - timedelta(days=rango_dias):
        f = f - timedelta(days=intervalo)
        fechas.append(f)
    f = proximo
    while f < hoy + timedelta(days=rango_dias):
        f = f + timedelta(days=intervalo)
        fechas.append(f)
    return sorted(set(fechas))

def get_periodo_tarjeta(tarjeta_nombre, año=None, mes=None):
    t_df = load("tarjetas")
    hoy = date.today()
    if año is None: año = hoy.year
    if mes is None: mes = hoy.month
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return date(año, mes, 1), date(año, mes, calendar.monthrange(año, mes)[1])
    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    fechas_cierre = _generar_fechas_cierre(row)
    if fechas_cierre:
        objetivo = date(año, mes, min(28, calendar.monthrange(año, mes)[1]))
        candidatas = [f for f in fechas_cierre if f.year == año and f.month == mes]
        if candidatas:
            fin = max(candidatas)
        else:
            posteriores = [f for f in fechas_cierre if f >= objetivo]
            fin = min(posteriores) if posteriores else max(fechas_cierre)
        anteriores = [f for f in fechas_cierre if f < fin]
        inicio = max(anteriores) + timedelta(days=1) if anteriores else fin - timedelta(days=30)
        return inicio, fin
    dia_cierre = safe_int(row.get("Dia cierre", 1), 1)
    mes_ant, año_ant = (12, año - 1) if mes == 1 else (mes - 1, año)
    ultimo_mes_ant = calendar.monthrange(año_ant, mes_ant)[1]
    inicio = date(año_ant, mes_ant, min(dia_cierre + 1, ultimo_mes_ant))
    fin = date(año, mes, min(dia_cierre, calendar.monthrange(año, mes)[1]))
    return inicio, fin

def periodo_actual_de_gasto(fecha_str, tarjeta_nombre):
    s = str(fecha_str).strip()
    if not s or s.lower() in ("s/f", "nan", "nat", "none", ""):
        hoy = date.today()
        return hoy.year, hoy.month
    fg = None
    try:
        fg = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        try:
            fg = pd.to_datetime(s, dayfirst=True).date()
        except Exception:
            hoy = date.today()
            return hoy.year, hoy.month
    t_df = load("tarjetas")
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return fg.year, fg.month
    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]
    fechas_cierre = _generar_fechas_cierre(row)
    if fechas_cierre:
        posteriores = [f for f in fechas_cierre if f >= fg]
        if posteriores:
            cierre_del_ciclo = min(posteriores)
            return cierre_del_ciclo.year, cierre_del_ciclo.month
        return fg.year, fg.month
    dia_cierre_raw = safe_int(row.get("Dia cierre", 1), 1)
    ultimo_dia_mes_gasto = calendar.monthrange(fg.year, fg.month)[1]
    dia_cierre = min(dia_cierre_raw, ultimo_dia_mes_gasto)
    if fg.day < dia_cierre:
        return fg.year, fg.month
    return (fg.year + 1, 1) if fg.month == 12 else (fg.year, fg.month + 1)

def filtrar_gastos_tarjeta_periodo(gastos_df, tarjeta_nombre, año_periodo, mes_periodo):
    if gastos_df.empty: return gastos_df.copy()
    rows = []
    for idx, r in gastos_df.iterrows():
        if str(r.get("Tarjeta", "")).strip() == tarjeta_nombre:
            ay, am = periodo_actual_de_gasto(r.get("Fecha", ""), tarjeta_nombre)
            if ay == año_periodo and am == mes_periodo:
                rows.append(r)
    return pd.DataFrame(rows, columns=gastos_df.columns) if rows else pd.DataFrame(columns=gastos_df.columns)

def proyectar_cuotas(df):
    if df.empty:
        return df
    filas_proyectadas = []
    col_cuotas = "Cuotas" if "Cuotas" in df.columns else "Cuota" if "Cuota" in df.columns else None
    
    for idx, r in df.iterrows():
        c_str = str(r.get(col_cuotas, "")) if col_cuotas else ""
        actual, total = parsear_cuotas(c_str)
        
        if total <= 1:
            fila = r.copy()
            fila["Cuota actual"] = 1
            fila["Cuota total"] = 1
            fila["Es proyectada"] = False
            if col_cuotas:
                fila[col_cuotas] = "1" if c_str else ""
            filas_proyectadas.append(fila)
            continue
            
        fecha_str = str(r.get("Fecha", ""))
        try:
            fecha_base = pd.to_datetime(fecha_str)
        except Exception:
            filas_proyectadas.append(r.copy())
            continue
            
        for n_cuota in range(1, total + 1):
            fila = r.copy()
            delta_meses = n_cuota - actual
            mes_total = fecha_base.month + delta_meses
            
            año_cuota = fecha_base.year + (mes_total - 1) // 12
            mes_cuota = (mes_total - 1) % 12 + 1
            
            ultimo_dia_mes = calendar.monthrange(año_cuota, mes_cuota)[1]
            dia_cuota = min(fecha_base.day, ultimo_dia_mes)
            fecha_cuota = date(año_cuota, mes_cuota, dia_cuota)
            
            fila["Fecha"] = fecha_cuota.strftime("%Y-%m-%d")
            fila["Cuota actual"] = n_cuota
            fila["Cuota total"] = total
            fila["Es proyectada"] = (n_cuota != actual)
            
            if col_cuotas:
                fila[col_cuotas] = f"Cuota {n_cuota}/{total}"
                
            filas_proyectadas.append(fila)
            
    return pd.DataFrame(filas_proyectadas).reset_index(drop=True)

def es_concepto_usd(concepto):
    s = str(concepto).upper()
    return bool(re.search(r'\(\s*U\\$S\s*\)|\(\s*USD\s*\)|\bU\\$S\b', s))

@st.cache_data(ttl=3600)
def obtener_cotizacion_dolar_tarjeta():
    try:
        resp = requests.get("https://dolarapi.com/v1/dolares/tarjeta", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        venta = float(data.get("venta", 0))
        return venta if venta > 0 else None
    except Exception:
        return None

def convertir_monto_usd_a_ars(monto_usd, cotizacion=None):
    if cotizacion is None:
        cotizacion = obtener_cotizacion_dolar_tarjeta()
    if cotizacion is None or cotizacion <= 0:
        return None
    try:
        return round(float(monto_usd) * cotizacion, 2)
    except (ValueError, TypeError):
        return None

def get_color_tarjeta(tname, tarjetas_df):
    if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
        c = str(tarjetas_df[tarjetas_df["Nombre"] == tname].iloc[0].get("Color", "#7c6af7"))
        return c if c.startswith("#") else "#7c6af7"
    idx = TARJETAS_DEFAULT.index(tname) if tname in TARJETAS_DEFAULT else 0
    return COLORES_TARJETA[idx % len(COLORES_TARJETA)]

def normalizar_texto(s):
    s = str(s).strip().lower()
    return " ".join(s.split())

def limpiar_csv_montos(texto_csv):
    lineas = texto_csv.strip().split("\n")
    if not lineas:
        return texto_csv
    resultado = [lineas[0]]
    for linea in lineas[1:]:
        if not linea.strip():
            continue
        linea_fix = re.sub(r'"(\d{1,3}(?:,\d{3})+\.\d+)"', lambda m: m.group(1).replace(",", ""), linea)
        linea_fix = re.sub(r',(\d{1,3}),(\d{3}\.\d+)', r',\1\2', linea_fix)
        resultado.append(linea_fix)
    return "\n".join(resultado)

def es_duplicado(fecha_str, concepto, monto, tarjeta, gastos_existentes):
    if gastos_existentes.empty:
        return False
    concepto_norm = normalizar_texto(concepto)
    tarjeta_norm = normalizar_texto(tarjeta)
    fecha_norm = normalizar_texto(fecha_str)
    try:
        monto_f = float(monto)
    except (ValueError, TypeError):
        return False
    existentes = gastos_existentes.copy()
    existentes["_concepto_norm"] = existentes["Concepto"].apply(normalizar_texto)
    existentes["_tarjeta_norm"] = existentes["Tarjeta"].apply(normalizar_texto)
    existentes["_fecha_norm"] = existentes["Fecha"].apply(normalizar_texto)
    candidatos = existentes[
        (existentes["_concepto_norm"] == concepto_norm) &
        (existentes["_tarjeta_norm"] == tarjeta_norm) &
        (existentes["_fecha_norm"] == fecha_norm)
    ]
    if candidatos.empty:
        return False
    for idx, r in candidatos.iterrows():
        try:
            monto_existente = float(r.get("Monto", 0))
        except (ValueError, TypeError):
            continue
        if abs(monto_existente - monto_f) < 1.0:
            return True
    return False

# Inyección UI e Interfaz Gráfica (CSS Nativo)
st.set_page_config(page_title="Biyuyo", layout="centered", initial_sidebar_state="collapsed")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css?family=DM+Sans:wght@300;400;500;600&display=swap');
*,*::before,*::after{box-sizing:border-box}
html, body, [class*="css"],.stApp{font-family: 'DM Sans', sans-serif!important; background:#040408; color:#dde0f0}
#MainMenu, header, footer {visibility:hidden}
.block-container {padding:0 0 6rem!important; max-width: 430px!important; margin:0 auto}
.app-header {background: #080810; padding:1rem 1rem 0.5rem; position:sticky; top:0; z-index:99}
.app-brand {font-family: 'DM Mono', monospace; font-size:1.1rem; font-weight:500; color:#ffffff}
.app-brand span {color:#7c6af7}
[data-baseweb="tab-list"] {background:#0d0d18!important; border-radius:0!important;}
[data-baseweb="tab"] {border-radius:0!important; font-size:0.75rem!important; font-weight:500}
.hero-block {padding:1.5rem 1rem 1rem; border-bottom:1px solid #14141e}
.hero-eyebrow {font-size:0.65rem; font-weight:600; text-transform:uppercase; letter-spacing:0.1em; color:#888}
.hero-num {font-family: 'DM Mono', monospace; font-size:3rem; font-weight:500; line-height:1}
.hero-sub {font-size:0.75rem; color:#444; margin-top:0.4rem}
.c-pos{color:#39e07a}.c-neg{color:#ff5f7e}.c-neu{color:#6c63ff}.c-yel{color:#f5c542}
.stat-row {display: flex; border-bottom:1px solid #14141e}
.stat-cell {flex:1; padding:0.9rem 1rem; border-right:1px solid #14141e}
.stat-cell:last-child {border-right:none}
.stat-label {font-size:0.63rem; text-transform:uppercase; letter-spacing:0.1em; color:#666}
.stat-val {font-family: 'DM Mono', monospace; font-size:1.1rem; font-weight:500; letter-spacing:-0.02em}
.tarjeta-row {display: flex; align-items:center; padding:0.9rem 1rem; border-bottom:1px solid #14141e}
.tarjeta-pip {width:8px; height:8px; border-radius:50%; flex-shrink:0}
.tarjeta-label {font-size:0.82rem; font-weight:500; color:#bbb; flex:1}
.tarjeta-meta-small {font-size:0.68rem; color:#444}
.tarjeta-amount {font-family: 'DM Mono', monospace; font-size:0.92rem; font-weight:500}
.tarjeta-bar-bg {width:60px; height:3px; background:#1a1a28; border-radius:99px; overflow:hidden}
.tarjeta-bar-fill {height:100%; border-radius:99px}
.tx {display: flex; align-items:center; padding:0.85rem 1rem; border-bottom:1px solid #14141e}
.tx-ico {width:34px; height:34px; background:#12121e; border-radius:8px; display:flex; align-items:center; justify-content:center}
.tx-main {flex:1; min-width:0; margin-left:0.5rem}
.tx-name {font-size:0.84rem; font-weight:500; color:#dde0f0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.tx-info {font-size:0.68rem; color:#666; margin-top:1px}
.tx-amt {font-family: 'DM Mono', monospace; font-size:0.88rem; font-weight:500; text-align:right}
.sec {padding:1.1rem 1rem 0.4rem; font-size:0.63rem; font-weight:600; text-transform:uppercase; letter-spacing:0.1em; color:#666}
.prog-wrap {padding:0.7rem 1rem; border-bottom:1px solid #14141e}
.prog-head {display: flex; justify-content: space-between; font-size:0.78rem; margin-bottom:0.3rem}
.prog-bg {background:#12121e; border-radius:99px; height:4px; overflow:hidden}
.prog-fill {height:100%; border-radius:99px}
.prog-note {font-size:0.65rem; color:#666; margin-top:0.3rem}
.per-badge {display:inline-flex; align-items:center; gap:4px; background:#0d0d18; border:1px solid #14141e; padding:0.2rem 0.5rem; border-radius:4px}
.per-badge.open {border-color:#39e07a33; color:#39e07a}
.per-badge.closed {border-color:#ff5f7e33; color:#ff5f7e}
.per-badge.future {border-color:#6c63ff33; color:#6c63ff}
.empty {text-align:center; padding:2.5rem 1rem; color:#444; font-size:0.82rem}
.total-strip {display: flex; justify-content:space-between; align-items:center; padding:1rem; background:#0a0a14; border-bottom:1px solid #14141e}
.total-strip-label {font-size:0.68rem; text-transform:uppercase; letter-spacing:0.1em; color:#666}
.total-strip-val {font-family:'DM Mono', monospace; font-size:1rem; font-weight:500}
.info-strip {background:#6c63ff10; border-left:2px solid #6c63ff; padding:0.6rem 1rem; font-size:0.75rem; margin-bottom:1rem; color:#aaa}
</style>
""", unsafe_allow_html=True)

# Inicialización segura de Estados de Sesión
if "gasto_limit" not in st.session_state: st.session_state.gasto_limit = 30
if "menu_accion" not in st.session_state: st.session_state.menu_accion = False

# Lectura General de Datos de Disco
gastos_df = load("gastos")
ingresos_df = load("ingresos")
comp_df = load("compartidos")
inv_df = load("inversiones")
pres_df = load("presupuesto")
tarjetas_df = load("tarjetas")

gastos_df["Monto"] = to_num(gastos_df["Monto"])
gastos_df["Cuanto recupero"] = to_num(gastos_df["Cuanto recupero"])
ingresos_df["Monto"] = to_num(ingresos_df["Monto"])
comp_df["Monto"] = to_num(comp_df["Monto"])

if not gastos_df.empty:
    gastos_df["Fecha"] = gastos_df["Fecha"].apply(normalizar_fecha_existente)

y, m = mes_actual()
nombre_mes = calendar.month_name[m].capitalize()

def sort_by_fecha(df):
    if df.empty: return df
    df = df.copy()
    df["_sort"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df = df.sort_values("_sort", ascending=False, na_position="last").drop(columns=["_sort"])
    return df

gastos_df = sort_by_fecha(gastos_df)
gastos_mes = filtrar_mes(gastos_df, y, m)
ingresos_mes = filtrar_mes(ingresos_df, y, m)

total_ing = ingresos_mes["Monto"].sum()
total_gast = gastos_mes["Monto"].sum()
recupero = gastos_mes["Cuanto recupero"].sum()
remanente = total_ing - total_gast + recupero

TARJETAS = get_tarjetas_nombres()

# Renderizado del Header Fijo
st.markdown("""
<div class='app-header'>
<div style='display: flex; justify-content: space-between; align-items:center'>
<div class='app-brand'>biyuyo<span>.</span></div>
</div>
</div>""", unsafe_allow_html=True)

# Botonera Desplegable para Nuevos Registros
col_r, _ = st.columns([3, 1])
with col_r:
    if st.button("x Cerrar" if st.session_state.menu_accion else "+ Agregar", key="menu_trigger"):
        st.session_state.menu_accion = not st.session_state.menu_accion
        st.rerun()

if st.session_state.menu_accion:
    st.markdown("<div class='acciones-panel'>", unsafe_allow_html=True)
    tipo = st.radio("", ["Gasto", "Ingreso", "Tarjeta"], horizontal=True, key="tipo_acc", label_visibility="collapsed")
    
    if tipo == "Gasto":
        with st.form("fq_g", clear_on_submit=True):
            c1, c2 = st.columns(2)
            q_c = c1.text_input("Concepto")
            q_m = c2.number_input("Monto $", min_value=0.0, step=100.0)
            c3, c4 = st.columns(2)
            q_t = c3.selectbox("Tarjeta", TARJETAS)
            q_k = c4.selectbox("Categoria", CAT_GASTOS)
            c5, c6 = st.columns(2)
            q_f = c5.date_input("Fecha", value=date.today())
            q_cu = c6.number_input("Cuotas", min_value=1, max_value=48, value=1)
            ca, cb = st.columns([3, 1])
            if ca.form_submit_button("Guardar gasto"):
                if q_c.strip() and q_m > 0:
                    fecha_str = fmt_fecha(q_f)
                    nv = pd.DataFrame([[fecha_str, q_c.strip(), q_m, q_t, q_k, str(q_cu), "No", "", 0.0, ""]], columns=FILES["gastos"][1])
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    save("gastos", gastos_df)
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")
            if cb.form_submit_button("x"):
                st.session_state.menu_accion = False
                st.rerun()
                
    elif tipo == "Ingreso":
        with st.form("fq_i", clear_on_submit=True):
            c1, c2 = st.columns(2)
            i_c = c1.text_input("Concepto")
            i_m = c2.number_input("Monto $", min_value=0.0, step=1000.0)
            c3, c4 = st.columns(2)
            i_k = c3.selectbox("Categoria", CAT_ING)
            i_f = c4.date_input("Fecha", value=date.today())
            ca, cb = st.columns([3, 1])
            if ca.form_submit_button("Guardar ingreso"):
                if i_c.strip() and i_m > 0:
                    nv = pd.DataFrame([[fmt_fecha(i_f), i_c.strip(), i_m, i_k]], columns=ingresos_df.columns)
                    ingresos_df = pd.concat([ingresos_df, nv], ignore_index=True)
                    save("ingresos", ingresos_df)
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")
            if cb.form_submit_button("x"):
                st.session_state.menu_accion = False
                st.rerun()
                
    elif tipo == "Tarjeta":
        with st.form("fq_t", clear_on_submit=True):
            t_n = st.text_input("Nombre de la tarjeta")
            c1, c2 = st.columns(2)
            t_dc = c1.number_input("Dia cierre", min_value=1, max_value=28, value=1)
            t_dv = c2.number_input("Dia vencimiento", min_value=1, max_value=31, value=1)
            t_col = st.selectbox("Color", COLORES_TARJETA)
            ca, cb = st.columns([3, 1])
            if ca.form_submit_button("Crear tarjeta"):
                if t_n.strip():
                    base = tarjetas_df[tarjetas_df["Nombre"] != t_n.strip()].copy() if not tarjetas_df.empty else pd.DataFrame(columns=FILES["tarjetas"][1])
                    fila = pd.DataFrame([[t_n.strip(), str(t_dc), str(t_dv), t_col, "", "", "31"]], columns=FILES["tarjetas"][1])
                    tarjetas_df = pd.concat([base, fila], ignore_index=True)
                    save("tarjetas", tarjetas_df)
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Ingresá un nombre.")
            if cb.form_submit_button("x"):
                st.session_state.menu_accion = False
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# Inicialización de Pestañas Principales
tabs = st.tabs(["Inicio", "Gastos", "Tarjetas", "Ingresos", "Compartidos", "Inversiones", "Presupuesto", "Flujo de Fondos"])

# ==========================================
# TAB 0: INICIO (Resumen Financiero Mensual)
# ==========================================
with tabs[0]:
    color_hero = "c-pos" if remanente >= 0 else "c-neg"
    st.markdown(
        f"<div class='hero-block'>"
        f"<div class='hero-eyebrow'>{nombre_mes} {y} • Remanente</div>"
        f"<div class='hero-num {color_hero}'>{fmt_ars(remanente)}</div>"
        f"</div>", unsafe_allow_html=True)
    
    recup_cell = f"<div class='stat-cell'><div class='stat-label'>Recuperás</div><div class='stat-val c-yel'>{fmt_ars(recupero)}</div></div>" if recupero > 0 else ""
    st.markdown(
        f"<div class='stat-row'>"
        f"<div class='stat-cell'><div class='stat-label'>Entró</div><div class='stat-val c-pos'>{fmt_ars(total_ing)}</div></div>"
        f"<div class='stat-cell'><div class='stat-label'>Salió</div><div class='stat-val c-neg'>{fmt_ars(total_gast)}</div></div>"
        f"{recup_cell}"
        f"</div>", unsafe_allow_html=True)
    
    gastos_fresh = load("gastos")
    gastos_fresh["Monto"] = to_num(gastos_fresh["Monto"])
    gastos_fresh = sort_by_fecha(gastos_fresh)
    gastos_proyectado = proyectar_cuotas(gastos_fresh)
    
    st.markdown("<div class='sec'>Este período (según cierre de cada tarjeta)</div>", unsafe_allow_html=True)
    tarjetas_con_gasto = {}
    for tname in TARJETAS:
        gf = filtrar_gastos_tarjeta_periodo(gastos_proyectado, tname, y, m)
        total_t = gf["Monto"].sum() if not gf.empty else 0
        if total_t > 0:
            tarjetas_con_gasto[tname] = total_t
            
    if tarjetas_con_gasto:
        max_t = max(tarjetas_con_gasto.values())
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = get_color_tarjeta(tname, tarjetas_df)
            pct = int(total_t / max_t * 100) if max_t > 0 else 0
            meta_html = ""
            if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
                dc = safe_int(tarjetas_df[tarjetas_df["Nombre"] == tname].iloc[0].get("Dia cierre", 1), 1)
                meta_html = f"<div class='tarjeta-meta-small'>cierra dia {dc}</div>"
            bar_fill = f"<div class='tarjeta-bar-fill' style='width: {pct}%; background: {color}'></div>"
            st.markdown(
                f"<div class='tarjeta-row'>"
                f"<div class='tarjeta-pip' style='background: {color}'></div>"
                f"<div style='flex:1; margin-left:0.5rem'><div class='tarjeta-label'>{tname}</div>{meta_html}</div>"
                f"<div class='tarjeta-bar-bg'>{bar_fill}</div>"
                f"<div class='tarjeta-amount c-neg' style='margin-left:1rem'>{fmt_ars(total_t)}</div>"
                f"</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='empty'>Sin gastos este mes.</div>", unsafe_allow_html=True)
        
    st.markdown("<div class='sec'>Últimos movimientos</div>", unsafe_allow_html=True)
    tiene_fecha = pd.to_datetime(gastos_fresh["Fecha"], errors="coerce").notna()
    con_fecha = gastos_fresh[tiene_fecha].head(8)
    sin_fecha = gastos_fresh[~tiene_fecha].head(max(0, 8 - len(con_fecha)))
    recientes = pd.concat([con_fecha, sin_fecha], ignore_index=True)
    
    if recientes.empty:
        st.markdown("<div class='empty'>Sin movimientos todavía.</div>", unsafe_allow_html=True)
    else:
        for idx, r in recientes.iterrows():
            ico = emoji_cat(str(r.get("Categoria", "")))
            fecha_str = normalizar_fecha_existente(r.get("Fecha", "")) or "sin fecha"
            tname_r = str(r.get("Tarjeta", ""))
            cuotas_v = safe_int(r.get("Cuotas", 1), 1)
            cuotas_t = f" ({cuotas_v} c)" if cuotas_v > 1 else ""
            chip = ""
            if not tarjetas_df.empty and tname_r in tarjetas_df["Nombre"].values:
                ay, am = periodo_actual_de_gasto(r.get("Fecha", ""), tname_r)
                if ay != y or am != m:
                    chip = f"<span class='chip-next'>{calendar.month_name[am][:3]}</span>"
            st.markdown(
                f"<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                f"<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto', '-')}{chip}</div>"
                f"<div class='tx-info'>{fecha_str} • {tname_r}{cuotas_t}</div>"
                f"</div>"
                f"<div class='tx-amt c-neg'>-{fmt_ars(r.get('Monto', 0))}</div>"
                f"</div>", unsafe_allow_html=True)
                
    pend = comp_df[comp_df["Estado"] == "Pendiente"] if not comp_df.empty else pd.DataFrame()
    if not pend.empty:
        st.markdown("<div class='sec'>Te deben</div>", unsafe_allow_html=True)
        for idx, r in pend.iterrows():
            st.markdown(
                f"<div class='pend-row' style='display:flex; justify-content:space-between; padding:0.5rem 1rem; border-bottom:1px solid #14141e'>"
                f"<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto', '-')}</div>"
                f"<div class='tx-info'>{r.get('Con quien', '')} • {str(r.get('Fecha',''))[:10]}</div>"
                f"</div>"
                f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Monto',0))}</div>"
                f"</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='total-strip'>"
            f"<span class='total-strip-label'>Total pendiente</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(pend['Monto'].sum())}</span>"
            f"</div>", unsafe_allow_html=True)

# ==========================================
# TAB 1: GASTOS (Importador, Exportador e Historial)
# ==========================================
with tabs[1]:
    with st.expander("📥 Exportar todos los gastos (backup)"):
        gastos_export = load("gastos")
        if gastos_export.empty:
            st.caption("No hay gastos cargados todavía.")
        else:
            csv_export = gastos_export.to_csv(index=False)
            st.text_area(f"{len(gastos_export)} movimientos en total", value=csv_export, height=160, key="export_area")
            st.download_button("Descargar como archivo .csv", data=csv_export, file_name=f"backup_gastos_{date.today().isoformat()}.csv", mime="text/csv", key="download_action_btn")
            
    with st.expander("📤 Importar desde CSV / resumen de texto"):
        st.markdown("<div class='info-strip'>Estructura requerida: <code>Fecha, Concepto, Monto, Cuotas</code>.</div>", unsafe_allow_html=True)
        csv_text = st.text_area("", placeholder="Fecha, Concepto, Monto, Cuotas\n09-Jun-2026,NIKE LA PLATA,37999.66,Cuota 1/6", key="csv_import_box")
        tarjeta_import = st.selectbox("Tarjeta para estos movimientos (Fallback de asignación)", TARJETAS, key="tarjeta_fallback")
        
        if st.button("🔍 Previsualizar Datos", key="preview_action"):
            if csv_text.strip():
                try:
                    csv_limpio = limpiar_csv_montos(csv_text)
                    nuevos = pd.read_csv(io.StringIO(csv_limpio), dtype=str).fillna("")
                    nuevos.columns = [c.strip() for c in nuevos.columns]
                    
                    if "Tarjeta" not in nuevos.columns:
                        nuevos["Tarjeta"] = tarjeta_import
                    else:
                        tarjeta_vacia_mask = nuevos["Tarjeta"].astype(str).str.strip().str.lower().isin({"", "none", "nan", "nat", "null", "<na>"})
                        nuevos.loc[tarjeta_vacia_mask, "Tarjeta"] = tarjeta_import
                        
                    for col in FILES["gastos"][1]:
                        if col not in nuevos.columns:
                            nuevos[col] = "0" if col in ["Monto", "Cuanto recupero"] else "No" if col == "Compartido" else ""
                            
                    nuevos = nuevos[FILES["gastos"][1]]
                    nuevos["Fecha"] = nuevos["Fecha"].apply(normalizar_fecha_existente)
                    nuevos["Monto"] = to_num(nuevos["Monto"])
                    
                    usd_mask = nuevos["Concepto"].apply(es_concepto_usd)
                    usd_count = int(usd_mask.sum())
                    usd_sin_convertir = 0
                    if usd_count > 0:
                        cotiz = obtener_cotizacion_dolar_tarjeta()
                        if cotiz:
                            nuevos.loc[usd_mask, "Notas"] = nuevos.loc[usd_mask, "Concepto"].astype(str) + " (USD original)"
                            nuevos.loc[usd_mask, "Monto"] = nuevos.loc[usd_mask, "Monto"].apply(lambda m: convertir_monto_usd_a_ars(m, cotiz))
                        else:
                            usd_sin_convertir = usd_count
                            
                    PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior"]
                    concepto_lower = nuevos["Concepto"].astype(str).str.strip().str.lower()
                    es_pago_mask = concepto_lower.isin(PALABRAS_EXCLUIR) | (nuevos["Monto"] <= 0)
                    excluidos_count = int(es_pago_mask.sum())
                    nuevos = nuevos[~es_pago_mask].copy()
                    
                    gastos_actuales = load("gastos")
                    gastos_actuales["Monto"] = to_num(gastos_actuales["Monto"])
                    if not gastos_actuales.empty:
                        gastos_actuales["Fecha"] = gastos_actuales["Fecha"].apply(normalizar_fecha_existente)
                        
                    es_dup_mask = nuevos.apply(lambda r: es_duplicado(r["Fecha"], r["Concepto"], r["Monto"], r["Tarjeta"], gastos_actuales), axis=1)
                    nuevos_filtrados = nuevos[~es_dup_mask].copy()
                    duplicados_count = int(es_dup_mask.sum())
                    
                    st.session_state["_csv_preview"] = nuevos_filtrados
                    st.session_state["_csv_dup_count"] = duplicados_count
                    st.session_state["_csv_excl_count"] = excluidos_count
                    st.session_state["_csv_usd_count"] = usd_count
                    st.session_state["_csv_usd_sin_convertir"] = usd_sin_convertir
                except Exception as e:
                    st.error(f"Error interpretando la entrada del CSV: {e}")
            else:
                st.warning("El campo de texto está vacío.")
                
        if "_csv_preview" in st.session_state:
            preview = st.session_state["_csv_preview"]
            dup_count = st.session_state.get("_csv_dup_count", 0)
            excl_count = st.session_state.get("_csv_excl_count", 0)
            usd_count = st.session_state.get("_csv_usd_count", 0)
            usd_sin_convertir = st.session_state.get("_csv_usd_sin_convertir", 0)
            
            if usd_count > 0:
                st.markdown(f"<div class='info-strip'>Se convirtieron {usd_count} consumos en USD al dólar tarjeta oficial.</div>", unsafe_allow_html=True)
            if excl_count > 0:
                st.markdown(f"<div class='info-strip'>{excl_count} registro(s) descartado(s) por transacciones de pago/crédito.</div>", unsafe_allow_html=True)
            if dup_count > 0:
                st.markdown(f"<div class='info-strip'>{dup_count} movimientos omitidos por colisión exacta de duplicados.</div>", unsafe_allow_html=True)
                
            if preview.empty:
                st.markdown("<div class='empty'>No hay filas nuevas procesables para importar.</div>", unsafe_allow_html=True)
            else:
                st.caption(f"{len(preview)} transacciones listas para inyección:")
                for idx, r in preview.iterrows():
                    ico = emoji_cat(str(r.get("Categoria","")))
                    st.markdown(
                        f"<div class='tx'>"
                        f"<div class='tx-ico'>{ico}</div>"
                        f"<div class='tx-main'>"
                        f"<div class='tx-name'>{r.get('Concepto', '-')}</div>"
                        f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]}</div>"
                        f"</div>"
                        f"<div class='tx-amt c-neg'>-{fmt_ars(r.get('Monto',0))}</div>"
                        f"</div>", unsafe_allow_html=True)
                if st.button(f"Confirmar e importar {len(preview)} movimientos", key="execute_import"):
                    base = load("gastos")
                    base["Monto"] = to_num(base["Monto"])
                    final = pd.concat([base, preview], ignore_index=True)
                    final = sort_by_fecha(final)
                    save("gastos", final)
                    st.session_state.pop("_csv_preview", None)
                    st.success(f"✓ {len(preview)} movimientos persistidos con éxito.")
                    st.rerun()
                    
    with st.expander("📝 Carga Manual Unitaria"):
        with st.form("f_gasto_full", clear_on_submit=True):
            g_c = st.text_input("Concepto", placeholder="Ej: Supermercado, Combustible...")
            c1, c2 = st.columns(2)
            g_m = c1.number_input("Monto $", min_value=0.0, step=500.0)
            g_cu = c2.number_input("Cuotas Totales", min_value=1, max_value=48, value=1)
            c3, c4 = st.columns(2)
            g_t = c3.selectbox("Tarjeta / Origen", TARJETAS, key="manual_g_t")
            g_k = c4.selectbox("Categoría de Gasto", CAT_GASTOS, key="manual_g_k")
            c5, c6 = st.columns(2)
            g_f = c5.date_input("Fecha Transacción", value=date.today(), key="manual_g_f")
            g_comp = c6.selectbox("¿Es un gasto compartido?", ["No", "Si"])
            c7, c8 = st.columns(2)
            g_quien = c7.text_input("Persona involucrada", placeholder="Nombre")
            g_rec = c8.number_input("Monto a recuperar $", min_value=0.0, step=100.0)
            g_nota = st.text_input("Notas adicionales", placeholder="Opcional")
            if st.form_submit_button("Guardar Registro"):
                if g_c.strip() and g_m > 0:
                    fecha_str = fmt_fecha(g_f)
                    nv = pd.DataFrame([[fecha_str, g_c.strip(), g_m, g_t, g_k, str(g_cu), g_comp, g_quien, g_rec, g_nota]], columns=FILES["gastos"][1])
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    gastos_df = sort_by_fecha(gastos_df)
                    save("gastos", gastos_df)
                    if g_comp == "Si" and g_rec > 0:
                        nvc = pd.DataFrame([[fecha_str, g_c.strip(), g_rec, g_quien, "Pendiente"]], columns=FILES["compartidos"][1])
                        comp_df2 = load("compartidos")
                        comp_df2 = pd.concat([comp_df2, nvc], ignore_index=True)
                        save("compartidos", comp_df2)
                    st.success("Gasto guardado correctamente.")
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")
                    
    with st.expander("🗑️ Depuración de Registros"):
        PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior"]
        concepto_lower_g = gastos_df["Concepto"].astype(str).str.strip().str.lower() if not gastos_df.empty else pd.Series(dtype=str)
        gastos_monto_num = to_num(gastos_df["Monto"]) if not gastos_df.empty else pd.Series(dtype=float)
        mask_basura = concepto_lower_g.isin(PALABRAS_EXCLUIR) | (gastos_monto_num <= 0) if not gastos_df.empty else pd.Series(dtype=bool)
        candidatos_basura = gastos_df[mask_basura] if not gastos_df.empty else pd.DataFrame()
        if not candidatos_basura.empty:
            st.markdown(f"<div class='info-strip'>Se aislaron {len(candidatos_basura)} ítems inválidos/pagos en el histórico.</div>", unsafe_allow_html=True)
            if st.button(f"Purgar {len(candidatos_basura)} filas del histórico"):
                gastos_df_limpio = gastos_df.drop(index=candidatos_basura.index)
                save("gastos", gastos_df_limpio)
                st.success("Limpieza completada con éxito.")
                st.rerun()
        st.divider()
        del_q = st.text_input("Buscador de conceptos para remover", placeholder="Texto clave del concepto...")
        if del_q:
            cands = gastos_df[gastos_df["Concepto"].str.contains(del_q, case=False, na=False)] if not gastos_df.empty else pd.DataFrame()
            if cands.empty:
                st.caption("Sin coincidencias.")
            else:
                for idx, r in cands.iterrows():
                    ca, cb = st.columns([5, 1])
                    ca.markdown(f"**{r['Concepto']}** | {r['Fecha']} | {fmt_ars(r['Monto'])}")
                    if cb.button("x", key=f"dg_act_{idx}"):
                        gastos_df = gastos_df.drop(index=idx).reset_index(drop=True)
                        save("gastos", gastos_df)
                        st.rerun()
                        
    st.markdown("<div class='sec'>Todos los movimientos</div>", unsafe_allow_html=True)
    if gastos_df.empty:
        st.markdown("<div class='empty'>No hay transacciones registradas.</div>", unsafe_allow_html=True)
    else:
        for idx, r in gastos_df.head(st.session_state.gasto_limit).iterrows():
            ico = emoji_cat(str(r.get("Categoria", "")))
            fstr = str(r.get("Fecha", ""))[:10]
            cuotas_v = safe_int(r.get("Cuotas", 1), 1)
            cuotas_t = f" ({cuotas_v} c)" if cuotas_v > 1 else ""
            st.markdown(
                f"<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                f"<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto', '-')}</div>"
                f"<div class='tx-info'>{fstr} • {r.get('Tarjeta', '')}{cuotas_t}</div>"
                f"</div>"
                f"<div class='tx-amt c-neg'>-{fmt_ars(r.get('Monto', 0))}</div>"
                f"</div>", unsafe_allow_html=True)
        if len(gastos_df) > st.session_state.gasto_limit:
            if st.button("Cargar más movimientos ▼", key="load_more_g"):
                st.session_state.gasto_limit += 25
                st.rerun()
    st.markdown(
        f"<div class='total-strip'>"
        f"<span class='total-strip-label'>Histórico Acumulado</span>"
        f"<span class='total-strip-val c-neg'>{fmt_ars(gastos_df['Monto'].sum() if not gastos_df.empty else 0)}</span>"
        f"</div>", unsafe_allow_html=True)

# ==========================================
# TAB 2: TARJETAS (Ciclos y Períodos de Cierre)
# ==========================================
with tabs[2]:
    st.markdown("<div class='sec'>Configuración de Parámetros de Ciclos</div>", unsafe_allow_html=True)
    if tarjetas_df.empty or len(tarjetas_df) < 3:
        st.markdown("<div class='info-strip'>Estructura de tarjetas incompleta. Puede inicializar los valores de los cierres bancarios predeterminados.</div>", unsafe_allow_html=True)
        if st.button("⚙️ Autoconfigurar Tarjetas Base"):
            config_rapida = pd.DataFrame([
                ["Visa ICBC", "28", "10", "#7c6af7", "", "", "31"],
                ["Visa Hipotecario", "28", "5", "#4ade80", "", "", "31"],
                ["Master ICBC", "28", "10", "#f87171", "", "", "31"]
            ], columns=FILES["tarjetas"][1])
            nombres_config = config_rapida["Nombre"].tolist()
            resto = tarjetas_df[~tarjetas_df["Nombre"].isin(nombres_config)] if not tarjetas_df.empty else pd.DataFrame()
            final_tarjetas = pd.concat([config_rapida, resto], ignore_index=True)
            save("tarjetas", final_tarjetas)
            st.success("Estructura de tarjetas inyectada.")
            st.rerun()
    st.divider()
    
    if tarjetas_df.empty:
        tarjetas_edit = pd.DataFrame(columns=FILES["tarjetas"][1])
    else:
        tarjetas_edit = tarjetas_df.copy()
        tarjetas_edit["Dia cierre"] = pd.to_numeric(tarjetas_edit["Dia cierre"], errors="coerce").fillna(1)
        tarjetas_edit["Dia vencimiento"] = pd.to_numeric(tarjetas_edit["Dia vencimiento"], errors="coerce").fillna(1)
        tarjetas_edit["Dias entre cierres"] = pd.to_numeric(tarjetas_edit["Dias entre cierres"], errors="coerce").fillna(31)
        
    st.markdown("<div class='info-strip'>Defina cierres explícitos (Cierre anterior/Próximo cierre) para ciclos irregulares, o configure los días fijos simples.</div>", unsafe_allow_html=True)
    edited_t = st.data_editor(tarjetas_edit, num_rows="dynamic", use_container_width=True, key="data_editor_tarjetas")
    
    if st.button("💾 Guardar Configuración de Tarjetas"):
        edited_limpio = edited_t.copy()
        edited_limpio["Nombre"] = edited_limpio["Nombre"].fillna("").astype(str).str.strip()
        edited_limpio = edited_limpio[edited_limpio["Nombre"] != ""].reset_index(drop=True)
        save("tarjetas", edited_limpio)
        st.success("Líneas de ciclos guardadas.")
        st.rerun()
        
    st.markdown("<div class='sec'>Gastos por Tarjeta en su Ciclo de Facturación</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    t_sel = c1.selectbox("Filtrar Tarjeta", TARJETAS, key="selector_tarjeta_tab")
    
    periodos = []
    for delta in range(-5, 2):
        pm, py = m + delta, y
        while pm <= 0: pm += 12; py -= 1
        while pm > 12: pm -= 12; py += 1
        periodos.append((py, pm))
        
    opciones_per = []
    for py, pm in periodos:
        ini, fin = get_periodo_tarjeta(t_sel, py, pm)
        opciones_per.append(f"{calendar.month_name[pm][:3].capitalize()} {py} ({ini.strftime('%d/%m')}-{fin.strftime('%d/%m')})")
        
    per_sel = c2.selectbox("Seleccionar Período", opciones_per, index=5, key="selector_periodo_tab")
    sel_py, sel_pm = periodos[opciones_per.index(per_sel)]
    inicio_p, fin_p = get_periodo_tarjeta(t_sel, sel_py, sel_pm)
    
    gastos_base = load("gastos")
    gastos_base["Monto"] = to_num(gastos_base["Monto"])
    gastos_base["_row_id"] = range(len(gastos_base))
    
    df_per = filtrar_gastos_tarjeta_periodo(gastos_base, t_sel, sel_py, sel_pm)
    total_per = to_num(df_per["Monto"]).sum() if not df_per.empty else 0
    
    gastos_proyectado_tab = proyectar_cuotas(gastos_base.drop(columns=["_row_id"], errors="ignore"))
    df_per_proyectado = filtrar_gastos_tarjeta_periodo(gastos_proyectado_tab, t_sel, sel_py, sel_pm)
    total_proyectado = to_num(df_per_proyectado["Monto"]).sum() if not df_per_proyectado.empty else 0
    cant_cuotas_de_others = len(df_per_proyectado[df_per_proyectado.get("Es proyectada", False) == True])
    
    hoy_d = date.today()
    if fin_p < hoy_d:
        badge_class, badge_ico = "closed", "🔒 Período Cerrado"
    elif inicio_p > hoy_d:
        badge_class, badge_ico = "future", "⏳ Período Futuro"
    else:
        dias_r = (fin_p - hoy_d).days
        badge_class, badge_ico = "open", f"🔓 Cierra en {dias_r} días"
        
    st.markdown(f"<div class='per-badge {badge_class}'>{badge_ico} ({inicio_p.strftime('%d/%m')} al {fin_p.strftime('%d/%m')})</div>", unsafe_allow_html=True)
    color_t_sel = get_color_tarjeta(t_sel, tarjetas_df)
    st.markdown(
        f"<div class='total-strip'>"
        f"<span class='total-strip-label'>Consumos Reales del Mes (Modificables)</span>"
        f"<span class='total-strip-val' style='color:{color_t_sel}'>-{fmt_ars(total_per)}</span>"
        f"</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='total-strip'>"
        f"<span class='total-strip-label'>Total Proyectado (Compras + Cuotas Arrastradas)</span>"
        f"<span class='total-strip-val' style='color:{color_t_sel}'>-{fmt_ars(total_proyectado)}</span>"
        f"</div>", unsafe_allow_html=True)
        
    if cant_cuotas_de_others > 0:
        st.markdown(f"<div class='info-strip'>Este período incluye {cant_cuotas_de_others} cuotas arrastradas de meses anteriores. El editor inferior muestra únicamente compras cuyo origen fue este período específico.</div>", unsafe_allow_html=True)
        
    if not df_per.empty:
        df_ed = df_per.drop(columns=["_row_id"], errors="ignore").copy().reset_index(drop=True)
        edited_per = st.data_editor(df_ed, num_rows="dynamic", use_container_width=True, key=f"grid_per_{t_sel}_{sel_py}_{sel_pm}")
        if st.button("💾 Persistir Modificaciones en Gastos"):
            ids_a_eliminar = set(df_per["_row_id"].astype(int))
            base = load("gastos")
            base_limpia = base.drop(index=list(ids_a_eliminar), errors="ignore")
            edited_per["Fecha"] = edited_per["Fecha"].apply(fmt_fecha)
            final = pd.concat([base_limpia, edited_per], ignore_index=True)
            final = sort_by_fecha(final)
            save("gastos", final)
            st.success("Cambios en el bloque del período persistidos de forma segura.")
            st.rerun()
    else:
        st.markdown("<div class='empty'>No se registran compras directas en esta ventana temporal.</div>", unsafe_allow_html=True)

# ==========================================
# TAB 3: INGRESOS (Gestión de Rentas)
# ==========================================
with tabs[3]:
    with st.form("f_ing", clear_on_submit=True):
        c1, c2 = st.columns(2)
        i_c = c1.text_input("Concepto de Ingreso", placeholder="Sueldo, Cobros, Ventas...")
        i_m = c2.number_input("Monto Recibido $", min_value=0.0, step=1000.0)
        c3, c4 = st.columns(2)
        i_k = c3.selectbox("Categoría de Origen", CAT_ING, key="income_cat")
        i_f = c4.date_input("Fecha de Acreditación", value=date.today(), key="income_date")
        if st.form_submit_button("Registrar Entrada"):
            if i_c.strip() and i_m > 0:
                nv = pd.DataFrame([[fmt_fecha(i_f), i_c.strip(), i_m, i_k]], columns=ingresos_df.columns)
                ingresos_df = pd.concat([ingresos_df, nv], ignore_index=True)
                save("ingresos", ingresos_df)
                st.success("Ingreso adicionado.")
                st.rerun()
            else:
                st.warning("Completá concepto y monto.")
                
    st.markdown(f"<div class='sec'>Entradas de {nombre_mes} {y}</div>", unsafe_allow_html=True)
    ing_show = ingresos_mes.sort_values("Fecha", ascending=False) if not ingresos_mes.empty else pd.DataFrame()
    if ing_show.empty:
        st.markdown("<div class='empty'>Sin entradas registradas en este mes calendario.</div>", unsafe_allow_html=True)
    else:
        for idx, r in ing_show.iterrows():
            ico = emoji_cat(str(r.get("Categoria", "")))
            st.markdown(
                f"<div class='tx'>"
                f"<div class='tx-ico'>{ico if ico else '💰'}</div>"
                f"<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto', '-')}</div>"
                f"<div class='tx-info'>{str(r.get('Fecha', ''))[:10]}</div>"
                f"</div>"
                f"<div class='tx-amt c-pos'>+{fmt_ars(r.get('Monto',0))}</div>"
                f"</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='total-strip'>"
            f"<span class='total-strip-label'>Total Ingresado en el Mes</span>"
            f"<span class='total-strip-val c-pos'>{fmt_ars(ing_show['Monto'].sum())}</span>"
            f"</div>", unsafe_allow_html=True)

# ==========================================
# TAB 4: COMPARTIDOS (Cuentas Cruzadas)
# ==========================================
with tabs[4]:
    with st.form("f_comp", clear_on_submit=True):
        c1, c2 = st.columns(2)
        co_c = c1.text_input("Concepto del Gasto Común", placeholder="Cena, Regalos, Viajes...")
        co_m = c2.number_input("Monto neto adeudado $", min_value=0.0, step=100.0)
        c3, c4 = st.columns(2)
        co_q = c3.text_input("Nombre de la persona que debe", placeholder="Nombre...")
        co_f =
