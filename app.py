import streamlit as st
import pandas as pd
import os
import re
import requests
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
    "tarjetas":    ("mis_tarjetas.csv",    ["Nombre","Dia cierre","Dia vencimiento","Color","Cierre anterior","Proximo cierre","Dias entre cierres"]),
}

CAT_GASTOS = ["🍔 Comida","🚗 Transporte","🎉 Salidas","✈️ Viaje","🏥 Salud",
               "👕 Ropa","📱 Servicios","🏠 Casa","💊 Farmacia","📚 Educación","🎁 Regalos","💳 Otro"]
CAT_ING    = ["💼 Sueldo","💻 Freelance","📈 Inversión","🎁 Regalo","💰 Otro"]
MONEDAS    = ["ARS","USD","EUR"]
COLORES_TARJETA = ["#7c6af7","#4ade80","#f87171","#fbbf24","#60a5fa","#f472b6","#34d399","#fb923c"]
TARJETAS_DEFAULT = ["Visa ICBC","Visa Hipotecario","Master ICBC","Efectivo","Débito","Otro"]

# ── Helpers y APIs ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_dolar_vendedor():
    """Obtiene la cotización del tipo de cambio vendedor del día."""
    try:
        r = requests.get("https://dolarapi.com/v1/dolares/oficial", timeout=5)
        if r.status_code == 200:
            return float(r.json().get("venta", 1000.0))
    except:
        pass
    return 1000.0

def add_months(d, m):
    """Suma M meses a una fecha manejando correctamente los fines de mes."""
    month = d.month - 1 + m
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

def procesar_usd(concepto, monto_val):
    """Detecta si hay USD en el concepto/monto y devuelve el monto en ARS y el concepto actualizado."""
    c_upper = str(concepto).upper()
    is_usd = False
    if "USD" in c_upper or "U$S" in c_upper or "US$" in c_upper:
        is_usd = True
    
    if is_usd:
        rate = get_dolar_vendedor()
        monto_ars = float(monto_val) * rate
        # Solo agregamos el tag si no estaba explicitado
        nuevo_concepto = concepto if "USD" in c_upper else f"{concepto} (USD)"
        return nuevo_concepto, monto_ars
    return concepto, float(monto_val)

def expandir_filas_cuotas(df_nuevos, es_manual=False):
    """Genera las filas de cuotas futuras automáticamente sumando meses."""
    filas = []
    for _, r in df_nuevos.iterrows():
        c = safe_int(r.get("Cuotas", 1), 1)
        if c > 1:
            monto_base = float(r.get("Monto", 0))
            # Si es manual, divide el monto total. Si es CSV, suele ser el valor ya dividido de la cuota.
            monto_cuota = monto_base / c if es_manual else monto_base
            recup_base = float(r.get("Cuanto recupero", 0))
            recup_cuota = recup_base / c if es_manual else recup_base

            try:
                f_ini = datetime.strptime(str(r["Fecha"])[:10], "%Y-%m-%d").date()
            except:
                f_ini = date.today()

            conc_base = str(r.get("Concepto", ""))

            # Evita duplicar si ya tiene un formato de cuota tipo (1/12)
            if re.search(r'\(\d+/\d+\)', conc_base):
                filas.append(r)
                continue

            for i in range(1, c + 1):
                r_new = r.copy()
                r_new["Fecha"] = fmt_fecha(add_months(f_ini, i - 1))
                r_new["Monto"] = monto_cuota
                r_new["Cuanto recupero"] = recup_cuota
                r_new["Concepto"] = f"{conc_base} ({i}/{c})"
                r_new["Cuotas"] = c
                filas.append(r_new)
        else:
            filas.append(r)
    return pd.DataFrame(filas)

_MESES_ES = {
    "ene":"01","feb":"02","mar":"03","abr":"04","may":"05","jun":"06",
    "jul":"07","ago":"08","sep":"09","oct":"10","nov":"11","dic":"12",
}

def _parsear_fecha_es(s):
    s = str(s).strip()
    if not s or s.lower() in ("nat","nan","none","s/f","","pd.nat"):
        return None
    s_lower = s.lower()
    for mes_es, mes_num in _MESES_ES.items():
        if f"-{mes_es}-" in s_lower:
            try:
                partes = s_lower.split("-")
                dia = partes[0].zfill(2)
                anio = partes[2][:4]
                return f"{anio}-{mes_num}-{dia}"
            except: pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except: pass
    try:
        r = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isnull(r): return None
        return r.strftime("%Y-%m-%d")
    except:
        return None

def fmt_fecha(d):
    try:
        if pd.isnull(d): return str(date.today())
    except (TypeError, ValueError): pass
    if d is None: return str(date.today())
    if isinstance(d, (date, datetime)): return d.strftime("%Y-%m-%d")
    parsed = _parsear_fecha_es(d)
    return parsed if parsed else str(date.today())

def normalizar_fecha_existente(s):
    parsed = _parsear_fecha_es(s)
    return parsed if parsed else ""

_VALORES_NULOS_LITERALES = {"none", "nan", "nat", "<na>", "null"}

def _limpiar_nulos_literales(df):
    for col in df.columns:
        if df[col].dtype != object: continue
        mask = df[col].astype(str).str.strip().str.lower().isin(_VALORES_NULOS_LITERALES)
        if mask.any(): df.loc[mask, col] = ""
    return df

def load(key):
    f, cols = FILES[key]
    if os.path.exists(f):
        df = pd.read_csv(f, dtype=str).fillna("")
        df = _limpiar_nulos_literales(df)
        for c in cols:
            if c not in df.columns: df[c] = ""
        df = df[cols]
        if "Fecha" in df.columns:
            df["Fecha"] = df["Fecha"].apply(normalizar_fecha_existente)
        return df
    return pd.DataFrame(columns=cols)

def save(key, df):
    f, _ = FILES[key]
    df = _limpiar_nulos_literales(df.copy())
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
    if df.empty or "Fecha" not in df.columns: return df
    fechas = pd.to_datetime(df["Fecha"], errors="coerce")
    return df[(fechas.dt.year == y) & (fechas.dt.month == m)].copy()

def fmt_ars(n):
    try:
        return f"${float(n):,.0f}".replace(",", ".")
    except:
        return "$0"

def emoji_cat(cat):
    s = str(cat)
    return s.split(" ")[0] if " " in s else "💳"

def safe_int(val, default=1):
    try:
        v = str(val).strip()
        if v in ("", "nan", "None", "N/A", "none"): return default
        return int(float(v))
    except:
        return default

