import streamlit as st
import pandas as pd
import os
from datetime import date, datetime, timedelta
import calendar
import io
import requests

# ── Archivos ───────────────────────────────────────────────────────────────────
FILES = {
    "gastos":      ("mis_gastos.csv",      ["Fecha","Concepto","Monto","Tarjeta","Cuotas","Categoria","Compartido","Con quien","Cuanto recupero","Notas"]),
    "ingresos":    ("mis_ingresos.csv",    ["Fecha","Concepto","Monto","Categoria"]),
    "compartidos": ("mis_compartidos.csv", ["Fecha","Concepto","Monto","Con quien","Estado","Notas"]),
    "inversiones": ("mis_inversiones.csv", ["Fecha","Instrumento","Capital","Rendimiento","Moneda","Notas"]),
    "presupuesto": ("mis_presupuesto.csv", ["Categoria","Limite"]),
    # "Proximo cierre" (fecha exacta YYYY-MM-DD) y "Dias entre cierres" (intervalo)
    # permiten reflejar tarjetas cuyo ciclo NO cae el mismo día fijo cada mes
    # (ej: Banco Hipotecario salta de 28/05 a 02/07). Si "Proximo cierre" está
    # vacío, se usa el modo simple con "Dia cierre" fijo (compatibilidad vieja).
    "tarjetas":    ("mis_tarjetas.csv",    ["Nombre","Dia cierre","Dia vencimiento","Color","Cierre anterior","Proximo cierre","Dias entre cierres"]),
}

CAT_GASTOS = ["🍔 Comida","🚗 Transporte","🎉 Salidas","✈️ Viaje","🏥 Salud",
               "👕 Ropa","📱 Servicios","🏠 Casa","💊 Farmacia","📚 Educación","🎁 Regalos","💳 Otro"]
CAT_ING    = ["💼 Sueldo","💻 Freelance","📈 Inversión","🎁 Regalo","💰 Otro"]
MONEDAS    = ["ARS","USD","EUR"]
COLORES_TARJETA = ["#7c6af7","#4ade80","#f87171","#fbbf24","#60a5fa","#f472b6","#34d399","#fb923c"]
# BUG 3 FIX: tarjetas por defecto siempre presentes
TARJETAS_DEFAULT = ["Visa ICBC","Visa Hipotecario","Master ICBC","Efectivo","Débito","Otro"]

# ── Helpers ────────────────────────────────────────────────────────────────────
_MESES_ES = {
    "ene":"01","feb":"02","mar":"03","abr":"04","may":"05","jun":"06",
    "jul":"07","ago":"08","sep":"09","oct":"10","nov":"11","dic":"12",
}

def _parsear_fecha_es(s):
    """Intenta parsear una fecha en cualquier formato, incluyendo mes en español.
    Devuelve string YYYY-MM-DD o None si no se pudo reconocer."""
    s = str(s).strip()
    if not s or s.lower() in ("nat","nan","none","s/f","","pd.nat"):
        return None

    s_lower = s.lower()
    # DD-MMM-YYYY con mes en español (05-jun-2026, 13-abr-2026)
    for mes_es, mes_num in _MESES_ES.items():
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
    """Para INPUTS NUEVOS (date_input del formulario): siempre devuelve una fecha,
    usa hoy como fallback porque el usuario está creando un gasto AHORA."""
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
    """Para DATOS YA GUARDADOS en el CSV: normaliza el formato si se puede,
    pero si no hay fecha reconocible, devuelve "" (vacío) para que quede
    correctamente al fondo en el ordenamiento, en vez de inventar la fecha de hoy."""
    parsed = _parsear_fecha_es(s)
    return parsed if parsed else ""

_VALORES_NULOS_LITERALES = {"none", "nan", "nat", "<na>", "null"}

def _limpiar_nulos_literales(df):
    """Convierte strings literales como 'None', 'nan', 'NaT' (que Streamlit
    puede llegar a persistir en el CSV al guardar filas vacías del data_editor)
    en strings vacíos reales. Sin esto, fillna('') no los detecta porque
    técnicamente no son NaN, son texto.
    Solo toca columnas de tipo texto (object) — las numéricas no aceptan
    asignación de string vacío y rompen con TypeError."""
    for col in df.columns:
        if df[col].dtype != object:
            continue  # columnas numéricas (float/int) no pueden recibir "" — se omiten
        mask = df[col].astype(str).str.strip().str.lower().isin(_VALORES_NULOS_LITERALES)
        if mask.any():
            df.loc[mask, col] = ""
    return df

def load(key):
    f, cols = FILES[key]
    if os.path.exists(f):
        df = pd.read_csv(f, dtype=str).fillna("")
        df = _limpiar_nulos_literales(df)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df = df[cols]
        # Normalizar SIEMPRE la columna Fecha al cargar, en cualquier archivo que
        # la tenga. Antes esto solo se hacía manualmente en algunos puntos del
        # código (después de cada load("gastos") suelto), y se olvidaba en otros
        # (ej. gastos_fresh = load("gastos") en home), dejando fechas crudas tipo
        # "11-Jun-2026" sin convertir a "2026-06-11" — eso rompía cualquier [:10]
        # que truncaba a la mitad, y también el cálculo de período por tarjeta.
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
    return s.split(" ")[0] if " " in s else "💳"

def safe_int(val, default=1):
    try:
        v = str(val).strip()
        if v in ("", "nan", "None", "N/A", "none"):
            return default
        return int(float(v))
    except:
        return default

def parsear_cuotas(val):
    """Interpreta el campo Cuotas en cualquiera de sus formatos posibles:
    - Número simple: "1", "3" -> (cuota_actual=1, total=ese número, sin info de actual)
    - Texto de resumen real: "Cuota 1/24", "C.05/06", "Cuota 2/3" -> (cuota_actual, total)
    - Vacío/inválido -> (1, 1)
    Devuelve SIEMPRE (cuota_actual, total_cuotas) como enteros >= 1.
    Esto es la base de todo el sistema de proyección de deuda: sin esto,
    "Cuota 1/24" se leía como 1 cuota total (perdiendo 23 cuotas futuras de deuda)."""
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "n/a", ""):
        return 1, 1
    # Buscar patrón X/Y en cualquier parte del string (cubre "Cuota 1/24", "C.05/06", "1/3")
    import re
    m = re.search(r'(\d+)\s*/\s*(\d+)', s)
    if m:
        actual = int(m.group(1))
        total = int(m.group(2))
        if total < 1: total = 1
        if actual < 1: actual = 1
        if actual > total: actual = total
        return actual, total
    # Si no hay patrón X/Y, es un número simple (cantidad total de cuotas,
    # asumiendo que es la PRIMERA cuota — comportamiento histórico de la app
    # para cuando el usuario carga un gasto nuevo a mano)
    n = safe_int(s, 1)
    if n < 1: n = 1
    return 1, n

def fmt_cuotas(actual, total):
    """Formatea el par (actual, total) de vuelta a texto para guardar en el CSV."""
    if total <= 1:
        return "1"
    return f"Cuota {actual}/{total}"

def get_tarjetas_nombres():
    """BUG 3 FIX: siempre incluye defaults + las configuradas + las que aparecen en gastos"""
    nombres = list(TARJETAS_DEFAULT)  # empieza con defaults
    t_df = load("tarjetas")
    if not t_df.empty:
        for n in t_df["Nombre"].dropna().tolist():
            n = str(n).strip()
            if n and n not in nombres:
                nombres.append(n)
    # también agregar las que están en gastos importados
    g_df = load("gastos")
    if not g_df.empty and "Tarjeta" in g_df.columns:
        for t in g_df["Tarjeta"].dropna().unique():
            t = str(t).strip()
            if t and t not in ("nan", "None", "") and t not in nombres:
                nombres.append(t)
    return nombres

def _generar_fechas_cierre(tarjeta_row, rango_dias=400):
    """Genera la lista de fechas de cierre (date objects) para una tarjeta,
    cubriendo desde `rango_dias` atrás hasta `rango_dias` adelante de hoy.

    Modo nuevo (preferido): usa 'Cierre anterior' + 'Proximo cierre' (las DOS
    fechas exactas que aparecen en el resumen real) para calcular el intervalo
    REAL entre esos dos ciclos específicos, y lo repite hacia adelante/atrás.
    Esto es necesario porque el intervalo entre cierres NO es constante en
    algunos bancos (ej: Banco Hipotecario salta de 28/05 a 02/07 = 35 días,
    no los 31 días "típicos") — anclar con un solo punto + intervalo fijo
    genera fechas intermedias que no coinciden con la realidad.
    Si solo hay 'Proximo cierre' (sin 'Cierre anterior'), usa 'Dias entre
    cierres' como intervalo aproximado.
    Si no hay 'Proximo cierre' configurado, devuelve None (caller usa modo
    día-fijo viejo para no romper compatibilidad)."""
    proximo_raw = str(tarjeta_row.get("Proximo cierre", "")).strip()
    if not proximo_raw or proximo_raw.lower() in ("nan","none","s/f",""):
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
    if anterior_raw and anterior_raw.lower() not in ("nan","none","s/f",""):
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
    # Retroceder desde el punto ancla más antiguo conocido
    f = anterior if anterior else proximo
    while f > hoy - timedelta(days=rango_dias):
        f = f - timedelta(days=intervalo)
        fechas.append(f)
    # Avanzar desde el próximo cierre conocido
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
        # Modo fecha exacta: el período "del mes X" es el ciclo cuyo CIERRE
        # cae en el mes/año X. Se busca esa fecha de cierre y la anterior.
        objetivo = date(año, mes, min(28, calendar.monthrange(año, mes)[1]))
        candidatas = [f for f in fechas_cierre if f.year == año and f.month == mes]
        if candidatas:
            fin = max(candidatas)
        else:
            # No hay cierre exacto en ese mes (puede saltarse un mes, como
            # pasó de 28/05 a 02/07): tomar el cierre más próximo posterior
            posteriores = [f for f in fechas_cierre if f >= objetivo]
            fin = min(posteriores) if posteriores else max(fechas_cierre)
        anteriores = [f for f in fechas_cierre if f < fin]
        inicio = max(anteriores) + timedelta(days=1) if anteriores else fin - timedelta(days=30)
        return inicio, fin

    # Modo viejo (compatibilidad): día fijo todos los meses
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
    # CRÍTICO: las fechas ya guardadas en la base están normalizadas a YYYY-MM-DD
    # (sin ambigüedad). Usar dayfirst=True sobre un string YYYY-MM-DD lo reinterpreta
    # mal (ej: "2026-06-11" con dayfirst=True devuelve 2026-11-06 -> ¡noviembre!).
    # Por eso primero se intenta el formato ISO exacto, y solo si falla
    # se cae a un parseo más flexible con dayfirst para fechas crudas tipo DD/MM/YYYY.
    fg = None
    try:
        fg = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        pass
    if fg is None:
        try:
            fg = pd.to_datetime(s, dayfirst=True).date()
        except Exception:
            hoy = date.today(); return hoy.year, hoy.month
    t_df = load("tarjetas")
    if t_df.empty or tarjeta_nombre not in t_df["Nombre"].values:
        return fg.year, fg.month
    row = t_df[t_df["Nombre"] == tarjeta_nombre].iloc[0]

    fechas_cierre = _generar_fechas_cierre(row)
    if fechas_cierre:
        # El gasto pertenece al ciclo cuyo cierre es la primera fecha >= fecha del gasto
        posteriores = [f for f in fechas_cierre if f >= fg]
        if posteriores:
            cierre_del_ciclo = min(posteriores)
            return cierre_del_ciclo.year, cierre_del_ciclo.month
        # Si no hay cierre futuro generado (gasto muy lejano), usar su propio mes
        return fg.year, fg.month

    # Modo viejo (compatibilidad): día fijo todos los meses
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
            ay, am = periodo_actual_de_gasto(r.get("Fecha",""), tarjeta_nombre)
            if ay == año_periodo and am == mes_periodo:
                rows.append(r)
    return pd.DataFrame(rows, columns=gastos_df.columns) if rows else pd.DataFrame(columns=gastos_df.columns)