def get_tarjetas_nombres():
    nombres = list(TARJETAS_DEFAULT)
    t_df = load("tarjetas")
    if not t_df.empty:
        for n in t_df["Nombre"].dropna().tolist():
            n = str(n).strip()
            if n and n not in nombres: nombres.append(n)
    g_df = load("gastos")
    if not g_df.empty and "Tarjeta" in g_df.columns:
        for t in g_df["Tarjeta"].dropna().unique():
            t = str(t).strip()
            if t and t not in ("nan", "None", "") and t not in nombres:
                nombres.append(t)
    return nombres

def _generar_fechas_cierre(tarjeta_row, rango_dias=400):
    proximo_raw = str(tarjeta_row.get("Proximo cierre", "")).strip()
    if not proximo_raw or proximo_raw.lower() in ("nan","none","s/f",""): return None
    try:
        proximo = datetime.strptime(proximo_raw[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        try: proximo = pd.to_datetime(proximo_raw, dayfirst=True).date()
        except Exception: return None

    anterior_raw = str(tarjeta_row.get("Cierre anterior", "")).strip()
    anterior = None
    if anterior_raw and anterior_raw.lower() not in ("nan","none","s/f",""):
        try: anterior = datetime.strptime(anterior_raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            try: anterior = pd.to_datetime(anterior_raw, dayfirst=True).date()
            except Exception: anterior = None

    if anterior and anterior < proximo:
        intervalo = (proximo - anterior).days
    else:
        intervalo = safe_int(tarjeta_row.get("Dias entre cierres", 31), 31)
    if intervalo <= 0: intervalo = 31

    hoy = date.today()
    fechas = [proximo]
    if anterior: fechas.append(anterior)
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
    mes_ant, año_ant = (12, año-1) if mes == 1 else (mes-1, año)
    ultimo_mes_ant = calendar.monthrange(año_ant, mes_ant)[1]
    inicio = date(año_ant, mes_ant, min(dia_cierre+1, ultimo_mes_ant))
    fin = date(año, mes, min(dia_cierre, calendar.monthrange(año, mes)[1]))
    return inicio, fin

def periodo_actual_de_gasto(fecha_str, tarjeta_nombre):
    s = str(fecha_str).strip()
    if not s or s.lower() in ("s/f", "nan", "nat", "none", ""):
        hoy = date.today(); return hoy.year, hoy.month
    fg = None
    try: fg = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError): pass
    if fg is None:
        try: fg = pd.to_datetime(s, dayfirst=True).date()
        except Exception: hoy = date.today(); return hoy.year, hoy.month
        
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
    if fg.day <= dia_cierre:
        return fg.year, fg.month
    return (fg.year+1, 1) if fg.month == 12 else (fg.year, fg.month+1)

def filtrar_gastos_tarjeta_periodo(gastos_df, tarjeta_nombre, año_periodo, mes_periodo):
    if gastos_df.empty: return gastos_df.copy()
    rows = []
    for _, r in gastos_df.iterrows():
        if str(r.get("Tarjeta","")).strip() == tarjeta_nombre:
            notas = str(r.get("Notas", ""))
            per_match = re.search(r'\[Per:(\d{4})-(\d{1,2})\]', notas)
            if per_match:
                ay = int(per_match.group(1))
                am = int(per_match.group(2))
            else:
                ay, am = periodo_actual_de_gasto(r.get("Fecha",""), tarjeta_nombre)
            if ay == año_periodo and am == mes_periodo:
                rows.append(r)
    return pd.DataFrame(rows, columns=gastos_df.columns) if rows else pd.DataFrame(columns=gastos_df.columns)

def get_color_tarjeta(tname, tarjetas_df):
    if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
        c = str(tarjetas_df[tarjetas_df["Nombre"]==tname].iloc[0].get("Color","#7c6af7"))
        return c if c.startswith("#") else "#7c6af7"
    idx = TARJETAS_DEFAULT.index(tname) if tname in TARJETAS_DEFAULT else 0
    return COLORES_TARJETA[idx % len(COLORES_TARJETA)]

def _normalizar_texto(s):
    s = str(s).strip().lower()
    s = " ".join(s.split())
    return s

def limpiar_csv_montos(texto_csv):
    import re
    lineas = texto_csv.strip().split("\n")
    if not lineas: return texto_csv
    resultado = [lineas[0]]
    for linea in lineas[1:]:
        if not linea.strip(): continue
        linea_fix = re.sub(r'"(\d{1,3}(?:,\d{3})+\.\d+)"', lambda m: m.group(1).replace(",", ""), linea)
        linea_fix = re.sub(r',(\d{1,3}),(\d{3}\.\d+)', r',\1\2', linea_fix)
        resultado.append(linea_fix)
    return "\n".join(resultado)

def es_duplicado(fecha_str, concepto, monto, tarjeta, gastos_existentes):
    if gastos_existentes.empty: return False
    concepto_norm = _normalizar_texto(concepto)
    tarjeta_norm  = _normalizar_texto(tarjeta)
    fecha_norm    = _normalizar_texto(fecha_str)
    try: monto_f = float(monto)
    except (ValueError, TypeError): return False

    existentes = gastos_existentes.copy()
    existentes["_concepto_norm"] = existentes["Concepto"].apply(_normalizar_texto)
    existentes["_tarjeta_norm"]  = existentes["Tarjeta"].apply(_normalizar_texto)
    existentes["_fecha_norm"]    = existentes["Fecha"].apply(_normalizar_texto)

    candidatos = existentes[
        (existentes["_concepto_norm"] == concepto_norm) &
        (existentes["_tarjeta_norm"]  == tarjeta_norm) &
        (existentes["_fecha_norm"]    == fecha_norm)
    ]
    if candidatos.empty: return False
    for _, r in candidatos.iterrows():
        try: monto_existente = float(r.get("Monto", 0))
        except (ValueError, TypeError): continue
        if abs(monto_existente - monto_f) < 1.0:
            return True
    return False

# ── CSS ────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Biyuyo", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');
*,*::before,*::after{box-sizing:border-box}
html,body,[class*="css"],.stApp{font-family:'DM Sans',sans-serif!important;background-color:#080810!important;color:#dde0f0!important;-webkit-font-smoothing:antialiased}
#MainMenu,header,footer{visibility:hidden}
.block-container{padding:0 0 6rem!important;max-width:430px!important;margin:0 auto!important}
.app-header{background:#080810;padding:1rem 1rem 0.5rem;position:sticky;top:0;z-index:100;border-bottom:1px solid #14141e}
.app-brand{font-family:'DM Mono',monospace;font-size:1.1rem;font-weight:500;color:#6c63ff;letter-spacing:-0.02em}
.app-brand span{color:#dde0f0}
[data-baseweb="tab-list"]{background:#0d0d18!important;border-radius:0!important;padding:0 0.8rem!important;gap:0!important;border-bottom:1px solid #14141e!important;border-top:none!important;border-left:none!important;border-right:none!important;overflow-x:auto!important}
[data-baseweb="tab"]{border-radius:0!important;font-size:0.75rem!important;font-weight:500!important;padding:0.8rem 0.9rem!important;color:#555!important;background:transparent!important;border-bottom:2px solid transparent!important;white-space:nowrap!important}
[aria-selected="true"][data-baseweb="tab"]{background:transparent!important;color:#dde0f0!important;border-bottom:2px solid #6c63ff!important}
[data-testid="stTabContent"]{padding:0!important}
.hero-block{padding:1.5rem 1rem 1rem;border-bottom:1px solid #14141e}
.hero-eyebrow{font-size:0.65rem;font-weight:600;text-transform:uppercase;letter-spacing:0.12em;color:#444;margin-bottom:0.25rem}
.hero-num{font-family:'DM Mono',monospace;font-size:3rem;font-weight:500;line-height:1;letter-spacing:-0.03em}
.hero-sub{font-size:0.75rem;color:#444;margin-top:0.4rem}
.c-pos{color:#39e07a}.c-neg{color:#ff5f7e}.c-neu{color:#6c63ff}.c-yel{color:#f5c542}.c-dim{color:#555}
.stat-row{display:flex;border-bottom:1px solid #14141e}
.stat-cell{flex:1;padding:0.9rem 1rem;border-right:1px solid #14141e}
.stat-cell:last-child{border-right:none}
.stat-label{font-size:0.63rem;text-transform:uppercase;letter-spacing:0.1em;color:#444;margin-bottom:0.2rem}
.stat-val{font-family:'DM Mono',monospace;font-size:1.1rem;font-weight:500;letter-spacing:-0.02em}
.tarjeta-row{display:flex;align-items:center;padding:0.9rem 1rem;border-bottom:1px solid #14141e;gap:0.75rem}
.tarjeta-pip{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.tarjeta-label{font-size:0.82rem;font-weight:500;color:#bbb;flex:1}
.tarjeta-meta-small{font-size:0.68rem;color:#444}
.tarjeta-amount{font-family:'DM Mono',monospace;font-size:0.92rem;font-weight:500;letter-spacing:-0.02em}
.tarjeta-bar-bg{width:60px;height:3px;background:#1a1a28;border-radius:99px;overflow:hidden;flex-shrink:0}
.tarjeta-bar-fill{height:100%;border-radius:99px}
.tx{display:flex;align-items:center;padding:0.85rem 1rem;border-bottom:1px solid #14141e;gap:0.75rem}
.tx-ico{width:34px;height:34px;background:#12121e;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0}
.tx-main{flex:1;min-width:0}
.tx-name{font-size:0.84rem;font-weight:500;color:#dde0f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tx-info{font-size:0.68rem;color:#444;margin-top:1px}
.tx-amt{font-family:'DM Mono',monospace;font-size:0.88rem;font-weight:500;text-align:right;flex-shrink:0;letter-spacing:-0.02em}
.sec{padding:1.1rem 1rem 0.4rem;font-size:0.63rem;font-weight:600;text-transform:uppercase;letter-spacing:0.12em;color:#333}
.prog-wrap{padding:0.7rem 1rem;border-bottom:1px solid #14141e}
.prog-head{display:flex;justify-content:space-between;font-size:0.78rem;margin-bottom:0.4rem;color:#bbb}
.prog-head span:last-child{font-family:'DM Mono',monospace;font-size:0.75rem;color:#666}
.prog-bg{background:#12121e;border-radius:99px;height:4px;overflow:hidden}
.prog-fill{height:100%;border-radius:99px}
.prog-note{font-size:0.65rem;color:#444;margin-top:0.3rem}
.per-badge{display:inline-flex;align-items:center;gap:4px;background:#0d0d18;border:1px solid #1e1e30;border-radius:6px;padding:3px 9px;font-size:0.68rem;color:#555;margin:0.5rem 1rem}
.per-badge.open{border-color:#39e07a33;color:#39e07a}
.per-badge.closed{border-color:#ff5f7e33;color:#ff5f7e}
.per-badge.future{border-color:#6c63ff33;color:#6c63ff}
.empty{text-align:center;padding:2.5rem 1rem;color:#333;font-size:0.82rem;border-bottom:1px solid #14141e}
.empty big{display:block;font-size:1.8rem;margin-bottom:0.5rem;opacity:0.4}
.chip-next{display:inline-block;background:#6c63ff15;border:1px solid #6c63ff30;color:#6c63ff;border-radius:4px;font-size:0.6rem;padding:1px 5px;margin-left:5px;vertical-align:middle;font-family:'DM Mono',monospace}
div[data-testid="stButton"]>button,div[data-testid="stFormSubmitButton"]>button{background:#6c63ff!important;color:#fff!important;border:none!important;border-radius:8px!important;font-weight:600!important;font-size:0.82rem!important;padding:0.5rem 1rem!important;width:100%!important;font-family:'DM Sans',sans-serif!important;transition:background 0.15s!important}
div[data-testid="stButton"]>button:hover,div[data-testid="stFormSubmitButton"]>button:hover{background:#5a52e0!important;color:#fff!important}
.stTextInput input,.stNumberInput input,.stDateInput input,.stSelectbox div[data-baseweb="select"]>div,.stTextArea textarea{background:#0d0d18!important;border:1px solid #1e1e30!important;border-radius:8px!important;color:#dde0f0!important;font-size:0.88rem!important;font-family:'DM Sans',sans-serif!important}
.stTextInput input:focus,.stNumberInput input:focus,.stTextArea textarea:focus{border-color:#6c63ff!important;box-shadow:0 0 0 3px #6c63ff18!important}
label[data-testid="stWidgetLabel"] p{font-size:0.68rem!important;font-weight:600!important;color:#444!important;text-transform:uppercase!important;letter-spacing:0.08em!important}
[data-testid="stExpander"]{background:#0a0a14!important;border:1px solid #14141e!important;border-radius:10px!important;margin:0 1rem 0.5rem!important}
[data-testid="stForm"]{background:#0a0a14!important;border:1px solid #14141e!important;border-radius:12px!important;padding:1rem!important;margin:0.5rem 1rem!important}
.stRadio>div{gap:0.5rem!important}
.stRadio label{font-size:0.82rem!important;color:#888!important}
.total-strip{display:flex;justify-content:space-between;align-items:center;padding:0.8rem 1rem;background:#0a0a14;border-top:1px solid #14141e;border-bottom:1px solid #14141e;margin-top:0.2rem}
.total-strip-label{font-size:0.68rem;text-transform:uppercase;letter-spacing:0.1em;color:#444}
.total-strip-val{font-family:'DM Mono',monospace;font-size:1rem;font-weight:500}
[data-testid="stDataEditor"]{border:1px solid #1e1e30!important;border-radius:8px!important;overflow:hidden!important;margin:0 1rem!important}
.acciones-panel{background:#0d0d18;border-bottom:2px solid #6c63ff;padding:0.75rem 1rem 0;margin-bottom:0}
.info-strip{background:#6c63ff10;border-left:2px solid #6c63ff;padding:0.6rem 1rem;font-size:0.75rem;color:#888;margin:0.5rem 1rem;border-radius:0 6px 6px 0}
.pend-row{display:flex;align-items:center;padding:0.85rem 1rem;border-bottom:1px solid #14141e;gap:0.75rem;background:#f5c54208}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "gasto_limit" not in st.session_state: st.session_state.gasto_limit = 30
if "menu_accion" not in st.session_state: st.session_state.menu_accion = False

gastos_df   = load("gastos")
ingresos_df = load("ingresos")
comp_df     = load("compartidos")
inv_df      = load("inversiones")
pres_df     = load("presupuesto")
tarjetas_df = load("tarjetas")

gastos_df["Monto"]          = to_num(gastos_df["Monto"])
gastos_df["Cuanto recupero"]= to_num(gastos_df["Cuanto recupero"])
ingresos_df["Monto"]        = to_num(ingresos_df["Monto"])
comp_df["Monto"]            = to_num(comp_df["Monto"])

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

gastos_df   = sort_by_fecha(gastos_df)
gastos_mes  = filtrar_mes(gastos_df, y, m)
ingresos_mes= filtrar_mes(ingresos_df, y, m)

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
    <div style='font-size:0.6rem;color:#333;font-family:"DM Mono",monospace'>build 2026-06-17-v2</div>
  </div>
</div>""", unsafe_allow_html=True)

# ── BOTÓN AGREGAR ─────────────────────────────────────────────────────────────
_, col_r = st.columns([3,1])
with col_r:
    if st.button("✕ Cerrar" if st.session_state.menu_accion else "+ Agregar", key="fab"):
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
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Guardar gasto"):
                if q_c.strip() and q_m > 0:
                    fecha_str = fmt_fecha(q_f)
                    conc_usd, monto_final = procesar_usd(q_c.strip(), q_m)
                    
                    nv = pd.DataFrame([[fecha_str, conc_usd, monto_final, q_t, q_cu, q_k, "No", "", 0, ""]],
                                      columns=FILES["gastos"][1])
                    
                    # Expansión de cuotas manual (divide el total)
                    nv = expandir_filas_cuotas(nv, es_manual=True)
                    
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
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
                    nv = pd.DataFrame([[fmt_fecha(i_f), i_c.strip(), i_m, i_k]],
                                      columns=ingresos_df.columns)
                    ingresos_df = pd.concat([ingresos_df, nv], ignore_index=True)
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
            st.markdown(f"<div style='width:18px;height:18px;border-radius:50%;background:{t_col};margin-top:4px'></div>", unsafe_allow_html=True)
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Crear tarjeta"):
                if t_n.strip():
                    base = tarjetas_df[tarjetas_df["Nombre"] != t_n.strip()].copy()
                    fila = pd.DataFrame([[t_n.strip(), t_dc, t_dv, t_col, "", "", 31]],
                                        columns=["Nombre","Dia cierre","Dia vencimiento","Color","Cierre anterior","Proximo cierre","Dias entre cierres"])
                    tarjetas_df = pd.concat([base, fila], ignore_index=True)
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
    color_hero = "c-pos" if remanente >= 0 else "c-neg"
    st.markdown(
        "<div class='hero-block'>"
        f"<div class='hero-eyebrow'>{nombre_mes} {y} · remanente</div>"
        f"<div class='hero-num {color_hero}'>{fmt_ars(remanente)}</div>"
        "<div class='hero-sub'>ingresos − gastos + recupero</div>"
        "</div>", unsafe_allow_html=True)

    recup_cell = ""
    if recupero > 0:
        recup_cell = (
            "<div class='stat-cell'>"
            "<div class='stat-label'>Recuperás</div>"
            f"<div class='stat-val c-yel'>{fmt_ars(recupero)}</div>"
            "</div>"
        )
    st.markdown(
        "<div class='stat-row'>"
        f"<div class='stat-cell'><div class='stat-label'>Entró</div><div class='stat-val c-pos'>{fmt_ars(total_ing)}</div></div>"
        f"<div class='stat-cell'><div class='stat-label'>Salió</div><div class='stat-val c-neg'>{fmt_ars(total_gast)}</div></div>"
        f"{recup_cell}"
        "</div>", unsafe_allow_html=True)

    gastos_fresh = load("gastos")
    gastos_fresh["Monto"] = to_num(gastos_fresh["Monto"])
    gastos_fresh = sort_by_fecha(gastos_fresh)

    st.markdown("<div class='sec'>Este período (según cierre de cada tarjeta)</div>", unsafe_allow_html=True)
    tarjetas_con_gasto = {}
    configuradas = tarjetas_df["Nombre"].dropna().tolist() if not tarjetas_df.empty else []
    
    for tname in TARJETAS:
        gf = filtrar_gastos_tarjeta_periodo(gastos_fresh, tname, y, m)
        total_t = gf["Monto"].sum() if not gf.empty else 0
        # Ahora se muestran también tarjetas configuradas a $0
        if total_t > 0 or tname in configuradas:
            tarjetas_con_gasto[tname] = total_t

    if tarjetas_con_gasto:
        max_t = max([v for v in tarjetas_con_gasto.values()] + [0])
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = get_color_tarjeta(tname, tarjetas_df)
            pct = int(total_t / max_t * 100) if max_t > 0 else 0
            meta_html = ""
            if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
                dc = safe_int(tarjetas_df[tarjetas_df["Nombre"]==tname].iloc[0].get("Dia cierre",""), 0)
                if dc:
                    meta_html = f"<div class='tarjeta-meta-small'>cierra día {dc}</div>"
            bar_fill = f"<div class='tarjeta-bar-fill' style='width:{pct}%;background:{color}'></div>"
            st.markdown(
                "<div class='tarjeta-row'>"
                f"<div class='tarjeta-pip' style='background:{color}'></div>"
                f"<div style='flex:1'><div class='tarjeta-label'>{tname}</div>{meta_html}</div>"
                f"<div class='tarjeta-bar-bg'>{bar_fill}</div>"
                f"<div class='tarjeta-amount c-neg'>{fmt_ars(total_t)}</div>"
                "</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='empty'><big>💸</big>Sin gastos este mes.</div>", unsafe_allow_html=True)

    st.markdown("<div class='sec'>Últimos movimientos</div>", unsafe_allow_html=True)
    _tiene_fecha = pd.to_datetime(gastos_fresh["Fecha"], errors="coerce").notna()
    _con_fecha   = gastos_fresh[_tiene_fecha].head(8)
    _sin_fecha   = gastos_fresh[~_tiene_fecha].head(max(0, 8 - len(_con_fecha)))
    recientes    = pd.concat([_con_fecha, _sin_fecha], ignore_index=True)
    if recientes.empty:
        st.markdown("<div class='empty'><big>📋</big>Sin movimientos todavía.</div>", unsafe_allow_html=True)
    else:
        for _, r in recientes.iterrows():
            ico = emoji_cat(str(r.get("Categoria","💳")))
            fecha_str = normalizar_fecha_existente(r.get("Fecha","")) or "sin fecha"
            tname_r = str(r.get("Tarjeta",""))
            cuotas_v = safe_int(r.get("Cuotas",1), 1)
            cuotas_t = f" · {cuotas_v}c" if cuotas_v > 1 else ""
            chip = ""
            if not tarjetas_df.empty and tname_r in tarjetas_df["Nombre"].values:
                # Comprobación de si está forzado a un periodo particular
                notas_r = str(r.get("Notas", ""))
                per_match = re.search(r'\[Per:(\d{4})-(\d{1,2})\]', notas_r)
                if per_match:
                    ay, am = int(per_match.group(1)), int(per_match.group(2))
                else:
                    ay, am = periodo_actual_de_gasto(r.get("Fecha",""), tname_r)
                    
                if ay != y or am != m:
                    chip = f"<span class='chip-next'>→{calendar.month_name[am][:3]}</span>"
            st.markdown(
                "<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}{chip}</div>"
                f"<div class='tx-info'>{fecha_str} · {tname_r}{cuotas_t}</div>"
                "</div>"
                f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                "</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — GASTOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    with st.expander("📥 Importar desde CSV / foto de resumen"):
        st.markdown(
            "<div class='info-strip'>Pasale tus capturas a la IA, pegá el CSV acá. El código ahora procesa dólares (USD) al cambio vendedor automáticamente y soporta forzar el período para que no se escapen movimientos.</div>",
            unsafe_allow_html=True
        )
        csv_text = st.text_area("", placeholder="Fecha,Concepto,Monto,Cuotas\n2026-05-24,MERPAGO,16476.46,1\n...", height=140, label_visibility="collapsed", key="csv_import_text")
        tarjeta_import = st.selectbox("Tarjeta (si el CSV no la trae)", TARJETAS, key="tarjeta_import_sel")

        periodos_posibles = []
        for delta in range(-3, 3):
            pm, py = m+delta, y
            while pm <= 0: pm+=12; py-=1
            while pm > 12: pm-=12; py+=1
            periodos_posibles.append((py, pm))
            
        opc_periodos = ["Automático (según fecha)"] + [f"{py}-{pm:02d}" for py, pm in periodos_posibles]
        per_forzado = st.selectbox("Forzar período de este extracto", opc_periodos, index=0)

        if st.button("🔍 Previsualizar", key="preview_csv"):
            if csv_text.strip():
                try:
                    csv_limpio = limpiar_csv_montos(csv_text)
                    nuevos = pd.read_csv(io.StringIO(csv_limpio), dtype=str).fillna("")
                    nuevos.columns = [c.strip() for c in nuevos.columns]

                    if "Tarjeta" not in nuevos.columns:
                        nuevos["Tarjeta"] = tarjeta_import
                    else:
                        tarjeta_vacia_mask = nuevos["Tarjeta"].astype(str).str.strip().str.lower().isin(
                            {"", "none", "nan", "nat", "null", "<na>"}
                        )
                        nuevos.loc[tarjeta_vacia_mask, "Tarjeta"] = tarjeta_import

                    for col in FILES["gastos"][1]:
                        if col not in nuevos.columns:
                            nuevos[col] = "0" if col in ["Monto","Cuanto recupero"] else ("No" if col=="Compartido" else "")
                    nuevos = nuevos[FILES["gastos"][1]]
                    
                    # Convertimos USD si hay
                    for idx, row_cv in nuevos.iterrows():
                        c_usd, m_usd = procesar_usd(row_cv["Concepto"], row_cv["Monto"])
                        nuevos.at[idx, "Concepto"] = c_usd
                        nuevos.at[idx, "Monto"] = m_usd

                    nuevos["Fecha"] = nuevos["Fecha"].apply(normalizar_fecha_existente)
                    nuevos["Monto"] = to_num(nuevos["Monto"])

                    PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior", "pago tarjeta"]
                    concepto_lower = nuevos["Concepto"].astype(str).str.strip().str.lower()
                    es_pago_mask = concepto_lower.isin(PALABRAS_EXCLUIR) | nuevos["Monto"].astype(float).le(0)
                    excluidos_count = int(es_pago_mask.sum())
                    nuevos = nuevos[~es_pago_mask].copy()

                    # Forzar período si el usuario lo seleccionó
                    if per_forzado != "Automático (según fecha)":
                        for idx in nuevos.index:
                            n_actual = str(nuevos.at[idx, "Notas"]).strip()
                            nuevos.at[idx, "Notas"] = f"{n_actual} [Per:{per_forzado}]".strip()

                    gastos_actuales = load("gastos")
                    gastos_actuales["Monto"] = to_num(gastos_actuales["Monto"])
                    gastos_actuales["Fecha"] = gastos_actuales["Fecha"].apply(normalizar_fecha_existente)

                    es_dup_mask = nuevos.apply(
                        lambda r: es_duplicado(r["Fecha"], r["Concepto"], r["Monto"], r["Tarjeta"], gastos_actuales),
                        axis=1
                    )
                    nuevos_filtrados = nuevos[~es_dup_mask].copy()
                    duplicados_count = int(es_dup_mask.sum())

                    st.session_state["_csv_preview"] = nuevos_filtrados
                    st.session_state["_csv_dup_count"] = duplicados_count
                    st.session_state["_csv_excl_count"] = excluidos_count
                except Exception as e:
                    st.error(f"No pude leer el CSV: {e}")
                    st.session_state.pop("_csv_preview", None)
            else:
                st.warning("Pegá el CSV primero.")

        if "_csv_preview" in st.session_state:
            preview = st.session_state["_csv_preview"]
            dup_count = st.session_state.get("_csv_dup_count", 0)
            excl_count = st.session_state.get("_csv_excl_count", 0)

            if excl_count > 0:
                st.markdown(f"<div class='info-strip'>🚫 {excl_count} fila(s) excluida(s) (pagos o $0).</div>", unsafe_allow_html=True)
            if dup_count > 0:
                st.markdown(f"<div class='info-strip'>⏭️ {dup_count} duplicado(s) omitido(s).</div>", unsafe_allow_html=True)

            if preview.empty:
                st.markdown("<div class='empty'><big>✅</big>Nada nuevo para importar.</div>", unsafe_allow_html=True)
            else:
                st.caption(f"{len(preview)} movimiento(s) nuevo(s) para importar:")
                for _, r in preview.iterrows():
                    st.markdown(
                        "<div class='tx'>"
                        f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                        "<div class='tx-main'>"
                        f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                        f"<div class='tx-info'>{str(r.get('Fecha',''))[:10] or 'sin fecha'} · {r.get('Tarjeta','')}</div>"
                        "</div>"
                        f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                        "</div>", unsafe_allow_html=True)

                if st.button(f"✅ Confirmar e importar", key="confirm_import"):
                    base = load("gastos")
                    base["Monto"] = to_num(base["Monto"])
                    
                    # Generamos cuotas desde el CSV (manteniendo valor porque el CSV ya trae la cuota separada)
                    preview_expandido = expandir_filas_cuotas(preview, es_manual=False)
                    
                    final = pd.concat([base, preview_expandido], ignore_index=True)
                    final = sort_by_fecha(final)
                    save("gastos", final)
                    st.session_state.pop("_csv_preview", None)
                    st.session_state.pop("_csv_dup_count", None)
                    st.success(f"✅ Movimientos importados exitosamente.")
                    st.rerun()

    with st.expander("✏️ Carga manual"):
        with st.form("f_gasto_full", clear_on_submit=True):
            g_c = st.text_input("Concepto", placeholder="Ej: almuerzo, nafta, cuota…")
            c1,c2 = st.columns(2)
            g_m  = c1.number_input("Monto $", min_value=0.0, step=500.0)
            g_cu = c2.number_input("Cuotas", min_value=1, max_value=48, value=1)
            c3,c4 = st.columns(2)
            g_t = c3.selectbox("Tarjeta", TARJETAS)
            g_k = c4.selectbox("Categoría", CAT_GASTOS)
            c5,c6 = st.columns(2)
            g_f    = c5.date_input("Fecha", value=date.today())
            g_comp = c6.selectbox("Compartido", ["No","Sí"])
            c7,c8 = st.columns(2)
            g_quien = c7.text_input("Con quién", placeholder="Nombre")
            g_rec   = c8.number_input("Recuperás $", min_value=0.0, step=100.0) if g_comp == "Sí" else 0.0
            g_nota  = st.text_input("Nota", placeholder="Opcional")
            if st.form_submit_button("Guardar gasto"):
                if g_c.strip() and g_m > 0:
                    fecha_str = fmt_fecha(g_f)
                    conc_usd, monto_final = procesar_usd(g_c.strip(), g_m)

                    nv = pd.DataFrame([[fecha_str, conc_usd, monto_final, g_t, g_cu, g_k, g_comp, g_quien, g_rec, g_nota]],
                                      columns=FILES["gastos"][1])
                    
                    # Expansión de cuotas manual (divide el total)
                    nv = expandir_filas_cuotas(nv, es_manual=True)

                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    gastos_df = sort_by_fecha(gastos_df)
                    save("gastos", gastos_df)
                    if g_comp == "Sí" and g_rec > 0:
                        nvc = pd.DataFrame([[fecha_str, conc_usd, g_rec, g_quien, "Pendiente", ""]],
                                           columns=comp_df.columns)
                        comp_df2 = pd.concat([comp_df, nvc], ignore_index=True)
                        comp_df2["Monto"] = to_num(comp_df2["Monto"])
                        save("compartidos", comp_df2)
                    st.success(f"Guardado.")
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")

    with st.expander("🗑️ Eliminar gastos"):
        del_q = st.text_input("Buscar concepto", key="del_g", placeholder="Parte del concepto…")
        if del_q:
            cands = gastos_df[gastos_df["Concepto"].str.contains(del_q, case=False, na=False)]
            if cands.empty:
                st.caption("Sin resultados.")
            else:
                for idx, r in cands.iterrows():
                    ca,cb = st.columns([5,1])
                    ca.markdown(f"**{r['Concepto']}** · {r['Fecha']} · {fmt_ars(r['Monto'])}")
                    with cb:
                        if st.button("✕", key=f"dg_{idx}"):
                            gastos_df = gastos_df.drop(index=idx).reset_index(drop=True)
                            save("gastos", gastos_df)
                            st.rerun()

    st.markdown("<div class='sec'>Todos los movimientos</div>", unsafe_allow_html=True)
    if gastos_df.empty:
        st.markdown("<div class='empty'><big>📋</big>Nada cargado todavía.</div>", unsafe_allow_html=True)
    else:
        for _, r in gastos_df.head(st.session_state.gasto_limit).iterrows():
            ico = emoji_cat(str(r.get("Categoria","💳")))
            fstr = str(r.get("Fecha",""))[:10]
            cuotas_v = safe_int(r.get("Cuotas",1), 1)
            cuotas_t = f" · {cuotas_v}c" if cuotas_v > 1 else ""
            comp_t   = f" · {r.get('Con quien','')}" if str(r.get("Compartido","No")) == "Sí" else ""
            st.markdown(
                "<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                f"<div class='tx-info'>{fstr} · {r.get('Tarjeta','')}{cuotas_t}{comp_t}</div>"
                "</div>"
                f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                "</div>", unsafe_allow_html=True)
        if len(gastos_df) > st.session_state.gasto_limit:
            if st.button("Ver más ▼", key="mas_g"):
                st.session_state.gasto_limit += 25
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TARJETAS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    tarjetas_df = load("tarjetas")

    st.markdown("<div class='sec'>Configuración de tarjetas</div>", unsafe_allow_html=True)
    if tarjetas_df.empty:
        tarjetas_edit = pd.DataFrame(columns=["Nombre","Dia cierre","Dia vencimiento","Color","Cierre anterior","Proximo cierre","Dias entre cierres"])
    else:
        tarjetas_edit = tarjetas_df.copy()
    tarjetas_edit["Dia cierre"]          = pd.to_numeric(tarjetas_edit["Dia cierre"], errors="coerce").fillna(5).astype(int)
    tarjetas_edit["Dia vencimiento"]     = pd.to_numeric(tarjetas_edit["Dia vencimiento"], errors="coerce").fillna(20).astype(int)
    tarjetas_edit["Nombre"]              = tarjetas_edit["Nombre"].fillna("").astype(str)
    tarjetas_edit["Color"]               = tarjetas_edit["Color"].fillna("#7c6af7").astype(str)
    tarjetas_edit["Cierre anterior"]     = tarjetas_edit["Cierre anterior"].apply(
        lambda x: pd.to_datetime(x, errors="coerce").date() if str(x).strip() not in ("", "nan", "none", "s/f") else None
    )
    tarjetas_edit["Proximo cierre"]      = tarjetas_edit["Proximo cierre"].apply(
        lambda x: pd.to_datetime(x, errors="coerce").date() if str(x).strip() not in ("", "nan", "none", "s/f") else None
    )
    tarjetas_edit["Dias entre cierres"]  = pd.to_numeric(tarjetas_edit["Dias entre cierres"], errors="coerce").fillna(31).astype(int)

    edited_t = st.data_editor(
        tarjetas_edit,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Nombre":             st.column_config.TextColumn("Nombre"),
            "Dia cierre":         st.column_config.NumberColumn("Día cierre", min_value=1, max_value=31, step=1),
            "Dia vencimiento":    st.column_config.NumberColumn("Día vence", min_value=1, max_value=31, step=1),
            "Cierre anterior":    st.column_config.DateColumn("Cierre anterior (exacto)"),
            "Proximo cierre":     st.column_config.DateColumn("Próximo cierre (exacto)"),
            "Dias entre cierres": st.column_config.NumberColumn("Días entre cierres", min_value=20, max_value=45, step=1),
            "Color":              st.column_config.SelectboxColumn("Color", options=["#7c6af7","#4ade80","#f87171","#fbbf24","#60a5fa","#f472b6","#34d399","#fb923c"]),
        },
        key="editor_tarjetas"
    )
    if st.button("💾 Guardar tarjetas", key="save_t"):
        edited_limpio = edited_t.copy()
        edited_limpio["Nombre"] = edited_limpio["Nombre"].fillna("").astype(str).str.strip()
        edited_limpio = edited_limpio[edited_limpio["Nombre"] != ""].reset_index(drop=True)
        edited_limpio["Cierre anterior"] = edited_limpio["Cierre anterior"].apply(
            lambda x: fmt_fecha(x) if pd.notnull(x) else ""
        )
        edited_limpio["Proximo cierre"] = edited_limpio["Proximo cierre"].apply(
            lambda x: fmt_fecha(x) if pd.notnull(x) else ""
        )
        save("tarjetas", edited_limpio)
        st.success("Configuración guardada.")
        st.rerun()

    st.markdown("<div class='sec'>Gastos por tarjeta y período</div>", unsafe_allow_html=True)
    c1,c2 = st.columns(2)
    t_sel = c1.selectbox("Tarjeta", TARJETAS, key="t_sel_tab")

    periodos = []
    for delta in range(-5, 2):
        pm,py = m+delta, y
        while pm <= 0: pm+=12; py-=1
        while pm > 12: pm-=12; py+=1
        periodos.append((py,pm))

    opciones_per = []
    for py, pm in periodos:
        ini, fin = get_periodo_tarjeta(t_sel, py, pm)
        opciones_per.append(f"{calendar.month_name[pm][:3]} {py} · {ini.strftime('%d/%m')}→{fin.strftime('%d/%m')}")

    per_sel = c2.selectbox("Período", opciones_per, index=5, key="per_sel_tab")
    sel_py, sel_pm = periodos[opciones_per.index(per_sel)]

    inicio_p, fin_p = get_periodo_tarjeta(t_sel, sel_py, sel_pm)
    gastos_base = load("gastos")
    gastos_base["Monto"] = to_num(gastos_base["Monto"])
    gastos_base["Cuanto recupero"] = to_num(gastos_base["Cuanto recupero"])
    gastos_base["_row_id"] = range(len(gastos_base))
    df_per = filtrar_gastos_tarjeta_periodo(gastos_base, t_sel, sel_py, sel_pm)
    
    _key_ids = f"row_ids_{t_sel}_{sel_py}_{sel_pm}"
    if not df_per.empty:
        st.session_state[_key_ids] = list(df_per["_row_id"].astype(int))
    elif _key_ids not in st.session_state:
        st.session_state[_key_ids] = []
    total_per = to_num(df_per["Monto"]).sum() if not df_per.empty else 0

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
        unsafe_allow_html=True)

    color_t_sel = get_color_tarjeta(t_sel, tarjetas_df)
    st.markdown(
        "<div class='total-strip'>"
        f"<span class='total-strip-label'>{t_sel} · {per_sel}</span>"
        f"<span class='total-strip-val' style='color:{color_t_sel}'>−{fmt_ars(total_per)}</span>"
        "</div>", unsafe_allow_html=True)

    if not df_per.empty:
        df_ed = df_per.drop(columns=["_row_id"], errors="ignore").copy().reset_index(drop=True)
        df_ed["Fecha"]           = df_ed["Fecha"].apply(lambda x: pd.to_datetime(x, errors="coerce").date() if str(x) not in ("S/F","","nan") else None)
        df_ed["Monto"]           = pd.to_numeric(df_ed["Monto"], errors="coerce").fillna(0)
        df_ed["Cuotas"]          = pd.to_numeric(df_ed["Cuotas"], errors="coerce").fillna(1).astype(int)
        df_ed["Cuanto recupero"] = pd.to_numeric(df_ed["Cuanto recupero"], errors="coerce").fillna(0)
        df_ed["Concepto"]        = df_ed["Concepto"].fillna("").astype(str)
        df_ed["Tarjeta"]         = df_ed["Tarjeta"].fillna("").astype(str)
        df_ed["Categoria"]       = df_ed["Categoria"].fillna("💳 Otro").astype(str)
        df_ed["Compartido"]      = df_ed["Compartido"].fillna("No").astype(str)
        df_ed["Con quien"]       = df_ed["Con quien"].fillna("").astype(str)
        df_ed["Notas"]           = df_ed["Notas"].fillna("").astype(str)

        edited_per = st.data_editor(
            df_ed,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Fecha":           st.column_config.DateColumn("Fecha"),
                "Concepto":        st.column_config.TextColumn("Concepto"),
                "Monto":           st.column_config.NumberColumn("Monto $", format="$%d", min_value=0),
                "Cuotas":          st.column_config.NumberColumn("Cuotas", min_value=1, max_value=48, step=1),
                "Compartido":      st.column_config.TextColumn("Compartido"),
                "Con quien":       st.column_config.TextColumn("Con quién"),
                "Cuanto recupero": st.column_config.NumberColumn("Recupero $", format="$%d", min_value=0),
                "Notas":           st.column_config.TextColumn("Notas"),
                "Tarjeta":         st.column_config.TextColumn("Tarjeta"),
                "Categoria":       st.column_config.TextColumn("Categoría"),
            },
            key=f"editor_per_{t_sel}_{sel_py}_{sel_pm}"
        )

        if st.button("💾 Guardar cambios en gastos", key="save_per"):
            _key_ids = f"row_ids_{t_sel}_{sel_py}_{sel_pm}"
            ids_a_eliminar = set(st.session_state.get(_key_ids, []))

            base = load("gastos")
            base["Monto"]           = to_num(base["Monto"])
            base["Cuanto recupero"] = to_num(base["Cuanto recupero"])
            base["_row_id"]         = range(len(base))

            max_id = len(base) - 1
            ids_validos = {i for i in ids_a_eliminar if 0 <= i <= max_id}

            if not ids_validos and ids_a_eliminar:
                st.error("Error de sincronización. Recargá la página y volvé a intentar.")
                st.stop()

            base_limpia = base[~base["_row_id"].isin(ids_validos)].drop(columns=["_row_id"]).reset_index(drop=True)

            nuevas = edited_per.copy()
            nuevas["Fecha"]           = nuevas["Fecha"].apply(fmt_fecha)
            nuevas["Monto"]           = to_num(nuevas["Monto"])
            nuevas["Cuanto recupero"] = to_num(nuevas["Cuanto recupero"])
            for col in FILES["gastos"][1]:
                if col not in nuevas.columns:
                    nuevas[col] = ""
            nuevas = nuevas[FILES["gastos"][1]]

            final = pd.concat([base_limpia, nuevas], ignore_index=True)
            final = sort_by_fecha(final)
            save("gastos", final)
            if _key_ids in st.session_state:
                del st.session_state[_key_ids]
            st.success(f"✅ Guardado. {len(nuevas)} filas actualizadas.")
            st.rerun()
    else:
        st.markdown("<div class='empty'><big>💳</big>Sin gastos en este período.</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# RESTO DE TABS (SIN MODIFICAR, TAL CUAL FUNCIONABAN)
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
                nv = pd.DataFrame([[fmt_fecha(i_f), i_c.strip(), i_m, i_k]], columns=ingresos_df.columns)
                ingresos_df = pd.concat([ingresos_df, nv], ignore_index=True)
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
                "</div>", unsafe_allow_html=True)

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
                nv = pd.DataFrame([[fmt_fecha(co_f), co_c.strip(), co_m, co_q.strip(), "Pendiente", co_n]],
                                  columns=comp_df.columns)
                comp_df = pd.concat([comp_df, nv], ignore_index=True)
                comp_df["Monto"] = to_num(comp_df["Monto"])
                save("compartidos", comp_df)
                st.success(f"{co_q} te debe {fmt_ars(co_m)}")
                st.rerun()
            else:
                st.warning("Completá todos los campos.")

    comp_df = load("compartidos")
    comp_df["Monto"] = to_num(comp_df["Monto"])
    pends = comp_df[comp_df["Estado"] == "Pendiente"].sort_values("Fecha", ascending=False)
    
    st.markdown("<div class='sec'>Pendientes de cobrar</div>", unsafe_allow_html=True)
    if pends.empty:
        st.markdown("<div class='empty'><big>🎉</big>Todo cobrado.</div>", unsafe_allow_html=True)
    else:
        for idx, r in pends.iterrows():
            ca,cb = st.columns([5,2])
            with ca:
                st.markdown(
                    "<div class='pend-row'>"
                    "<div class='tx-ico'>🤝</div>"
                    "<div class='tx-main'>"
                    f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                    f"<div class='tx-info'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>"
                    "</div>"
                    f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Monto',0))}</div>"
                    "</div>", unsafe_allow_html=True)
            with cb:
                if st.button("✓ Cobrado", key=f"cob_{idx}"):
                    comp_df.at[idx, "Estado"] = "Cobrado"
                    save("compartidos", comp_df)
                    st.rerun()

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
                inv_df2 = load("inversiones")
                nv = pd.DataFrame([[fmt_fecha(inv_f), inv_n.strip(), inv_cap, inv_r, inv_mon, inv_nota]],
                                  columns=inv_df2.columns)
                inv_df2 = pd.concat([inv_df2, nv], ignore_index=True)
                save("inversiones", inv_df2)
                st.success(f"Registrado: {inv_n}")
                st.rerun()

    inv_df = load("inversiones")
    if not inv_df.empty:
        st.markdown("<div class='sec'>Portafolio</div>", unsafe_allow_html=True)
        for _, r in inv_df.sort_values("Fecha", ascending=False).iterrows():
            st.markdown(
                "<div class='tx'>"
                "<div class='tx-ico'>📈</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Instrumento','—')}</div>"
                f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]} · {r.get('Moneda','ARS')}</div>"
                "</div>"
                "<div style='text-align:right'>"
                f"<div class='tx-amt c-neu'>{fmt_ars(r.get('Capital',0))}</div>"
                "</div>"
                "</div>", unsafe_allow_html=True)

with tabs[6]:
    pres_df = load("presupuesto")
    st.markdown(f"<div class='sec'>Control — {nombre_mes} {y}</div>", unsafe_allow_html=True)
    if pres_df.empty:
        st.markdown("<div class='empty'><big>🎯</big>Configurá límites arriba.</div>", unsafe_allow_html=True)