def proyectar_cuotas(gastos_df):
    """Toma el DataFrame de gastos (un registro por compra, con Cuotas tipo
    'Cuota X/Y' o número simple) y genera una fila VIRTUAL por cada cuota
    pendiente, con su fecha y período correctos.

    Ejemplo: una compra de $1200 cargada como "Cuota 1/12" con fecha 2026-01-15
    genera 12 filas virtuales: cuota 1 en enero, cuota 2 en febrero, ..., cuota
    12 en diciembre — cada una por $100 (el monto del registro original ya es
    el valor de UNA cuota, tal como aparece en el resumen real del banco).

    Esto es la base para que el flujo de fondos y los períodos de tarjeta
    muestren la deuda real proyectada, en vez de solo la fecha de la compra
    original. No modifica el CSV en disco — es una vista calculada en memoria.

    Devuelve un DataFrame con las mismas columnas que gastos_df, más:
    - 'Cuota actual', 'Cuota total': para mostrar "3/12" etc
    - 'Es proyectada': True si es una cuota futura generada (no el registro original)
    """
    if gastos_df.empty:
        return gastos_df.copy()

    filas_resultado = []
    for _, r in gastos_df.iterrows():
        actual, total = parsear_cuotas(r.get("Cuotas", 1))
        fecha_raw = str(r.get("Fecha", "")).strip()
        try:
            fecha_base = datetime.strptime(fecha_raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            fecha_base = None

        if total <= 1 or fecha_base is None:
            # Gasto sin cuotas (o fecha inválida): se deja como está, sin proyectar
            fila = r.copy()
            fila["Cuota actual"] = 1
            fila["Cuota total"] = 1
            fila["Es proyectada"] = False
            filas_resultado.append(fila)
            continue

        # La fecha guardada corresponde a la cuota "actual". Se proyecta
        # hacia atrás y hacia adelante para cubrir las cuotas 1..total,
        # sumando/restando meses calendario desde esa fecha ancla.
        for n_cuota in range(1, total + 1):
            delta_meses = n_cuota - actual
            mes_total = fecha_base.month - 1 + delta_meses
            año_cuota = fecha_base.year + mes_total // 12
            mes_cuota = mes_total % 12 + 1
            dia_cuota = min(fecha_base.day, calendar.monthrange(año_cuota, mes_cuota)[1])
            fecha_cuota = date(año_cuota, mes_cuota, dia_cuota)

            fila = r.copy()
            fila["Fecha"] = fecha_cuota.strftime("%Y-%m-%d")
            fila["Cuota actual"] = n_cuota
            fila["Cuota total"] = total
            fila["Es proyectada"] = (n_cuota != actual)
            filas_resultado.append(fila)

    return pd.DataFrame(filas_resultado).reset_index(drop=True)

def es_concepto_usd(concepto):
    """Detecta si un concepto de gasto está en dólares según las marcas que
    usan los resúmenes argentinos: (U$S), (USD), U$S al final, etc."""
    import re
    s = str(concepto).upper()
    return bool(re.search(r'\(\s*U\$S\s*\)|\(\s*USD\s*\)|\bU\$S\b', s))

@st.cache_data(ttl=3600)
def obtener_cotizacion_dolar_tarjeta():
    """Consulta la cotización del 'dólar tarjeta' (oficial + impuestos PAIS/
    ganancias), que es la que usan los bancos argentinos para convertir
    consumos en USD hechos con tarjeta de crédito. Cacheada 1 hora para no
    golpear la API en cada rerun de Streamlit. Devuelve el valor de VENTA
    (el que corresponde para convertir un gasto, no una compra de dólares)."""
    try:
        resp = requests.get("https://dolarapi.com/v1/dolares/tarjeta", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        venta = float(data.get("venta", 0))
        return venta if venta > 0 else None
    except Exception:
        return None

def convertir_monto_usd_a_ars(monto_usd, cotizacion=None):
    """Convierte un monto en USD a ARS usando la cotización dólar tarjeta.
    Si no se pasa cotización, la consulta (con cache). Si la consulta falla
    (sin internet, API caída), devuelve None — el caller decide qué hacer
    (ej: dejar el monto sin convertir y avisar al usuario)."""
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
        c = str(tarjetas_df[tarjetas_df["Nombre"]==tname].iloc[0].get("Color","#7c6af7"))
        return c if c.startswith("#") else "#7c6af7"
    idx = TARJETAS_DEFAULT.index(tname) if tname in TARJETAS_DEFAULT else 0
    return COLORES_TARJETA[idx % len(COLORES_TARJETA)]

# ── Importación de movimientos desde texto/CSV pegado ──────────────────────────
def _normalizar_texto(s):
    """Normaliza un string para comparación: minúsculas, sin espacios extra,
    sin espacios dobles internos."""
    s = str(s).strip().lower()
    s = " ".join(s.split())  # colapsa espacios múltiples
    return s

def limpiar_csv_montos(texto_csv):
    """Pre-procesa un CSV crudo donde los montos pueden venir con coma de miles
    SIN comillas (ej: 5,000.00) o CON comillas (ej: "224,679.00").
    Ambos casos rompen el parseo estándar de CSV porque la coma se confunde
    con el separador de columnas. Esta función junta esos fragmentos en un solo
    campo antes de que pandas lea el archivo.
    Detecta patrones tipo: ,NUMERO,NUMERO.NUMERO  o  ,"NUMERO,NUMERO.NUMERO",
    y los convierte a: ,NUMERO.NUMERO (sin coma de miles)."""
    import re
    lineas = texto_csv.strip().split("\n")
    if not lineas:
        return texto_csv

    resultado = [lineas[0]]  # header tal cual
    n_cols_esperadas = len(lineas[0].split(","))

    for linea in lineas[1:]:
        if not linea.strip():
            continue
        # Caso 1: monto entre comillas con coma de miles -> "224,679.00" => 224679.00
        linea_fix = re.sub(r'"(\d{1,3}(?:,\d{3})+\.\d+)"', lambda m: m.group(1).replace(",", ""), linea)
        # Caso 2: monto SIN comillas con coma de miles suelta en medio de la línea
        # Patrón: ,NUMERO,NUMERO.NUMERO  (ej: ,5,000.00) -> ,5000.00
        linea_fix = re.sub(r',(\d{1,3}),(\d{3}\.\d+)', r',\1\2', linea_fix)
        resultado.append(linea_fix)

    return "\n".join(resultado)

def es_duplicado(fecha_str, concepto, monto, tarjeta, gastos_existentes):
    """Chequea si un movimiento ya existe en la base.
    Match ESTRICTO de las 4 columnas: Tarjeta + Fecha + Concepto + Monto (tolerancia $1).
    Las 4 tienen que coincidir para considerarlo duplicado."""
    if gastos_existentes.empty:
        return False

    concepto_norm = _normalizar_texto(concepto)
    tarjeta_norm  = _normalizar_texto(tarjeta)
    fecha_norm    = _normalizar_texto(fecha_str)

    try:
        monto_f = float(monto)
    except (ValueError, TypeError):
        return False

    existentes = gastos_existentes.copy()
    existentes["_concepto_norm"] = existentes["Concepto"].apply(_normalizar_texto)
    existentes["_tarjeta_norm"]  = existentes["Tarjeta"].apply(_normalizar_texto)
    existentes["_fecha_norm"]    = existentes["Fecha"].apply(_normalizar_texto)

    candidatos = existentes[
        (existentes["_concepto_norm"] == concepto_norm) &
        (existentes["_tarjeta_norm"]  == tarjeta_norm) &
        (existentes["_fecha_norm"]    == fecha_norm)
    ]
    if candidatos.empty:
        return False

    for _, r in candidatos.iterrows():
        try:
            monto_existente = float(r.get("Monto", 0))
        except (ValueError, TypeError):
            continue
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

# ── Cargar datos (dtype=str para no perder nada, numéricos explícitos después) ─
gastos_df   = load("gastos")
ingresos_df = load("ingresos")
comp_df     = load("compartidos")
inv_df      = load("inversiones")
pres_df     = load("presupuesto")
tarjetas_df = load("tarjetas")

# Numéricos explícitos — nunca tocar strings raros
gastos_df["Monto"]          = to_num(gastos_df["Monto"])
gastos_df["Cuanto recupero"]= to_num(gastos_df["Cuanto recupero"])
ingresos_df["Monto"]        = to_num(ingresos_df["Monto"])
comp_df["Monto"]            = to_num(comp_df["Monto"])

# Normalizar TODAS las fechas de gastos a YYYY-MM-DD en cada carga.
# Usa normalizar_fecha_existente (NO fmt_fecha) para no inventar "hoy"
# en filas que genuinamente no tienen fecha — esas deben ir al fondo del orden.
if not gastos_df.empty:
    gastos_df["Fecha"] = gastos_df["Fecha"].apply(normalizar_fecha_existente)

y, m = mes_actual()
nombre_mes = calendar.month_name[m].capitalize()

# BUG 5 FIX: ordenar por fecha correctamente desde el inicio
def sort_by_fecha(df):
    """Ordena por fecha desc. Los None/NaT (sin fecha) van siempre al fondo."""
    if df.empty: return df
    df = df.copy()
    df["_sort"] = pd.to_datetime(df["Fecha"], errors="coerce")
    # na_position="last": filas sin fecha van al fondo, no al tope
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
            # BUG 1 FIX: date_input nativo de Python, sin conversión manual
            q_f = c5.date_input("Fecha", value=date.today())
            q_cu = c6.number_input("Cuotas", min_value=1, max_value=48, value=1)
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Guardar gasto"):
                if q_c.strip() and q_m > 0:
                    # BUG 6 FIX: usar fmt_fecha que convierte date→YYYY-MM-DD sin distorsión
                    fecha_str = fmt_fecha(q_f)
                    nv = pd.DataFrame([[fecha_str, q_c.strip(), q_m, q_t, q_cu, q_k, "No", "", 0, ""]],
                                      columns=FILES["gastos"][1])
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
                    # Incluir TODAS las columnas del esquema actual (no solo las 4 viejas),
                    # sino la fila queda con columnas faltantes -> "None" feos.
                    fila = pd.DataFrame([[t_n.strip(), t_dc, t_dv, t_col, "", "", 31]],
                                        columns=FILES["tarjetas"][1])
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
tabs = st.tabs(["Inicio","Gastos","Tarjetas","Ingresos","Compartidos","Inversiones","Presupuesto","Flujo"])

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

    # Recargar del disco una sola vez para toda la sección home
    gastos_fresh = load("gastos")
    gastos_fresh["Monto"] = to_num(gastos_fresh["Monto"])
    gastos_fresh = sort_by_fecha(gastos_fresh)
    # Proyectar cuotas: cada compra en cuotas se "desdobla" en una fila virtual
    # por cuota pendiente, con la fecha real en que esa cuota cae. Sin esto,
    # un gasto cargado como "Cuota 1/12" solo aparecía en el mes de la compra
    # y las otras 11 cuotas de deuda quedaban invisibles.
    gastos_proyectado = proyectar_cuotas(gastos_fresh)

    # Resumen tarjetas — usa el PERÍODO REAL de cada tarjeta (cierre/vencimiento),
    # no el mes calendario. Antes esto usaba filtrar_mes() que ignoraba la
    # configuración de Día de cierre, dejando afuera tarjetas cuyo ciclo
    # no coincide con el mes calendario (ej: gastos del 31-may que pertenecen
    # al período de junio si la tarjeta cierra el 28).
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

    # Últimos movimientos — priorizar los que TIENEN fecha real
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
            # No truncar el string crudo con [:10] — si la fecha no está en formato
            # ISO (ej: viene como "11-Jun-2026", 12 caracteres), cortar a 10 caracteres
            # corta el año a la mitad ("11-Jun-202"). Se normaliza siempre antes de mostrar.
            fecha_str = normalizar_fecha_existente(r.get("Fecha","")) or "sin fecha"
            tname_r = str(r.get("Tarjeta",""))
            cuotas_v = safe_int(r.get("Cuotas",1), 1)
            cuotas_t = f" · {cuotas_v}c" if cuotas_v > 1 else ""
            # chip de período solo si la tarjeta tiene cierre configurado
            chip = ""
            if not tarjetas_df.empty and tname_r in tarjetas_df["Nombre"].values:
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
                "</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total pendiente</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(pend['Monto'].sum())}</span>"
            "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — GASTOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    with st.expander("📤 Exportar todos los gastos (backup)"):
        st.markdown(
            "<div class='info-strip'>Copiá este texto y guardalo aparte. Si en algún momento se pierden gastos "
            "por una actualización del código, lo pegás en el importador de arriba y los recuperás.</div>",
            unsafe_allow_html=True
        )
        gastos_export = load("gastos")
        if gastos_export.empty:
            st.caption("No hay gastos cargados todavía.")
        else:
            csv_export = gastos_export.to_csv(index=False)
            st.text_area(
                f"{len(gastos_export)} movimientos en total",
                value=csv_export,
                height=160,
                key="export_csv_area"
            )
            st.download_button(
                "⬇️ Descargar como archivo .csv",
                data=csv_export,
                file_name=f"backup_gastos_{date.today().isoformat()}.csv",
                mime="text/csv",
                key="download_csv_btn"
            )

    with st.expander("📥 Importar desde CSV / foto de resumen"):
        st.markdown(
            "<div class='info-strip'>Pasale tus capturas de resumen a Claude (chat normal) y pedile que te devuelva "
            "el CSV con columnas <code>Fecha,Concepto,Monto,Cuotas</code>. Pegalo acá. "
            "Si el CSV no trae columna Tarjeta, elegí una abajo — se aplica a todas las filas.</div>",
            unsafe_allow_html=True
        )
        csv_text = st.text_area("", placeholder="Fecha,Concepto,Monto,Cuotas\n2026-05-24,MERPAGO*SOFIACARLINI,16476.46,1\n...", height=140, label_visibility="collapsed", key="csv_import_text")
        tarjeta_import = st.selectbox("Tarjeta para estos movimientos (si el CSV no la trae)", TARJETAS, key="tarjeta_import_sel")

        if st.button("🔍 Previsualizar", key="preview_csv"):
            if csv_text.strip():
                try:
                    csv_limpio = limpiar_csv_montos(csv_text)
                    nuevos = pd.read_csv(io.StringIO(csv_limpio), dtype=str).fillna("")
                    nuevos.columns = [c.strip() for c in nuevos.columns]

                    # La tarjeta seleccionada en el selectbox es el FALLBACK:
                    # se usa siempre que el CSV no traiga Tarjeta, o la traiga
                    # vacía / "None" / "nan" en esa fila puntual (no solo si TODAS
                    # las filas están vacías — antes una fila con dato y otra sin
                    # dato dejaba esta última sin tarjeta asignada).
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
                    nuevos["Fecha"] = nuevos["Fecha"].apply(normalizar_fecha_existente)
                    nuevos["Monto"] = to_num(nuevos["Monto"])

                    # Detectar y convertir gastos en USD (ej: "APPLE.COM/BILL... (U$S)")
                    # usando la cotización del dólar tarjeta del día. El monto en el
                    # CSV de resumen ya viene en USD para estos casos — se convierte
                    # a ARS para que el resto de la app (totales, períodos, etc) sea
                    # consistente, ya que todo el resto del sistema asume pesos.
                    usd_mask = nuevos["Concepto"].apply(es_concepto_usd)
                    usd_count = int(usd_mask.sum())
                    usd_sin_convertir = 0
                    if usd_count > 0:
                        cotiz = obtener_cotizacion_dolar_tarjeta()
                        if cotiz:
                            nuevos.loc[usd_mask, "Notas"] = nuevos.loc[usd_mask, "Notas"].astype(str) + f" [USD→ARS @ ${cotiz:,.2f}]"
                            nuevos.loc[usd_mask, "Monto"] = nuevos.loc[usd_mask, "Monto"].apply(
                                lambda m: convertir_monto_usd_a_ars(m, cotiz)
                            )
                        else:
                            usd_sin_convertir = usd_count

                    # Excluir pagos de tarjeta y ajustes — NO son gastos de consumo.
                    # "SU PAGO EN PESOS", montos negativos o $0 son pagos/ajustes del resumen,
                    # no compras. Si se importan como gasto, rompen el remanente porque
                    # un monto negativo resta en vez de sumar al total de gastos.
                    PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior", "pago tarjeta"]
                    concepto_lower = nuevos["Concepto"].astype(str).str.strip().str.lower()
                    es_pago_mask = concepto_lower.isin(PALABRAS_EXCLUIR) | nuevos["Monto"].astype(float).le(0)
                    excluidos_count = int(es_pago_mask.sum())
                    nuevos = nuevos[~es_pago_mask].copy()

                    # Filtrar duplicados contra lo que ya está guardado
                    gastos_actuales = load("gastos")
                    gastos_actuales["Monto"] = to_num(gastos_actuales["Monto"])
                    # CRÍTICO: normalizar también la fecha de los datos ya guardados.
                    # Si no se hace, una fecha guardada en formato distinto al normalizado
                    # nunca matchea contra el CSV nuevo y el duplicado se cuela.
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
                    st.session_state["_csv_usd_count"] = usd_count
                    st.session_state["_csv_usd_sin_convertir"] = usd_sin_convertir
                except Exception as e:
                    st.error(f"No pude leer el CSV: {e}")
                    st.session_state.pop("_csv_preview", None)
            else:
                st.warning("Pegá el CSV primero.")

        if "_csv_preview" in st.session_state:
            preview = st.session_state["_csv_preview"]
            dup_count = st.session_state.get("_csv_dup_count", 0)
            excl_count = st.session_state.get("_csv_excl_count", 0)
            usd_count = st.session_state.get("_csv_usd_count", 0)
            usd_sin_convertir = st.session_state.get("_csv_usd_sin_convertir", 0)

            if usd_count > 0:
                if usd_sin_convertir > 0:
                    st.markdown(f"<div class='info-strip'>⚠️ {usd_sin_convertir} gasto(s) en USD detectado(s), pero no se pudo consultar la cotización (sin conexión a la API). Quedan con el monto en USD sin convertir — revisalos a mano.</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='info-strip'>💵 {usd_count} gasto(s) en USD convertido(s) a ARS con la cotización del dólar tarjeta del día.</div>", unsafe_allow_html=True)

            if excl_count > 0:
                st.markdown(f"<div class='info-strip'>🚫 {excl_count} fila(s) excluida(s) — eran pagos de tarjeta o montos negativos, no gastos de consumo.</div>", unsafe_allow_html=True)

            if dup_count > 0:
                st.markdown(f"<div class='info-strip'>⏭️ {dup_count} movimiento(s) ya existían y se omiten automáticamente.</div>", unsafe_allow_html=True)

            if preview.empty:
                st.markdown("<div class='empty'><big>✅</big>Nada nuevo para importar — todo ya estaba cargado.</div>", unsafe_allow_html=True)
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

                if st.button(f"✅ Confirmar e importar {len(preview)} movimiento(s)", key="confirm_import"):
                    base = load("gastos")
                    base["Monto"] = to_num(base["Monto"])
                    final = pd.concat([base, preview], ignore_index=True)
                    final = sort_by_fecha(final)
                    save("gastos", final)
                    st.session_state.pop("_csv_preview", None)
                    st.session_state.pop("_csv_dup_count", None)
                    st.success(f"✅ {len(preview)} movimientos importados.")
                    st.rerun()

    with st.expander("📤 Exportar / Backup de todos los gastos"):
        st.markdown(
            "<div class='info-strip'>Descargá un backup completo antes de modificar el código. "
            "El CSV generado se puede pegar tal cual en el importador de arriba para restaurar todo.</div>",
            unsafe_allow_html=True
        )
        gastos_export = load("gastos")
        if gastos_export.empty:
            st.markdown("<div class='empty'><big>📋</big>No hay gastos cargados todavía.</div>", unsafe_allow_html=True)
        else:
            gastos_export = sort_by_fecha(gastos_export)
            csv_bytes = gastos_export.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=f"⬇️ Descargar backup ({len(gastos_export)} movimientos)",
                data=csv_bytes,
                file_name=f"backup_gastos_{date.today().isoformat()}.csv",
                mime="text/csv",
                key="download_backup"
            )
            st.caption("También podés copiar el texto de abajo y guardarlo donde quieras:")
            st.text_area(
                "CSV completo (para copiar)",
                value=gastos_export.to_csv(index=False),
                height=160,
                key="export_csv_text"
            )

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
            # BUG 7 FIX: text_input para "Con quién" — nunca number_input
            g_quien = c7.text_input("Con quién", placeholder="Nombre")
            g_rec   = c8.number_input("Recuperás $", min_value=0.0, step=100.0) if g_comp == "Sí" else 0.0
            g_nota  = st.text_input("Nota", placeholder="Opcional")
            if st.form_submit_button("Guardar gasto"):
                if g_c.strip() and g_m > 0:
                    fecha_str = fmt_fecha(g_f)
                    nv = pd.DataFrame([[fecha_str, g_c.strip(), g_m, g_t, g_cu, g_k, g_comp, g_quien, g_rec, g_nota]],
                                      columns=FILES["gastos"][1])
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    gastos_df = sort_by_fecha(gastos_df)
                    save("gastos", gastos_df)
                    if g_comp == "Sí" and g_rec > 0:
                        nvc = pd.DataFrame([[fecha_str, g_c.strip(), g_rec, g_quien, "Pendiente", ""]],
                                           columns=comp_df.columns)
                        comp_df2 = pd.concat([comp_df, nvc], ignore_index=True)
                        comp_df2["Monto"] = to_num(comp_df2["Monto"])
                        save("compartidos", comp_df2)
                    st.success(f"Guardado: {g_c} — {fmt_ars(g_m)}")
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")

    with st.expander("🗑️ Eliminar gastos"):
        # Detectar automáticamente filas basura: pagos de tarjeta o montos <= 0
        # que se hayan colado en importaciones anteriores (antes de este fix)
        PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior", "pago tarjeta"]
        concepto_lower_g = gastos_df["Concepto"].astype(str).str.strip().str.lower()
        gastos_monto_num = to_num(gastos_df["Monto"])
        mask_basura = concepto_lower_g.isin(PALABRAS_EXCLUIR) | gastos_monto_num.le(0)
        candidatos_basura = gastos_df[mask_basura]

        if not candidatos_basura.empty:
            st.markdown(f"<div class='info-strip'>⚠️ Se detectaron {len(candidatos_basura)} fila(s) que parecen pagos de tarjeta o montos inválidos, no gastos reales.</div>", unsafe_allow_html=True)
            for idx, r in candidatos_basura.iterrows():
                st.markdown(f"- **{r['Concepto']}** · {r['Fecha']} · {fmt_ars(r['Monto'])}")
            if st.button(f"🧹 Eliminar {len(candidatos_basura)} fila(s) detectada(s)", key="clean_basura"):
                gastos_df_limpio = gastos_df.drop(index=candidatos_basura.index).reset_index(drop=True)
                save("gastos", gastos_df_limpio)
                st.success("Limpieza completada.")
                st.rerun()
            st.divider()

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
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total general</span>"
            f"<span class='total-strip-val c-neg'>{fmt_ars(gastos_df['Monto'].sum())}</span>"
            "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TARJETAS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    tarjetas_df = load("tarjetas")

    st.markdown("<div class='sec'>Configuración de tarjetas</div>", unsafe_allow_html=True)

    # Botón de configuración rápida: crea/reemplaza las 3 tarjetas conocidas
    # con los datos reales de tus resúmenes (Banco Nación / Banco Hipotecario).
    # Soluciona de raíz el problema de tarjetas sin configurar que hacían
    # caer todo al fallback de "mes calendario completo" en vez del ciclo real.
    if tarjetas_df.empty or len(tarjetas_df) < 3:
        st.markdown(
            "<div class='info-strip'>⚠️ Tenés menos de 3 tarjetas configuradas. "
            "Sin la configuración correcta, los períodos caen al mes calendario completo "
            "en vez de tu ciclo real de cierre/vencimiento.</div>",
            unsafe_allow_html=True
        )
        if st.button("⚡ Configurar mis 3 tarjetas con un click", key="setup_rapido"):
            config_rapida = pd.DataFrame([
                ["Visa ICBC",        28, 10, "#7c6af7", "", "", 31],
                ["Visa Hipotecario", 28,  5, "#4ade80", "", "", 31],
                ["Master ICBC",      28, 10, "#f87171", "", "", 31],
            ], columns=["Nombre","Dia cierre","Dia vencimiento","Color","Cierre anterior","Proximo cierre","Dias entre cierres"])
            # Conservar tarjetas existentes que no estén en esta lista
            nombres_config = config_rapida["Nombre"].tolist()
            resto = tarjetas_df[~tarjetas_df["Nombre"].isin(nombres_config)] if not tarjetas_df.empty else pd.DataFrame(columns=config_rapida.columns)
            final_tarjetas = pd.concat([config_rapida, resto], ignore_index=True)
            save("tarjetas", final_tarjetas)
            st.success("✅ Tarjetas configuradas: Visa ICBC, Visa Hipotecario, Master ICBC.")
            st.rerun()
        st.divider()

    # ABM siempre visible, incluso si está vacío (se puede agregar con num_rows="dynamic")
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

    st.markdown(
        "<div class='info-strip'>Hay 2 formas de configurar el ciclo:<br>"
        "<strong>① Simple:</strong> completá solo <strong>Día cierre</strong> y <strong>Día vence</strong> "
        "(mismo número todos los meses).<br>"
        "<strong>② Exacta (recomendada si el cierre no es siempre el mismo día, ej. Banco Hipotecario):</strong> "
        "completá <strong>Cierre anterior</strong> y <strong>Próximo cierre</strong> con las 2 fechas exactas "
        "que dice tu resumen ('CIERRE ANTERIOR' y 'PRÓXIMO CIERRE'). Con esas 2 fechas reales se calcula el "
        "intervalo real entre ciclos — sin necesidad de adivinar. Si solo completás 'Próximo cierre' sin la "
        "anterior, se usa 'Días entre cierres' como aproximación. Esto tiene prioridad sobre el modo simple. "
        "Los colores van en hex (#7c6af7). Luego guardá.</div>",
        unsafe_allow_html=True
    )
    edited_t = st.data_editor(
        tarjetas_edit,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Nombre":             st.column_config.TextColumn("Nombre"),
            "Dia cierre":         st.column_config.NumberColumn("Día cierre (simple)", min_value=1, max_value=31, step=1),
            "Dia vencimiento":    st.column_config.NumberColumn("Día vence (simple)", min_value=1, max_value=31, step=1),
            "Cierre anterior":    st.column_config.DateColumn("Cierre anterior (exacto)"),
            "Proximo cierre":     st.column_config.DateColumn("Próximo cierre (exacto)"),
            "Dias entre cierres": st.column_config.NumberColumn("Días entre cierres (si no hay anterior)", min_value=20, max_value=45, step=1),
            "Color":              st.column_config.SelectboxColumn("Color", options=["#7c6af7","#4ade80","#f87171","#fbbf24","#60a5fa","#f472b6","#34d399","#fb923c"]),
        },
        key="editor_tarjetas"
    )
    if st.button("💾 Guardar tarjetas", key="save_t"):
        # Filtrar filas sin nombre real (vacías o con texto basura tipo "None")
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

    # El nombre del período (ej "Jun 2026") es el mes en que CIERRA ese ciclo,
    # no el mes calendario de los gastos. Para que quede claro, cada opción
    # del selector muestra también el rango real de fechas que incluye
    # (ej: "Jun 2026 · 29/05→28/06"), calculado con el cierre de la tarjeta elegida.
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
    # Leer siempre del disco — fuente de verdad
    gastos_base = load("gastos")
    gastos_base["Monto"] = to_num(gastos_base["Monto"])
    gastos_base["Cuanto recupero"] = to_num(gastos_base["Cuanto recupero"])
    # _row_id = posición exacta en el CSV (0-indexed), ESTABLE para esta lectura
    gastos_base["_row_id"] = range(len(gastos_base))
    df_per = filtrar_gastos_tarjeta_periodo(gastos_base, t_sel, sel_py, sel_pm)
    # Guardar los row_ids en session_state para que el botón los use aunque Streamlit re-ejecute
    _key_ids = f"row_ids_{t_sel}_{sel_py}_{sel_pm}"
    if not df_per.empty:
        st.session_state[_key_ids] = list(df_per["_row_id"].astype(int))
    elif _key_ids not in st.session_state:
        st.session_state[_key_ids] = []
    total_per = to_num(df_per["Monto"]).sum() if not df_per.empty else 0

    # Total PROYECTADO: incluye cuotas futuras de compras hechas en OTROS meses
    # que caen en este período (ej: cuota 5/12 de una compra de hace 4 meses).
    # Se calcula aparte del editor de filas reales porque esas cuotas futuras
    # son virtuales — no existen como fila propia en el CSV, así que no se
    # pueden editar/borrar individualmente acá (se edita la compra original).
    gastos_proyectado_tab = proyectar_cuotas(gastos_base.drop(columns=["_row_id"]))
    df_per_proyectado = filtrar_gastos_tarjeta_periodo(gastos_proyectado_tab, t_sel, sel_py, sel_pm)
    total_proyectado = to_num(df_per_proyectado["Monto"]).sum() if not df_per_proyectado.empty else 0
    cant_cuotas_de_otros_meses = len(df_per_proyectado[df_per_proyectado.get("Es proyectada", False) == True]) if not df_per_proyectado.empty else 0

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
        f"<span class='total-strip-val' style='color:{color_t_sel}'>−{fmt_ars(total_proyectado)}</span>"
        "</div>", unsafe_allow_html=True)
    if cant_cuotas_de_otros_meses > 0:
        st.markdown(
            f"<div class='info-strip'>📋 Incluye {cant_cuotas_de_otros_meses} cuota(s) de compras hechas en otros meses. "
            f"Las filas editables abajo muestran solo las {len(df_per)} compra(s) ORIGINAL(es) de este período "
            f"(−{fmt_ars(total_per)}) — para ver/editar una cuota futura, buscá la compra original en su mes.</div>",
            unsafe_allow_html=True
        )

    if not df_per.empty:
        # Preparar df para el editor — quitar _row_id antes de mostrar
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

            # Leer CSV del disco en este momento exacto
            base = load("gastos")
            base["Monto"]           = to_num(base["Monto"])
            base["Cuanto recupero"] = to_num(base["Cuanto recupero"])
            base["_row_id"]         = range(len(base))

            # Validar que los IDs a eliminar están dentro del rango actual del CSV
            max_id = len(base) - 1
            ids_validos = {i for i in ids_a_eliminar if 0 <= i <= max_id}

            if not ids_validos and ids_a_eliminar:
                st.error("Error de sincronización. Recargá la página y volvé a intentar.")
                st.stop()

            # Quitar solo esas filas exactas
            base_limpia = base[~base["_row_id"].isin(ids_validos)].drop(columns=["_row_id"]).reset_index(drop=True)

            # Preparar filas editadas
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
            # Limpiar session_state para este período
            if _key_ids in st.session_state:
                del st.session_state[_key_ids]
            st.success(f"✅ Guardado. {len(nuevas)} filas actualizadas.")
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
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total mes</span>"
            f"<span class='total-strip-val c-pos'>{fmt_ars(ing_show['Monto'].sum())}</span>"
            "</div>", unsafe_allow_html=True)

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
    cobs  = comp_df[comp_df["Estado"] != "Pendiente"].sort_values("Fecha", ascending=False)

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
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total a cobrar</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(pends['Monto'].sum())}</span>"
            "</div>", unsafe_allow_html=True)

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
                    "</div>", unsafe_allow_html=True)


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
                inv_df2 = load("inversiones")
                nv = pd.DataFrame([[fmt_fecha(inv_f), inv_n.strip(), inv_cap, inv_r, inv_mon, inv_nota]],
                                  columns=inv_df2.columns)
                inv_df2 = pd.concat([inv_df2, nv], ignore_index=True)
                save("inversiones", inv_df2)
                st.success(f"Registrado: {inv_n}")
                st.rerun()
            else:
                st.warning("Nombre y capital requeridos.")

    inv_df = load("inversiones")
    inv_df["Capital"]     = to_num(inv_df["Capital"])
    inv_df["Rendimiento"] = to_num(inv_df["Rendimiento"])

    if not inv_df.empty:
        tc = inv_df["Capital"].sum(); tr = inv_df["Rendimiento"].sum()
        st.markdown(
            "<div class='stat-row'>"
            f"<div class='stat-cell'><div class='stat-label'>Capital</div><div class='stat-val c-neu'>{fmt_ars(tc)}</div></div>"
            f"<div class='stat-cell'><div class='stat-label'>Rendimiento</div><div class='stat-val c-pos'>+{fmt_ars(tr)}</div></div>"
            "</div>", unsafe_allow_html=True)
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
                f"<div style='font-size:0.68rem;color:#39e07a;font-family:\"DM Mono\",monospace'>+{fmt_ars(r.get('Rendimiento',0))}</div>"
                "</div>"
                "</div>", unsafe_allow_html=True)
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
                    pres_df_new = pd.concat([pres_df_new, pd.DataFrame([[p_cat, p_lim]], columns=["Categoria","Limite"])], ignore_index=True)
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
            pct  = min(int(gast/lim*100), 100) if lim > 0 else 0
            sobra= lim - gast
            col  = "#39e07a" if pct < 70 else "#f5c542" if pct < 90 else "#ff5f7e"
            ico  = "✓" if pct < 90 else "⚠" if pct < 100 else "✕"
            note = f"Excedido {fmt_ars(abs(sobra))}" if sobra < 0 else f"Disponible {fmt_ars(sobra)}"
            fill = f"<div class='prog-fill' style='width:{pct}%;background:{col}'></div>"
            st.markdown(
                "<div class='prog-wrap'>"
                f"<div class='prog-head'><span>{ico} {cat}</span><span>{fmt_ars(gast)} / {fmt_ars(lim)}</span></div>"
                f"<div class='prog-bg'>{fill}</div>"
                f"<div class='prog-note' style='color:{col}'>{note}</div>"
                "</div>", unsafe_allow_html=True)
        t_lim  = pres_df["Limite"].sum()
        t_gast = sum(gastado_cat.get(c, 0) for c in pres_df["Categoria"])
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Presupuestado total</span>"
            f"<span class='total-strip-val'>{fmt_ars(t_gast)} / {fmt_ars(t_lim)}</span>"
            "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — FLUJO DE FONDOS (deuda proyectada por mes, pensado para cuotas)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[7]:
    st.markdown("<div class='sec'>Flujo de fondos — deuda proyectada</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='info-strip'>Cuánto vas a pagar cada mes según las cuotas pendientes de "
        "todas tus tarjetas — incluye compras ya hechas que siguen generando cuotas a futuro.</div>",
        unsafe_allow_html=True
    )

    gastos_flujo_base = load("gastos")
    gastos_flujo_base["Monto"] = to_num(gastos_flujo_base["Monto"])
    gastos_flujo_proyectado = proyectar_cuotas(gastos_flujo_base)

    if gastos_flujo_proyectado.empty:
        st.markdown("<div class='empty'><big>📊</big>Sin gastos cargados todavía.</div>", unsafe_allow_html=True)
    else:
        # Construir los próximos 12 períodos (meses) desde el actual
        meses_flujo = []
        for delta in range(0, 12):
            pm, py = m + delta, y
            while pm > 12: pm -= 12; py += 1
            meses_flujo.append((py, pm))

        # Para cada mes, sumar el total proyectado por tarjeta (usa el período
        # real de cierre de cada tarjeta, igual que el resto de la app)
        filas_tabla = []
        max_total_mes = 1
        datos_por_mes = []
        for py, pm in meses_flujo:
            total_mes = 0
            detalle_tarjeta = {}
            for tname in TARJETAS:
                gf = filtrar_gastos_tarjeta_periodo(gastos_flujo_proyectado, tname, py, pm)
                t_total = to_num(gf["Monto"]).sum() if not gf.empty else 0
                if t_total > 0:
                    detalle_tarjeta[tname] = t_total
                    total_mes += t_total
            datos_por_mes.append((py, pm, total_mes, detalle_tarjeta))
            max_total_mes = max(max_total_mes, total_mes)

        # Barra de proyección mes a mes
        for py, pm, total_mes, detalle_tarjeta in datos_por_mes:
            pct = int(total_mes / max_total_mes * 100) if max_total_mes > 0 else 0
            es_mes_actual = (py == y and pm == m)
            label_mes = f"{calendar.month_name[pm][:3]} {py}" + (" · hoy" if es_mes_actual else "")
            color_barra = "#6c63ff" if es_mes_actual else "#39e07a" if total_mes < max_total_mes*0.5 else "#f5c542" if total_mes < max_total_mes*0.8 else "#ff5f7e"
            fill = f"<div class='prog-fill' style='width:{pct}%;background:{color_barra}'></div>"
            st.markdown(
                "<div class='prog-wrap'>"
                f"<div class='prog-head'><span>{label_mes}</span><span>{fmt_ars(total_mes)}</span></div>"
                f"<div class='prog-bg'>{fill}</div>"
                "</div>", unsafe_allow_html=True
            )

        st.markdown("<div class='sec'>Detalle por mes y tarjeta</div>", unsafe_allow_html=True)
        for py, pm, total_mes, detalle_tarjeta in datos_por_mes:
            if total_mes <= 0:
                continue
            label_mes = f"{calendar.month_name[pm].capitalize()} {py}"
            with st.expander(f"{label_mes} · {fmt_ars(total_mes)}"):
                for tname, t_total in sorted(detalle_tarjeta.items(), key=lambda x: -x[1]):
                    color = get_color_tarjeta(tname, tarjetas_df)
                    st.markdown(
                        "<div class='tarjeta-row'>"
                        f"<div class='tarjeta-pip' style='background:{color}'></div>"
                        f"<div style='flex:1'><div class='tarjeta-label'>{tname}</div></div>"
                        f"<div class='tarjeta-amount' style='color:{color}'>{fmt_ars(t_total)}</div>"
                        "</div>", unsafe_allow_html=True
                    )
                    # Detalle de cuotas individuales de esta tarjeta en este mes
                    gf_detalle = filtrar_gastos_tarjeta_periodo(gastos_flujo_proyectado, tname, py, pm)
                    if not gf_detalle.empty:
                        for _, r in gf_detalle.sort_values("Monto", ascending=False).iterrows():
                            c_act = int(r.get("Cuota actual", 1))
                            c_tot = int(r.get("Cuota total", 1))
                            cuota_txt = f" · {c_act}/{c_tot}" if c_tot > 1 else ""
                            st.markdown(
                                f"<div style='padding:0.3rem 0 0.3rem 1.5rem;font-size:0.78rem;color:#888;"
                                f"display:flex;justify-content:space-between'>"
                                f"<span>{r.get('Concepto','—')}{cuota_txt}</span>"
                                f"<span style='font-family:\"DM Mono\",monospace'>{fmt_ars(r.get('Monto',0))}</span>"
                                f"</div>", unsafe_allow_html=True
                            )

        total_12_meses = sum(t for _,_,t,_ in datos_por_mes)
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total proyectado (12 meses)</span>"
            f"<span class='total-strip-val c-neg'>{fmt_ars(total_12_meses)}</span>"
            "</div>", unsafe_allow_html=True
        )
