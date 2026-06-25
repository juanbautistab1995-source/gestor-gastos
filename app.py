import streamlit as st
import pandas as pd
import os
import re
from datetime import date, datetime, timedelta
import calendar
import io
import requests

# ═════════════════════════════════════════════════════════════════════════════
# REFACTOR JUN-2026 — resumen de cambios grandes en esta versión:
#
# 1) COMPARTIDOS: se eliminó mis_compartidos.csv como entidad separada. Ahora
#    es una VISTA agrupada sobre mis_gastos.csv (columnas Compartido/Con quien/
#    Cuanto recupero, que ya existían). Se agrega/edita "Con quien" directo
#    desde la tabla de gastos de cada tarjeta — no hay alta independiente.
#
# 2) PERÍODOS DE TARJETA: se reemplazó el esquema "año+mes calendario" por una
#    lista de CICLOS REALES (cada uno con su propio rango único de fechas).
#    Esto soluciona el bug donde una tarjeta con ciclo irregular (salto de mes,
#    ej. 28/05->02/07) hacía que dos meses-etiqueta distintos (jun y jul)
#    apuntaran al MISMO rango real, vaciando uno y duplicando datos en el otro.
#    Home y Tarjetas ahora comparten el mismo criterio ("ciclo que incluye
#    hoy"), así que sus totales ya no pueden divergir por esta causa.
#
# 3) CUOTAS: se eliminó la proyección "en memoria" (proyectar_cuotas). Ahora
#    al cargar una compra en N cuotas se generan y GUARDAN N filas reales en
#    el CSV, una por cuota, cada una independiente y editable sin afectar a
#    las demás. Se migra automáticamente (una sola vez) cualquier dato viejo
#    en formato "Cuota X/Y" de una sola fila a su set de filas reales.
#
# 4) TARJETAS: se repuso el botón de blanquear (vaciar gastos), con dos modos:
#    solo el período seleccionado, o reseteo total de la tarjeta.
#
# 5) PESTAÑAS: se eliminaron Inversiones y Presupuesto (no se usaban). Quedan
#    Inicio, Gastos, Tarjetas, Ingresos, Compartidos.
#
# 6) PERFORMANCE: se reemplazaron los iterrows() de proyectar_cuotas/
#    filtrar_gastos_tarjeta_periodo (los más costosos del código viejo) por
#    filtros vectorizados de pandas. Cada CSV se carga una sola vez por
#    render y se reusa entre tabs en vez de volver a leer el archivo.
# ═════════════════════════════════════════════════════════════════════════════

# ── Archivos ───────────────────────────────────────────────────────────────────
FILES = {
    "gastos":   ("mis_gastos.csv",   ["Fecha","Concepto","Monto","Tarjeta","Cuotas","Categoria","Compartido","Con quien","Cuanto recupero","Notas"]),
    "ingresos": ("mis_ingresos.csv", ["Fecha","Concepto","Monto","Categoria"]),
    # "Proximo cierre" (fecha exacta YYYY-MM-DD) y "Dias entre cierres" (intervalo)
    # permiten reflejar tarjetas cuyo ciclo NO cae el mismo día fijo cada mes
    # (ej: Banco Hipotecario salta de 28/05 a 02/07). Si "Proximo cierre" está
    # vacío, se usa el modo simple con "Dia cierre" fijo (compatibilidad vieja).
    "tarjetas": ("mis_tarjetas.csv", ["Nombre","Dia cierre","Dia vencimiento","Color","Cierre anterior","Proximo cierre","Dias entre cierres"]),
    # Log de auditoría: registra ediciones de Fecha/Monto sobre gastos ya
    # existentes (no altas nuevas), para poder rastrear si una cuota se
    # "movió" de período por una corrección manual o por error de tipeo.
    "historial": ("historial_cambios.csv", ["Timestamp","Concepto","Campo","Valor anterior","Valor nuevo","Tarjeta"]),
}

CAT_GASTOS = ["🍔 Comida","🚗 Transporte","🎉 Salidas","✈️ Viaje","🏥 Salud",
               "👕 Ropa","📱 Servicios","🏠 Casa","💊 Farmacia","📚 Educación","🎁 Regalos","💳 Otro"]
CAT_ING    = ["💼 Sueldo","💻 Freelance","📈 Inversión","🎁 Regalo","💰 Otro"]
COLORES_TARJETA = ["#7c6af7","#4ade80","#f87171","#fbbf24","#60a5fa","#f472b6","#34d399","#fb923c"]
TARJETAS_DEFAULT = ["Visa ICBC","Visa Hipotecario","Master ICBC","Efectivo","Débito","Otro"]
_MIGRACION_FLAG = ".cuotas_migradas_v1"

# ── Helpers de fecha ────────────────────────────────────────────────────────────
_MESES_ES = {
    "ene":"01", "feb":"02", "mar":"03", "abr":"04", "may":"05", "jun":"06",
    "jul":"07", "ago":"08", "sep":"09", "oct":"10", "nov":"11", "dic":"12",
}

def _parsear_fecha_es(s):
    """Intenta parsear una fecha en cualquier formato, incluyendo mes en español.
    Devuelve string YYYY-MM-DD o None si no se pudo reconocer."""
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

def _normalizar_fechas_vectorizado(serie):
    """Versión VECTORIZADA de normalizar_fecha_existente para columnas enteras
    (PERFORMANCE FIX): el 95% de las fechas guardadas ya están en formato ISO
    (YYYY-MM-DD) porque siempre se guardan así — para esas, pd.to_datetime
    vectorizado es órdenes de magnitud más rápido que aplicar la función fila
    por fila. Solo cae al parseo lento fila-por-fila para el resto (fechas en
    español, basura, formatos crudos de importación)."""
    s = serie.astype(str).str.strip()
    parsed_rapido = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    faltantes = parsed_rapido.isna()
    resultado = parsed_rapido.dt.strftime("%Y-%m-%d")
    resultado = resultado.where(~faltantes, "")
    if faltantes.any():
        resultado.loc[faltantes] = s[faltantes].apply(normalizar_fecha_existente)
    return resultado

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
            continue
        mask = df[col].astype(str).str.strip().str.lower().isin(_VALORES_NULOS_LITERALES)
        if mask.any():
            df.loc[mask, col] = ""
    return df

def _limpiar_nombre_tarjeta(s):
    """BUG FIX (problema 2 original): un trailing/leading space en 'Tarjeta'
    (típico al pegar desde resúmenes importados) hacía que 'Visa ICBC' y
    'Visa ICBC ' se trataran como dos tarjetas DISTINTAS — el total de Home
    (que sumaba todas las variantes encontradas) no coincidía con el total
    de la pestaña Tarjetas (que filtraba por un nombre exacto del selector).
    Se normaliza SIEMPRE a un único string limpio apenas se carga el CSV."""
    s = str(s).strip()
    return " ".join(s.split())

def load(key):
    f, cols = FILES[key]
    if os.path.exists(f):
        df = pd.read_csv(f, dtype=str).fillna("")
        df = _limpiar_nulos_literales(df)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        df = df[cols]
        if "Fecha" in df.columns:
            df["Fecha"] = _normalizar_fechas_vectorizado(df["Fecha"])
        if "Tarjeta" in df.columns:
            df["Tarjeta"] = df["Tarjeta"].apply(_limpiar_nombre_tarjeta)
        return df
    return pd.DataFrame(columns=cols)

def save(key, df):
    f, _ = FILES[key]
    df = _limpiar_nulos_literales(df.copy())
    if "Tarjeta" in df.columns:
        df["Tarjeta"] = df["Tarjeta"].apply(_limpiar_nombre_tarjeta)
    df.to_csv(f, index=False)

def to_num(series):
    return pd.to_numeric(
        pd.Series(series).astype(str).str.replace(r"[^\d\.\-]", "", regex=True),
        errors="coerce"
    ).fillna(0)

def mes_actual():
    hoy = date.today()
    return hoy.year, hoy.month

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

def sort_by_fecha(df):
    """Ordena por fecha desc. Los None/NaT (sin fecha) van siempre al fondo."""
    if df.empty: return df
    df = df.copy()
    df["_sort"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df = df.sort_values("_sort", ascending=False, na_position="last").drop(columns=["_sort"])
    return df

# ── Historial de cambios (auditoría de ediciones manuales) ─────────────────────
# A pedido explícito: cada cuota es independiente y NO se recalculan las demás
# al editar una. Para no perder trazabilidad si una fecha se corrige (o se
# mueve por error), se guarda un log aparte con valor anterior/nuevo. Esto NO
# cambia ningún comportamiento existente — es solo un registro de auditoría.
def detectar_cambios_fecha_monto(df_antes_con_id, df_despues, campos=("Fecha", "Monto")):
    """Compara, POSICIÓN A POSICIÓN, las filas que ya existían en el período
    (df_antes_con_id, tal como se le pasó al data_editor) contra lo que el
    usuario guardó (df_despues). Streamlit conserva el orden de las filas
    preexistentes aunque se agreguen o borren otras, así que alinear por
    posición hasta min(len(antes), len(despues)) es seguro: filas nuevas
    agregadas por el usuario quedan al final y no se comparan (son altas,
    no ediciones). Devuelve lista de dicts listos para el CSV de historial."""
    cambios = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = min(len(df_antes_con_id), len(df_despues))
    for i in range(n):
        fila_antes = df_antes_con_id.iloc[i]
        fila_despues = df_despues.iloc[i]
        for campo in campos:
            v_antes = str(fila_antes.get(campo, "")).strip()
            v_despues = str(fila_despues.get(campo, "")).strip()
            if v_antes != v_despues:
                cambios.append({
                    "Timestamp": ts,
                    "Concepto": fila_antes.get("Concepto", "?"),
                    "Campo": campo,
                    "Valor anterior": v_antes,
                    "Valor nuevo": v_despues,
                    "Tarjeta": fila_antes.get("Tarjeta", "?"),
                })
    return cambios

def registrar_historial(cambios):
    """Agrega filas nuevas al CSV de historial (append, nunca sobreescribe)."""
    if not cambios:
        return
    nuevas = pd.DataFrame(cambios, columns=FILES["historial"][1])
    existente = load("historial")
    final = pd.concat([existente, nuevas], ignore_index=True)
    save("historial", final)

# ── Cuotas: modelo de FILAS REALES (ver resumen del refactor arriba) ───────────
def parsear_cuotas(val):
    """Interpreta el campo Cuotas en cualquiera de sus formatos posibles:
    - Número simple: "1", "3" -> (cuota_actual=1, total=ese número)
    - Texto de resumen real: "Cuota 1/24", "C.05/06" -> (cuota_actual, total)
    - Vacío/inválido -> (1, 1)
    Se mantiene por compatibilidad con datos viejos / CSVs importados, y
    porque sigue siendo el formato en que se guarda cada fila individual."""
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "n/a", ""):
        return 1, 1
    import re
    m = re.search(r'(\d+)\s*/\s*(\d+)', s)
    if m:
        actual = int(m.group(1))
        total = int(m.group(2))
        if total < 1: total = 1
        if actual < 1: actual = 1
        if actual > total: actual = total
        return actual, total
    n = safe_int(s, 1)
    if n < 1: n = 1
    return 1, n

def fmt_cuotas(actual, total):
    """Formatea el par (actual, total) de vuelta a texto para guardar en el CSV."""
    if total <= 1:
        return "1"
    return f"Cuota {actual}/{total}"

def _sumar_meses(fecha_base, n_meses):
    """Suma n_meses meses calendario a fecha_base, ajustando el día si el mes
    destino tiene menos días (ej: 31 ene + 1 mes -> 28/29 feb, no 31 feb)."""
    mes_total = fecha_base.month - 1 + n_meses
    año = fecha_base.year + mes_total // 12
    mes = mes_total % 12 + 1
    dia = min(fecha_base.day, calendar.monthrange(año, mes)[1])
    return date(año, mes, dia)

def generar_filas_cuotas(fecha_compra, concepto, monto_x_cuota, tarjeta, n_cuotas,
                          categoria, compartido, con_quien, cuanto_recupero, notas):
    """Genera n_cuotas filas REALES nuevas (alta nueva: cuota_actual siempre
    arranca en 1). monto_x_cuota es el valor de UNA cuota — lo que realmente
    se paga cada mes, mismo criterio que usa el resto de la app. Cada fila es
    independiente desde el momento en que se crea: no hay vínculo posterior
    entre ellas, así que editar o borrar una no afecta a las demás (a pedido
    explícito del usuario, por si el banco cobra distinto algún mes puntual)."""
    filas = []
    for n_cuota in range(1, n_cuotas + 1):
        fecha_cuota = _sumar_meses(fecha_compra, n_cuota - 1)
        filas.append({
            "Fecha": fecha_cuota.strftime("%Y-%m-%d"),
            "Concepto": concepto,
            "Monto": monto_x_cuota,
            "Tarjeta": tarjeta,
            "Cuotas": fmt_cuotas(n_cuota, n_cuotas),
            "Categoria": categoria,
            "Compartido": compartido,
            "Con quien": con_quien,
            "Cuanto recupero": cuanto_recupero,
            "Notas": notas,
        })
    return pd.DataFrame(filas, columns=FILES["gastos"][1])

def _generar_filas_cuotas_desde_ancla(fecha_ancla, cuota_actual, n_cuotas, fila_base):
    """Para MIGRACIÓN de datos viejos: fecha_ancla es la fecha que ya estaba
    guardada (corresponde a cuota_actual, no necesariamente la 1/N). Genera
    las n_cuotas filas completas proyectando desde ese ancla hacia atrás y
    hacia adelante, conservando el resto de los campos de fila_base."""
    filas = []
    for n_cuota in range(1, n_cuotas + 1):
        delta = n_cuota - cuota_actual
        fecha_cuota = _sumar_meses(fecha_ancla, delta)
        nueva = dict(fila_base)
        nueva["Fecha"] = fecha_cuota.strftime("%Y-%m-%d")
        nueva["Cuotas"] = fmt_cuotas(n_cuota, n_cuotas)
        filas.append(nueva)
    return filas

def migrar_cuotas_viejas_a_filas_reales(gastos_df):
    """Recorre todo el CSV de gastos una sola vez: cualquier fila cuyo campo
    Cuotas indique más de 1 cuota total ('Cuota X/Y' guardado por la versión
    vieja) se REEMPLAZA por sus N filas reales correspondientes. Filas sin
    cuotas quedan intactas."""
    filas_finales = []
    hubo_cambios = False
    for _, r in gastos_df.iterrows():
        actual, total = parsear_cuotas(r.get("Cuotas", 1))
        fecha_raw = str(r.get("Fecha", "")).strip()
        try:
            fecha_ancla = datetime.strptime(fecha_raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            fecha_ancla = None

        if total <= 1 or fecha_ancla is None:
            filas_finales.append(r.to_dict())
            continue

        hubo_cambios = True
        nuevas = _generar_filas_cuotas_desde_ancla(fecha_ancla, actual, total, r.to_dict())
        filas_finales.extend(nuevas)

    resultado = pd.DataFrame(filas_finales, columns=FILES["gastos"][1]) if filas_finales else gastos_df.copy()
    return resultado, hubo_cambios

def ejecutar_migracion_cuotas_si_corresponde():
    """Corre la migración UNA SOLA VEZ en la vida del CSV (marcada con un
    archivo flag en disco, para que sobreviva entre reinicios del proceso).
    Si ya se migró, no hace nada — evita duplicar filas en cada render."""
    if os.path.exists(_MIGRACION_FLAG):
        return False
    gastos_df = load("gastos")
    if gastos_df.empty:
        open(_MIGRACION_FLAG, "w").close()
        return False
    migrado, hubo_cambios = migrar_cuotas_viejas_a_filas_reales(gastos_df)
    if hubo_cambios:
        migrado["Monto"] = to_num(migrado["Monto"])
        migrado["Cuanto recupero"] = to_num(migrado["Cuanto recupero"])
        migrado = sort_by_fecha(migrado)
        save("gastos", migrado)
    open(_MIGRACION_FLAG, "w").close()
    return hubo_cambios

# ── Tarjetas y ciclos reales (FIX problema 2 — ver resumen del refactor) ───────
def get_tarjetas_nombres(gastos_df, tarjetas_df):
    """Siempre incluye defaults + las configuradas + las que aparecen en
    gastos. Recibe los DataFrames ya cargados (PERFORMANCE FIX: antes esta
    función hacía sus propios load() internos, duplicando lecturas de disco
    en cada llamada)."""
    nombres = list(TARJETAS_DEFAULT)
    if not tarjetas_df.empty:
        for n in tarjetas_df["Nombre"].dropna().tolist():
            n = _limpiar_nombre_tarjeta(n)
            if n and n not in nombres:
                nombres.append(n)
    if not gastos_df.empty and "Tarjeta" in gastos_df.columns:
        for t in gastos_df["Tarjeta"].dropna().unique():
            t = _limpiar_nombre_tarjeta(t)
            if t and t not in ("nan", "None", "") and t not in nombres:
                nombres.append(t)
    return nombres

def _generar_fechas_cierre(tarjeta_row, rango_dias=400):
    """Genera la lista de fechas de cierre (date objects) para una tarjeta,
    cubriendo desde `rango_dias` atrás hasta `rango_dias` adelante de hoy.
    Modo nuevo (preferido): usa 'Cierre anterior' + 'Proximo cierre' para
    calcular el intervalo REAL entre esos dos ciclos específicos, y lo repite
    hacia adelante/atrás — necesario porque el intervalo no es constante en
    algunos bancos (ej: Banco Hipotecario salta de 28/05 a 02/07 = 35 días).
    Si solo hay 'Proximo cierre', usa 'Dias entre cierres' como aproximación.
    Si no hay 'Proximo cierre' configurado, devuelve None (modo día-fijo)."""
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
    f = anterior if anterior else proximo
    while f > hoy - timedelta(days=rango_dias):
        f = f - timedelta(days=intervalo)
        fechas.append(f)
    f = proximo
    while f < hoy + timedelta(days=rango_dias):
        f = f + timedelta(days=intervalo)
        fechas.append(f)
    return sorted(set(fechas))

@st.cache_data(ttl=300, show_spinner=False)
def listar_ciclos_tarjeta(tarjeta_nombre, tarjetas_df_csv_text, n_pasados=8, n_futuros=2):
    """FIX PROBLEMA 2: reemplaza el viejo esquema de 'año+mes calendario' por
    una lista de ciclos REALES, cada uno con su rango de fechas único.
    Antes, una tarjeta con salto de mes en su cierre (ej. 28/05->02/07) hacía
    que dos etiquetas de mes distintas (junio Y julio) apuntaran al MISMO
    rango real — un mes quedaba vacío («$0» fantasma) y el otro se quedaba
    con el total de ambos, sin que hubiera forma de notarlo desde la UI. Acá
    se genera la lista de ciclos DIRECTAMENTE desde las fechas de cierre
    reales, así que cada rango aparece exactamente una vez, sea cual sea el
    mes en que caiga. Recibe tarjetas_df como CSV-texto para poder cachear
    con st.cache_data (los DataFrames no son hasheables de forma estable)."""
    tarjetas_df = pd.read_csv(io.StringIO(tarjetas_df_csv_text), dtype=str) if tarjetas_df_csv_text else pd.DataFrame()
    hoy = date.today()

    if tarjetas_df.empty or tarjeta_nombre not in tarjetas_df["Nombre"].values:
        ciclos = []
        for delta in range(-n_pasados, n_futuros + 1):
            pm, py = hoy.month + delta, hoy.year
            while pm <= 0: pm += 12; py -= 1
            while pm > 12: pm -= 12; py += 1
            ini = date(py, pm, 1)
            fin = date(py, pm, calendar.monthrange(py, pm)[1])
            ciclos.append((ini, fin))
        return sorted(set(ciclos), key=lambda c: c[1], reverse=True)

    row = tarjetas_df[tarjetas_df["Nombre"] == tarjeta_nombre].iloc[0]
    fechas_cierre = _generar_fechas_cierre(row)

    if fechas_cierre:
        cierres_ordenados = sorted(fechas_cierre)
        ciclos = []
        for i in range(1, len(cierres_ordenados)):
            fin = cierres_ordenados[i]
            inicio = cierres_ordenados[i-1] + timedelta(days=1)
            ciclos.append((inicio, fin))
        ventana_atras = hoy - timedelta(days=31 * n_pasados)
        ventana_adelante = hoy + timedelta(days=31 * n_futuros)
        ciclos_relevantes = [c for c in ciclos if c[1] >= ventana_atras and c[0] <= ventana_adelante]
        return sorted(set(ciclos_relevantes), key=lambda c: c[1], reverse=True)

    # Modo simple (día fijo): un ciclo por mes calendario, sin saltos posibles
    dia_cierre = safe_int(row.get("Dia cierre", 1), 1)
    ciclos = []
    for delta in range(-n_pasados, n_futuros + 1):
        pm, py = hoy.month + delta, hoy.year
        while pm <= 0: pm += 12; py -= 1
        while pm > 12: pm -= 12; py += 1
        mes_ant, año_ant = (12, py-1) if pm == 1 else (pm-1, py)
        ultimo_mes_ant = calendar.monthrange(año_ant, mes_ant)[1]
        inicio = date(año_ant, mes_ant, min(dia_cierre+1, ultimo_mes_ant))
        fin = date(py, pm, min(dia_cierre, calendar.monthrange(py, pm)[1]))
        ciclos.append((inicio, fin))
    return sorted(set(ciclos), key=lambda c: c[1], reverse=True)

def ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df):
    """Devuelve el ciclo (inicio, fin) que incluye HOY, o el más reciente que
    ya cerró si por algún motivo no hay uno abierto. Usado tanto por Home
    como por el selector de la pestaña Tarjetas — MISMO criterio en los dos
    lugares, así nunca pueden divergir (causa raíz del problema 2 original)."""
    tarjetas_csv = tarjetas_df.to_csv(index=False) if not tarjetas_df.empty else ""
    ciclos = listar_ciclos_tarjeta(tarjeta_nombre, tarjetas_csv)
    hoy = date.today()
    for ini, fin in sorted(ciclos):
        if ini <= hoy <= fin:
            return (ini, fin)
    # Si ninguno incluye hoy exactamente (hueco raro), el más próximo posterior
    posteriores = [c for c in ciclos if c[0] > hoy]
    if posteriores:
        return min(posteriores)
    return max(ciclos) if ciclos else (date(hoy.year, hoy.month, 1), date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1]))

def filtrar_gastos_tarjeta_rango(gastos_df, tarjeta_nombre, inicio, fin):
    """Filtra gastos de una tarjeta cuya Fecha cae dentro de [inicio, fin].
    PERFORMANCE FIX: reemplaza el viejo filtrar_gastos_tarjeta_periodo, que
    iteraba fila por fila con iterrows() llamando periodo_actual_de_gasto()
    (con su propio loop de fechas de cierre) por cada gasto. Acá se filtra de
    forma vectorizada directo sobre el rango real de fechas — más rápido y
    sin la ambigüedad de 'a qué mes pertenece' que causaba el problema 2."""
    if gastos_df.empty:
        return gastos_df.copy()
    mask_tarjeta = gastos_df["Tarjeta"].astype(str).str.strip() == tarjeta_nombre.strip()
    fechas = pd.to_datetime(gastos_df["Fecha"], errors="coerce").dt.date
    mask_rango = fechas.notna() & (fechas >= inicio) & (fechas <= fin)
    return gastos_df[mask_tarjeta & mask_rango].copy()

def get_color_tarjeta(tname, tarjetas_df):
    if not tarjetas_df.empty and tname in tarjetas_df["Nombre"].values:
        c = str(tarjetas_df[tarjetas_df["Nombre"]==tname].iloc[0].get("Color","#7c6af7"))
        return c if c.startswith("#") else "#7c6af7"
    idx = TARJETAS_DEFAULT.index(tname) if tname in TARJETAS_DEFAULT else 0
    return COLORES_TARJETA[idx % len(COLORES_TARJETA)]

# ── USD / dólar tarjeta ─────────────────────────────────────────────────────────
def es_concepto_usd(concepto):
    """Detecta si un concepto de gasto está en dólares según las marcas que
    usan los resúmenes argentinos: (U$S), (USD), U$S al final, etc."""
    import re
    s = str(concepto).upper()
    return bool(re.search(r'\(\s*U\$S\s*\)|\(\s*USD\s*\)|\bU\$S\b', s))

@st.cache_data(ttl=3600)
def obtener_cotizacion_dolar_tarjeta():
    """Consulta la cotización del 'dólar tarjeta' (oficial + impuestos PAIS/
    ganancias). Cacheada 1 hora. Devuelve el valor de VENTA."""
    try:
        resp = requests.get("https://dolarapi.com/v1/dolares/tarjeta", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        venta = float(data.get("venta", 0))
        return venta if venta > 0 else None
    except Exception:
        return None

def convertir_monto_usd_a_ars(monto_usd, cotizacion=None):
    """Convierte un monto en USD a ARS usando la cotización dólar tarjeta."""
    if cotizacion is None:
        cotizacion = obtener_cotizacion_dolar_tarjeta()
    if cotizacion is None or cotizacion <= 0:
        return None
    try:
        return round(float(monto_usd) * cotizacion, 2)
    except (ValueError, TypeError):
        return None

# ── Importación de movimientos desde texto/CSV pegado ──────────────────────────
def _normalizar_texto(s):
    """Normaliza un string para comparación: minúsculas, sin espacios extra."""
    s = str(s).strip().lower()
    return " ".join(s.split())

def limpiar_csv_montos(texto_csv):
    """Pre-procesa un CSV crudo donde los montos pueden venir con coma de miles
    SIN comillas (ej: 5,000.00) o CON comillas (ej: "224,679.00"), que rompen
    el parseo estándar de CSV porque la coma se confunde con el separador de
    columnas. Junta esos fragmentos en un solo campo antes de leer con pandas."""
    import re
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

def es_duplicado_vectorizado(nuevos_df, gastos_existentes):
    """PERFORMANCE FIX: versión vectorizada de la detección de duplicados
    (antes se llamaba con .apply(axis=1) fila por fila sobre nuevos_df,
    cada llamada recorriendo TODO gastos_existentes con máscaras booleanas
    repetidas). Acá se normalizan ambos lados una sola vez y se hace un
    merge — mismo criterio exacto (Tarjeta+Fecha+Concepto+Monto, tolerancia
    $1), pero sin recalcular las columnas normalizadas en cada fila."""
    if gastos_existentes.empty or nuevos_df.empty:
        return pd.Series([False] * len(nuevos_df), index=nuevos_df.index)

    ex = gastos_existentes.copy()
    ex["_c"] = ex["Concepto"].apply(_normalizar_texto)
    ex["_t"] = ex["Tarjeta"].apply(_normalizar_texto)
    ex["_f"] = ex["Fecha"].apply(_normalizar_texto)
    ex["_m"] = pd.to_numeric(ex["Monto"], errors="coerce")

    nv = nuevos_df.copy()
    nv["_c"] = nv["Concepto"].apply(_normalizar_texto)
    nv["_t"] = nv["Tarjeta"].apply(_normalizar_texto)
    nv["_f"] = nv["Fecha"].apply(_normalizar_texto)
    nv["_m"] = pd.to_numeric(nv["Monto"], errors="coerce")

    resultado = []
    grupos = ex.groupby(["_c", "_t", "_f"])["_m"].apply(list).to_dict()
    for _, r in nv.iterrows():
        clave = (r["_c"], r["_t"], r["_f"])
        montos_existentes = grupos.get(clave, [])
        es_dup = any(abs(m - r["_m"]) < 1.0 for m in montos_existentes if pd.notna(m) and pd.notna(r["_m"]))
        resultado.append(es_dup)
    return pd.Series(resultado, index=nuevos_df.index)

# ── Detección de duplicados "sospechosos" (no exactos, requieren revisión) ────
# El detector de arriba exige Tarjeta+Fecha+Concepto+Monto casi exactos, así
# que se le escapan dos patrones reales que aparecieron en uso: (1) la misma
# compra cargada una vez en USD (con conversión automática a ARS) y otra vez
# en ARS a mano, con montos distintos pero mismo concepto+fecha; y (2) la
# misma compra cargada dos veces con 1-2 días de diferencia de fecha por
# error de tipeo. Estos NO se auto-excluyen (a diferencia de duplicados
# exactos) — se listan para que el usuario decida, porque podrían ser
# compras genuinamente distintas.
def _concepto_base_sin_usd(concepto):
    """Quita el sufijo '(U$S)' para poder comparar la versión en dólares
    contra la versión ya convertida a pesos del mismo concepto."""
    s = _normalizar_texto(concepto)
    return re.sub(r'\(\s*u\$s\s*\)\s*$', '', s).strip()

def detectar_duplicados_usd_ars(gastos_df):
    """Busca pares: mismo concepto base (ignorando '(U$S)') + misma fecha +
    EXACTAMENTE 2 filas, una marcada USD y otra no. Es deliberadamente
    estricto (exactamente 2, una de cada tipo) para no confundir compras
    genuinamente distintas del mismo gateway de pago el mismo día (ej. varios
    consumos de MERPAGO*EBANXSA en la misma fecha, que son normales)."""
    if gastos_df.empty:
        return []
    df = gastos_df.copy()
    df["_concepto_base"] = df["Concepto"].apply(_concepto_base_sin_usd)
    df["_es_usd"] = df["Concepto"].apply(es_concepto_usd)
    sospechosos = []
    for (concepto, fecha), sub in df.groupby(["_concepto_base", "Fecha"]):
        if len(sub) == 2 and sub["_es_usd"].sum() == 1:
            sospechosos.append(sub)
    return sospechosos

def detectar_duplicados_fecha_cercana(gastos_df, ventana_dias=3):
    """Busca pares: mismo concepto EXACTO + mismo monto EXACTO (tolerancia
    $1) + fechas distintas pero separadas por pocos días. Cubre el caso de
    cargar la misma compra dos veces con un error de tipeo en el día."""
    if gastos_df.empty:
        return []
    df = gastos_df.copy().reset_index(drop=True)
    df["_concepto_norm"] = df["Concepto"].apply(_normalizar_texto)
    df["_fecha_dt"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df = df[df["_fecha_dt"].notna()]
    grupos_vistos = set()
    sospechosos = []
    for i, r1 in df.iterrows():
        if i in grupos_vistos:
            continue
        similares = df[
            (df["_concepto_norm"] == r1["_concepto_norm"]) &
            (abs(df["Monto"] - r1["Monto"]) < 1.0) &
            (abs((df["_fecha_dt"] - r1["_fecha_dt"]).dt.days) <= ventana_dias) &
            (df["_fecha_dt"] != r1["_fecha_dt"]) &
            (df.index != i)
        ]
        if not similares.empty:
            grupo_idx = [i] + list(similares.index)
            if not any(idx in grupos_vistos for idx in grupo_idx):
                sospechosos.append(df.loc[grupo_idx])
                grupos_vistos.update(grupo_idx)
    return sospechosos

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
.chip-comp{display:inline-block;background:#39e07a15;border:1px solid #39e07a30;color:#39e07a;border-radius:4px;font-size:0.6rem;padding:1px 5px;margin-left:5px;vertical-align:middle;font-family:'DM Mono',monospace}
div[data-testid="stButton"]>button,div[data-testid="stFormSubmitButton"]>button{background:#6c63ff!important;color:#fff!important;border:none!important;border-radius:8px!important;font-weight:600!important;font-size:0.82rem!important;padding:0.5rem 1rem!important;width:100%!important;font-family:'DM Sans',sans-serif!important;transition:background 0.15s!important}
div[data-testid="stButton"]>button:hover,div[data-testid="stFormSubmitButton"]>button:hover{background:#5a52e0!important;color:#fff!important}
.danger-btn button{background:#ff5f7e!important}
.danger-btn button:hover{background:#e0455f!important}
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
.warn-strip{background:#ff5f7e10;border-left:2px solid #ff5f7e;padding:0.6rem 1rem;font-size:0.75rem;color:#ffb3c0;margin:0.5rem 1rem;border-radius:0 6px 6px 0}
.pend-row{display:flex;align-items:center;padding:0.85rem 1rem;border-bottom:1px solid #14141e;gap:0.75rem;background:#f5c54208}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "gasto_limit" not in st.session_state: st.session_state.gasto_limit = 30
if "menu_accion" not in st.session_state: st.session_state.menu_accion = False

# ── Migración automática de cuotas viejas (una sola vez en la vida del CSV) ────
ejecutar_migracion_cuotas_si_corresponde()

# ── Cargar datos UNA SOLA VEZ por render (PERFORMANCE FIX) ─────────────────────
# Antes, varias pestañas volvían a llamar load("gastos")/load("tarjetas") por
# su cuenta más abajo en el script (hasta 6 lecturas redundantes del mismo CSV
# en un solo render). Ahora se carga una vez acá arriba y se pasa explícito a
# cada función que lo necesita.
gastos_df   = load("gastos")
ingresos_df = load("ingresos")
tarjetas_df = load("tarjetas")

gastos_df["Monto"]           = to_num(gastos_df["Monto"])
gastos_df["Cuanto recupero"] = to_num(gastos_df["Cuanto recupero"])
ingresos_df["Monto"]         = to_num(ingresos_df["Monto"])

gastos_df = sort_by_fecha(gastos_df)

y, m = mes_actual()
nombre_mes = calendar.month_name[m].capitalize()

gastos_mes   = gastos_df[pd.to_datetime(gastos_df["Fecha"], errors="coerce").dt.to_period("M") == pd.Period(year=y, month=m, freq="M")] if not gastos_df.empty else gastos_df
ingresos_mes = ingresos_df[pd.to_datetime(ingresos_df["Fecha"], errors="coerce").dt.to_period("M") == pd.Period(year=y, month=m, freq="M")] if not ingresos_df.empty else ingresos_df

total_ing  = ingresos_mes["Monto"].sum()
total_gast = gastos_mes["Monto"].sum()
recupero   = gastos_mes["Cuanto recupero"].sum()
remanente  = total_ing - total_gast + recupero

TARJETAS = get_tarjetas_nombres(gastos_df, tarjetas_df)

# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class='app-header'>
  <div style='display:flex;justify-content:space-between;align-items:center'>
    <div class='app-brand'>biyuyo<span>.</span></div>
    <div style='font-size:0.6rem;color:#333;font-family:"DM Mono",monospace'>build 2026-06-25</div>
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
            q_m = c2.number_input("Monto x cuota $", min_value=0.0, step=100.0,
                                   help="Si es en cuotas, poné el valor de UNA cuota (lo que pagás cada mes)")
            c3,c4 = st.columns(2)
            q_t = c3.selectbox("Tarjeta", TARJETAS)
            q_k = c4.selectbox("Categoría", CAT_GASTOS)
            c5,c6 = st.columns(2)
            q_f = c5.date_input("Fecha", value=date.today())
            q_cu = c6.number_input("Cuotas", min_value=1, max_value=48, value=1)
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Guardar gasto"):
                if q_c.strip() and q_m > 0:
                    # FIX PROBLEMA 4: en vez de guardar 1 fila con "Cuotas"=N y
                    # dejar que el resto se proyecte en memoria, se generan y
                    # guardan las N filas reales directamente.
                    nv = generar_filas_cuotas(q_f, q_c.strip(), q_m, q_t, int(q_cu),
                                               q_k, "No", "", 0, "")
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    gastos_df = sort_by_fecha(gastos_df)
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
                                        columns=FILES["tarjetas"][1])
                    tarjetas_df = pd.concat([base, fila], ignore_index=True)
                    save("tarjetas", tarjetas_df)
                    listar_ciclos_tarjeta.clear()
                    st.session_state.menu_accion = False
                    st.rerun()
                else:
                    st.warning("Ingresá un nombre.")
            if cb.form_submit_button("✕"):
                st.session_state.menu_accion = False
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ── TABS ──────────────────────────────────────────────────────────────────────
# PESTAÑAS REDUCIDAS (problema 5): se eliminaron Inversiones y Presupuesto.
tabs = st.tabs(["Inicio","Gastos","Tarjetas","Ingresos","Compartidos"])

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

    # Resumen tarjetas — usa el CICLO REAL de cada tarjeta (el que incluye hoy),
    # MISMO criterio que usa el selector de período en la pestaña Tarjetas
    # (FIX problema 2: antes de este fix podían divergir).
    st.markdown("<div class='sec'>Este período (según cierre de cada tarjeta)</div>", unsafe_allow_html=True)
    tarjetas_con_gasto = {}
    ciclo_por_tarjeta = {}
    for tname in TARJETAS:
        ini_t, fin_t = ciclo_actual_de_tarjeta(tname, tarjetas_df)
        ciclo_por_tarjeta[tname] = (ini_t, fin_t)
        gf = filtrar_gastos_tarjeta_rango(gastos_df, tname, ini_t, fin_t)
        total_t = gf["Monto"].sum() if not gf.empty else 0
        if total_t > 0:
            tarjetas_con_gasto[tname] = total_t

    if tarjetas_con_gasto:
        max_t = max(tarjetas_con_gasto.values())
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = get_color_tarjeta(tname, tarjetas_df)
            pct = int(total_t / max_t * 100) if max_t > 0 else 0
            ini_t, fin_t = ciclo_por_tarjeta[tname]
            meta_html = f"<div class='tarjeta-meta-small'>cierra {fin_t.strftime('%d/%m')}</div>"
            bar_fill = f"<div class='tarjeta-bar-fill' style='width:{pct}%;background:{color}'></div>"
            st.markdown(
                "<div class='tarjeta-row'>"
                f"<div class='tarjeta-pip' style='background:{color}'></div>"
                f"<div style='flex:1'><div class='tarjeta-label'>{tname}</div>{meta_html}</div>"
                f"<div class='tarjeta-bar-bg'>{bar_fill}</div>"
                f"<div class='tarjeta-amount c-neg'>{fmt_ars(total_t)}</div>"
                "</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='empty'><big>💸</big>Sin gastos este período.</div>", unsafe_allow_html=True)

    # Últimos movimientos
    st.markdown("<div class='sec'>Últimos movimientos</div>", unsafe_allow_html=True)
    _tiene_fecha = pd.to_datetime(gastos_df["Fecha"], errors="coerce").notna()
    _con_fecha   = gastos_df[_tiene_fecha].head(8)
    _sin_fecha   = gastos_df[~_tiene_fecha].head(max(0, 8 - len(_con_fecha)))
    recientes    = pd.concat([_con_fecha, _sin_fecha], ignore_index=True)
    if recientes.empty:
        st.markdown("<div class='empty'><big>📋</big>Sin movimientos todavía.</div>", unsafe_allow_html=True)
    else:
        for _, r in recientes.iterrows():
            ico = emoji_cat(str(r.get("Categoria","💳")))
            fecha_str = str(r.get("Fecha","")) or "sin fecha"
            tname_r = str(r.get("Tarjeta",""))
            cuota_act_r, cuota_tot_r = parsear_cuotas(r.get("Cuotas", 1))
            cuotas_t = f" · {cuota_act_r}/{cuota_tot_r}" if cuota_tot_r > 1 else ""
            con_quien_r = str(r.get("Con quien","")).strip()
            comp_chip = f"<span class='chip-comp'>🤝 {con_quien_r}</span>" if con_quien_r else ""
            st.markdown(
                "<div class='tx'>"
                f"<div class='tx-ico'>{ico}</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}{comp_chip}</div>"
                f"<div class='tx-info'>{fecha_str} · {tname_r}{cuotas_t}</div>"
                "</div>"
                f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                "</div>", unsafe_allow_html=True)

    # Te deben (compartidos pendientes) — ahora derivado de gastos, no de un
    # CSV separado (FIX problema 1)
    con_persona_df = gastos_df[gastos_df["Con quien"].astype(str).str.strip() != ""] if not gastos_df.empty else gastos_df
    if not con_persona_df.empty and con_persona_df["Cuanto recupero"].sum() > 0:
        st.markdown("<div class='sec'>Te deben</div>", unsafe_allow_html=True)
        pend_home = con_persona_df[con_persona_df["Cuanto recupero"] > 0].sort_values("Fecha", ascending=False).head(6)
        for _, r in pend_home.iterrows():
            st.markdown(
                "<div class='pend-row'>"
                "<div class='tx-ico'>🤝</div>"
                "<div class='tx-main'>"
                f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                f"<div class='tx-info'>{r.get('Con quien','')} · {str(r.get('Fecha',''))[:10]}</div>"
                "</div>"
                f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Cuanto recupero',0))}</div>"
                "</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total a recuperar</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(con_persona_df['Cuanto recupero'].sum())}</span>"
            "</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — GASTOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    with st.expander("📥 Importar desde CSV / foto de resumen"):
        st.markdown(
            "<div class='info-strip'>Pasale tus capturas de resumen a Claude (chat normal) y pedile que te devuelva "
            "el CSV con columnas <code>Fecha,Concepto,Monto,Cuotas</code>. Pegalo acá. "
            "Si el CSV no trae columna Tarjeta, elegí una abajo — se aplica a todas las filas. "
            "Las filas en cuotas se expanden automáticamente a una fila real por cada cuota.</div>",
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

                    # USD -> ARS
                    usd_mask = nuevos["Concepto"].apply(es_concepto_usd)
                    usd_count = int(usd_mask.sum())
                    usd_sin_convertir = 0
                    if usd_count > 0:
                        cotiz = obtener_cotizacion_dolar_tarjeta()
                        if cotiz:
                            nuevos.loc[usd_mask, "Notas"] = nuevos.loc[usd_mask, "Notas"].astype(str) + f" [USD→ARS @ ${cotiz:,.2f}]"
                            nuevos.loc[usd_mask, "Monto"] = nuevos.loc[usd_mask, "Monto"].apply(
                                lambda mo: convertir_monto_usd_a_ars(mo, cotiz)
                            )
                        else:
                            usd_sin_convertir = usd_count

                    # Excluir pagos de tarjeta y ajustes
                    PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior", "pago tarjeta"]
                    concepto_lower = nuevos["Concepto"].astype(str).str.strip().str.lower()
                    es_pago_mask = concepto_lower.isin(PALABRAS_EXCLUIR) | nuevos["Monto"].astype(float).le(0)
                    excluidos_count = int(es_pago_mask.sum())
                    nuevos = nuevos[~es_pago_mask].copy()

                    # FIX PROBLEMA 4: expandir cuotas a filas reales ANTES de
                    # chequear duplicados (cada cuota se compara individualmente
                    # contra lo ya guardado, evitando reimportar una cuota que
                    # ya se había cargado a mano en un mes anterior).
                    filas_expandidas = []
                    for _, r in nuevos.iterrows():
                        actual, total = parsear_cuotas(r.get("Cuotas", 1))
                        try:
                            fecha_ancla = datetime.strptime(str(r["Fecha"])[:10], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            fecha_ancla = None
                        if total <= 1 or fecha_ancla is None:
                            filas_expandidas.append(r.to_dict())
                        else:
                            filas_expandidas.extend(_generar_filas_cuotas_desde_ancla(fecha_ancla, actual, total, r.to_dict()))
                    nuevos = pd.DataFrame(filas_expandidas, columns=FILES["gastos"][1]) if filas_expandidas else nuevos
                    nuevos["Monto"] = to_num(nuevos["Monto"])

                    # Filtrar duplicados (vectorizado) contra lo ya guardado
                    gastos_actuales = load("gastos")
                    gastos_actuales["Monto"] = to_num(gastos_actuales["Monto"])

                    es_dup_mask = es_duplicado_vectorizado(nuevos, gastos_actuales)
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
                    st.markdown(f"<div class='warn-strip'>⚠️ {usd_sin_convertir} gasto(s) en USD detectado(s), pero no se pudo consultar la cotización. Quedan con el monto en USD sin convertir — revisalos a mano.</div>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div class='info-strip'>💵 {usd_count} gasto(s) en USD convertido(s) a ARS con la cotización del dólar tarjeta del día.</div>", unsafe_allow_html=True)
            if excl_count > 0:
                st.markdown(f"<div class='info-strip'>🚫 {excl_count} fila(s) excluida(s) — eran pagos de tarjeta o montos negativos.</div>", unsafe_allow_html=True)
            if dup_count > 0:
                st.markdown(f"<div class='info-strip'>⏭️ {dup_count} movimiento(s) ya existían y se omiten automáticamente.</div>", unsafe_allow_html=True)

            if preview.empty:
                st.markdown("<div class='empty'><big>✅</big>Nada nuevo para importar.</div>", unsafe_allow_html=True)
            else:
                st.caption(f"{len(preview)} movimiento(s) nuevo(s) para importar:")
                for _, r in preview.head(20).iterrows():
                    st.markdown(
                        "<div class='tx'>"
                        f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                        "<div class='tx-main'>"
                        f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                        f"<div class='tx-info'>{str(r.get('Fecha',''))[:10] or 'sin fecha'} · {r.get('Tarjeta','')}</div>"
                        "</div>"
                        f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                        "</div>", unsafe_allow_html=True)
                if len(preview) > 20:
                    st.caption(f"... y {len(preview)-20} más")

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
            "<div class='info-strip'>Descargá un backup completo antes de modificar el código.</div>",
            unsafe_allow_html=True
        )
        if gastos_df.empty:
            st.markdown("<div class='empty'><big>📋</big>No hay gastos cargados todavía.</div>", unsafe_allow_html=True)
        else:
            csv_bytes = gastos_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=f"⬇️ Descargar backup ({len(gastos_df)} movimientos)",
                data=csv_bytes,
                file_name=f"backup_gastos_{date.today().isoformat()}.csv",
                mime="text/csv",
                key="download_backup"
            )

    with st.expander("✏️ Carga manual"):
        with st.form("f_gasto_full", clear_on_submit=True):
            g_c = st.text_input("Concepto", placeholder="Ej: almuerzo, nafta, cuota…")
            c1,c2 = st.columns(2)
            g_m  = c1.number_input("Monto x cuota $", min_value=0.0, step=500.0,
                                    help="Si es en cuotas, poné el valor de UNA cuota")
            g_cu = c2.number_input("Cuotas", min_value=1, max_value=48, value=1)
            c3,c4 = st.columns(2)
            g_t = c3.selectbox("Tarjeta", TARJETAS)
            g_k = c4.selectbox("Categoría", CAT_GASTOS)
            c5,c6 = st.columns(2)
            g_f    = c5.date_input("Fecha", value=date.today())
            g_comp = c6.selectbox("Compartido", ["No","Sí"])
            c7,c8 = st.columns(2)
            g_quien = c7.text_input("Con quién", placeholder="Nombre")
            g_rec   = c8.number_input("Recuperás $ (por cuota)", min_value=0.0, step=100.0) if g_comp == "Sí" else 0.0
            g_nota  = st.text_input("Nota", placeholder="Opcional")
            if st.form_submit_button("Guardar gasto"):
                if g_c.strip() and g_m > 0:
                    # FIX PROBLEMA 4: genera N filas reales (una por cuota) en
                    # vez de una sola fila con "Cuotas"=N.
                    nv = generar_filas_cuotas(g_f, g_c.strip(), g_m, g_t, int(g_cu),
                                               g_k, g_comp, g_quien.strip(), g_rec, g_nota)
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    gastos_df = sort_by_fecha(gastos_df)
                    save("gastos", gastos_df)
                    st.success(f"Guardado: {g_c} — {fmt_ars(g_m)}" + (f" x{g_cu} cuotas" if g_cu > 1 else ""))
                    st.rerun()
                else:
                    st.warning("Completá concepto y monto.")

    with st.expander("🔍 Revisar posibles duplicados"):
        st.markdown(
            "<div class='info-strip'>Busca patrones que el detector automático de importación "
            "no cacha: la misma compra cargada en USD y en ARS por separado, o cargada dos veces "
            "con 1-3 días de diferencia. No se borra nada solo — revisá y decidí.</div>",
            unsafe_allow_html=True
        )
        sospechosos_usd = detectar_duplicados_usd_ars(gastos_df)
        sospechosos_fecha = detectar_duplicados_fecha_cercana(gastos_df)

        if not sospechosos_usd and not sospechosos_fecha:
            st.caption("No se encontraron patrones sospechosos.")
        else:
            if sospechosos_usd:
                st.markdown("**💵 Misma compra en USD y en ARS:**")
                for grupo in sospechosos_usd:
                    for idx, r in grupo.iterrows():
                        ca, cb = st.columns([5, 1])
                        ca.markdown(f"`{r['Fecha']}` **{r['Concepto']}** · {fmt_ars(r['Monto'])}")
                        with cb:
                            if st.button("✕", key=f"dup_usd_{idx}"):
                                gastos_df = gastos_df.drop(index=idx).reset_index(drop=True)
                                save("gastos", gastos_df)
                                st.rerun()
                    st.divider()

            if sospechosos_fecha:
                st.markdown("**📅 Mismo monto y concepto, fecha cercana:**")
                for grupo in sospechosos_fecha:
                    for idx, r in grupo.iterrows():
                        ca, cb = st.columns([5, 1])
                        ca.markdown(f"`{r['Fecha']}` **{r['Concepto']}** · {fmt_ars(r['Monto'])}")
                        with cb:
                            if st.button("✕", key=f"dup_fec_{idx}"):
                                gastos_df = gastos_df.drop(index=idx).reset_index(drop=True)
                                save("gastos", gastos_df)
                                st.rerun()
                    st.divider()

    with st.expander("🗑️ Eliminar gastos"):
        PALABRAS_EXCLUIR = ["su pago en pesos", "pago en pesos", "saldo anterior", "pago tarjeta"]
        concepto_lower_g = gastos_df["Concepto"].astype(str).str.strip().str.lower()
        gastos_monto_num = to_num(gastos_df["Monto"])
        mask_basura = concepto_lower_g.isin(PALABRAS_EXCLUIR) | gastos_monto_num.le(0)
        candidatos_basura = gastos_df[mask_basura]

        if not candidatos_basura.empty:
            st.markdown(f"<div class='warn-strip'>⚠️ Se detectaron {len(candidatos_basura)} fila(s) que parecen pagos de tarjeta o montos inválidos.</div>", unsafe_allow_html=True)
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
                    cuota_act_d, cuota_tot_d = parsear_cuotas(r.get("Cuotas", 1))
                    cuotas_label = f" ({cuota_act_d}/{cuota_tot_d})" if cuota_tot_d > 1 else ""
                    ca,cb = st.columns([5,1])
                    ca.markdown(f"**{r['Concepto']}**{cuotas_label} · {r['Fecha']} · {fmt_ars(r['Monto'])}")
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
            cuota_act, cuota_tot = parsear_cuotas(r.get("Cuotas", 1))
            cuotas_t = f" · {cuota_act}/{cuota_tot}" if cuota_tot > 1 else ""
            con_quien_l = str(r.get("Con quien","")).strip()
            comp_t = f" · {con_quien_l}" if con_quien_l else ""
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
    st.markdown("<div class='sec'>Configuración de tarjetas</div>", unsafe_allow_html=True)

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
            nombres_config = config_rapida["Nombre"].tolist()
            resto = tarjetas_df[~tarjetas_df["Nombre"].isin(nombres_config)] if not tarjetas_df.empty else pd.DataFrame(columns=config_rapida.columns)
            final_tarjetas = pd.concat([config_rapida, resto], ignore_index=True)
            save("tarjetas", final_tarjetas)
            listar_ciclos_tarjeta.clear()
            st.success("✅ Tarjetas configuradas: Visa ICBC, Visa Hipotecario, Master ICBC.")
            st.rerun()
        st.divider()

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

    with st.expander("⚙️ Editar configuración de cierre"):
        st.markdown(
            "<div class='info-strip'>Hay 2 formas de configurar el ciclo:<br>"
            "<strong>① Simple:</strong> completá solo <strong>Día cierre</strong> y <strong>Día vence</strong> "
            "(mismo número todos los meses).<br>"
            "<strong>② Exacta (recomendada si el cierre no es siempre el mismo día):</strong> "
            "completá <strong>Cierre anterior</strong> y <strong>Próximo cierre</strong> con las 2 fechas exactas "
            "de tu resumen. Tiene prioridad sobre el modo simple — si están completas, el Día cierre simple "
            "se ignora por completo para esa tarjeta.<br><br>"
            "⚠️ <strong>Importante:</strong> cambiar estos valores afecta TODOS los períodos, pasados y "
            "futuros — no hay forma de decir 'desde tal mes usá este día'. Si tu tarjeta cambió de día de "
            "cierre en algún momento real, usá el modo Exacto con las 2 fechas de tu último resumen.</div>",
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
                "Dias entre cierres": st.column_config.NumberColumn("Días entre cierres", min_value=20, max_value=45, step=1),
                "Color":              st.column_config.SelectboxColumn("Color", options=COLORES_TARJETA),
            },
            key="editor_tarjetas"
        )

        # FIX: validación de coherencia. Si "Cierre anterior" y "Próximo cierre"
        # están completos pero el intervalo entre ellos no es un mes razonable
        # (28-35 días), avisar ANTES de guardar — un error de tipeo en estas
        # fechas (como pasó con Visa ICBC: un intervalo corto) desincroniza
        # todos los períodos siguientes sin que se note hasta semanas después.
        avisos_coherencia = []
        for _, row_t in edited_t.iterrows():
            nombre_t = str(row_t.get("Nombre", "")).strip()
            ant = row_t.get("Cierre anterior")
            prox = row_t.get("Proximo cierre")
            if nombre_t and pd.notnull(ant) and pd.notnull(prox):
                try:
                    intervalo_dias = (prox - ant).days
                except TypeError:
                    intervalo_dias = None
                if intervalo_dias is not None and not (27 <= intervalo_dias <= 36):
                    avisos_coherencia.append((nombre_t, intervalo_dias, ant, prox))

        if avisos_coherencia:
            for nombre_t, intervalo_dias, ant, prox in avisos_coherencia:
                st.markdown(
                    f"<div class='warn-strip'>⚠️ <b>{nombre_t}</b>: entre {ant} y {prox} hay "
                    f"{intervalo_dias} días — no es un mes típico (28-35 días). Si esto no es a "
                    f"propósito, revisá esas 2 fechas antes de guardar; un intervalo incorrecto "
                    f"acá desincroniza los períodos siguientes.</div>",
                    unsafe_allow_html=True
                )

        c_save, c_clear = st.columns(2)
        with c_save:
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
                listar_ciclos_tarjeta.clear()  # invalidar cache de ciclos tras cambiar config
                st.success("Configuración guardada.")
                st.rerun()
        with c_clear:
            tarjeta_a_limpiar = st.selectbox("Volver a modo simple en…", ["—"] + tarjetas_edit["Nombre"].tolist(), key="tarjeta_limpiar_sel", label_visibility="collapsed")
            if st.button("🧽 Borrar fechas exactas", key="clear_modo_exacto"):
                if tarjeta_a_limpiar != "—":
                    base_t = load("tarjetas")
                    mask_t = base_t["Nombre"].astype(str).str.strip() == tarjeta_a_limpiar.strip()
                    base_t.loc[mask_t, "Cierre anterior"] = ""
                    base_t.loc[mask_t, "Proximo cierre"] = ""
                    save("tarjetas", base_t)
                    listar_ciclos_tarjeta.clear()
                    st.success(f"{tarjeta_a_limpiar} vuelve al modo simple (Día cierre).")
                    st.rerun()

    st.markdown("<div class='sec'>Gastos por tarjeta y período</div>", unsafe_allow_html=True)
    t_sel = st.selectbox("Tarjeta", TARJETAS, key="t_sel_tab")

    # FIX PROBLEMA 2: el selector ahora ofrece los CICLOS REALES de la tarjeta
    # (cada uno con su rango único de fechas) en vez de "año+mes calendario".
    # Esto elimina la posibilidad de que dos etiquetas de mes apunten al mismo
    # rango cuando el cierre salta un mes (ej. Banco Hipotecario 28/05->02/07).
    tarjetas_csv_actual = tarjetas_df.to_csv(index=False) if not tarjetas_df.empty else ""
    ciclos_disponibles = listar_ciclos_tarjeta(t_sel, tarjetas_csv_actual)
    hoy_d = date.today()
    # Determinar índice del ciclo "actual" (el que incluye hoy) para que el
    # selector arranque ahí por default, en vez de en el más reciente/futuro.
    idx_actual = 0
    for i, (ini_c, fin_c) in enumerate(ciclos_disponibles):
        if ini_c <= hoy_d <= fin_c:
            idx_actual = i
            break

    opciones_ciclo = [f"{ini_c.strftime('%d/%m')} → {fin_c.strftime('%d/%m/%y')}  (cierra {calendar.month_name[fin_c.month][:3]} {fin_c.year})"
                       for ini_c, fin_c in ciclos_disponibles]
    idx_sel = st.selectbox("Período (ciclo real de cierre)", range(len(opciones_ciclo)),
                            format_func=lambda i: opciones_ciclo[i], index=idx_actual, key="ciclo_sel_tab")
    inicio_p, fin_p = ciclos_disponibles[idx_sel]

    df_per = filtrar_gastos_tarjeta_rango(gastos_df, t_sel, inicio_p, fin_p)
    total_per = to_num(df_per["Monto"]).sum() if not df_per.empty else 0

    if fin_p < hoy_d:
        badge_class, badge_ico = "closed", "🔒"
    elif inicio_p > hoy_d:
        badge_class, badge_ico = "future", "🔮"
    else:
        dias_r = (fin_p - hoy_d).days
        badge_class, badge_ico = "open", f"🟢 cierra en {dias_r}d"
    st.markdown(
        f"<div class='per-badge {badge_class}'>{badge_ico} · {inicio_p.strftime('%d/%m')} → {fin_p.strftime('%d/%m')}</div>",
        unsafe_allow_html=True)

    if df_per.empty:
        color_t_sel = get_color_tarjeta(t_sel, tarjetas_df)
        st.markdown(
            "<div class='total-strip'>"
            f"<span class='total-strip-label'>{t_sel} · este período</span>"
            f"<span class='total-strip-val' style='color:{color_t_sel}'>−{fmt_ars(total_per)}</span>"
            "</div>", unsafe_allow_html=True)
        st.markdown("<div class='empty'><big>💳</big>Sin gastos en este período.</div>", unsafe_allow_html=True)
    else:
        # _row_id = posición exacta en el CSV completo (0-indexed), estable
        # para esta lectura — necesario para poder guardar cambios sin perder
        # ni duplicar filas que no están en este período.
        gastos_con_id = gastos_df.reset_index(drop=True).copy()
        gastos_con_id["_row_id"] = range(len(gastos_con_id))
        df_per_con_id = filtrar_gastos_tarjeta_rango(gastos_con_id, t_sel, inicio_p, fin_p)
        row_ids_periodo = list(df_per_con_id["_row_id"].astype(int))

        # FIX PROBLEMA 1: "Con quien" se edita DIRECTO en esta misma tabla —
        # no hay alta separada en Compartidos. Al poner un nombre y un monto
        # en "Recupero", ese gasto va a aparecer agrupado en la pestaña
        # Compartidos automáticamente.
        # FIX PROBLEMA 4: "Cuota actual/total" muestran el número real de ESTA
        # fila (que ya es una fila materializada, no una proyección) y son
        # editables — pero ojo, cambiar el total acá NO regenera las otras
        # cuotas (cada fila es independiente, a pedido explícito).
        df_ed = df_per_con_id.drop(columns=["_row_id"], errors="ignore").copy().reset_index(drop=True)
        df_ed["Fecha"]    = df_ed["Fecha"].apply(lambda x: pd.to_datetime(x, errors="coerce").date() if str(x) not in ("S/F","","nan") else None)
        df_ed["Monto"]    = pd.to_numeric(df_ed["Monto"], errors="coerce").fillna(0)
        _cuotas_parseadas = df_ed["Cuotas"].apply(parsear_cuotas)
        df_ed["Cuota actual"] = _cuotas_parseadas.apply(lambda t: t[0])
        df_ed["Cuota total"]  = _cuotas_parseadas.apply(lambda t: t[1])
        df_ed = df_ed.drop(columns=["Cuotas"])
        df_ed["Cuanto recupero"] = pd.to_numeric(df_ed["Cuanto recupero"], errors="coerce").fillna(0)
        for c in ["Concepto","Tarjeta","Categoria","Compartido","Con quien","Notas"]:
            df_ed[c] = df_ed[c].fillna("").astype(str)

        edited_per = st.data_editor(
            df_ed,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Fecha":           st.column_config.DateColumn("Fecha"),
                "Concepto":        st.column_config.TextColumn("Concepto"),
                "Monto":           st.column_config.NumberColumn("Monto $", format="$%d", min_value=0),
                "Cuota actual":    st.column_config.NumberColumn("Cuota actual", min_value=1, max_value=48, step=1, help="Independiente — no afecta a las otras cuotas"),
                "Cuota total":     st.column_config.NumberColumn("Cuota total", min_value=1, max_value=48, step=1),
                "Compartido":      st.column_config.TextColumn("Compartido"),
                "Con quien":       st.column_config.TextColumn("Con quién", help="Poné un nombre acá para que este gasto se agrupe en la pestaña Compartidos"),
                "Cuanto recupero": st.column_config.NumberColumn("Recupero $", format="$%d", min_value=0, help="Cuánto te corresponde recuperar de esta cuota puntual"),
                "Notas":           st.column_config.TextColumn("Notas"),
                "Tarjeta":         st.column_config.TextColumn("Tarjeta"),
                "Categoria":       st.column_config.TextColumn("Categoría"),
            },
            key=f"editor_per_{t_sel}_{inicio_p}_{fin_p}"
        )

        # Total recalculado en vivo desde la tabla editada (no desde el disco),
        # y ubicado DEBAJO de la tabla — antes estaba arriba, donde la barra
        # de herramientas flotante del data_editor (lupa/descarga/expandir) lo
        # tapaba visualmente en pantallas chicas.
        total_per_editado = pd.to_numeric(edited_per["Monto"], errors="coerce").fillna(0).sum()
        color_t_sel = get_color_tarjeta(t_sel, tarjetas_df)
        st.markdown(
            "<div class='total-strip'>"
            f"<span class='total-strip-label'>{t_sel} · este período</span>"
            f"<span class='total-strip-val' style='color:{color_t_sel}'>−{fmt_ars(total_per_editado)}</span>"
            "</div>", unsafe_allow_html=True)

        if st.button("💾 Guardar cambios en gastos", key="save_per"):
            base = load("gastos")
            base["Monto"]           = to_num(base["Monto"])
            base["Cuanto recupero"] = to_num(base["Cuanto recupero"])
            base["_row_id"]         = range(len(base))

            max_id = len(base) - 1
            ids_validos = {i for i in row_ids_periodo if 0 <= i <= max_id}
            if not ids_validos and row_ids_periodo:
                st.error("Error de sincronización. Recargá la página y volvé a intentar.")
                st.stop()

            base_limpia = base[~base["_row_id"].isin(ids_validos)].drop(columns=["_row_id"]).reset_index(drop=True)

            nuevas = edited_per.copy()
            nuevas["Fecha"]           = nuevas["Fecha"].apply(fmt_fecha)
            nuevas["Monto"]           = to_num(nuevas["Monto"])
            nuevas["Cuanto recupero"] = to_num(nuevas["Cuanto recupero"])
            _cuota_act_col = pd.to_numeric(nuevas.get("Cuota actual", 1), errors="coerce").fillna(1).astype(int)
            _cuota_tot_col = pd.to_numeric(nuevas.get("Cuota total", 1), errors="coerce").fillna(1).astype(int)
            nuevas["Cuotas"] = [fmt_cuotas(a, t) for a, t in zip(_cuota_act_col, _cuota_tot_col)]
            nuevas = nuevas.drop(columns=["Cuota actual", "Cuota total"], errors="ignore")
            for col in FILES["gastos"][1]:
                if col not in nuevas.columns:
                    nuevas[col] = ""
            nuevas = nuevas[FILES["gastos"][1]]

            # Historial de auditoría: detecta si Fecha o Monto cambiaron
            # respecto a lo que había antes de editar (df_per_con_id), y lo
            # registra en un log aparte. No cambia el guardado en sí — cada
            # cuota sigue siendo independiente, esto es solo trazabilidad
            # para poder rastrear después si una cuota "se movió" de período.
            cambios_detectados = detectar_cambios_fecha_monto(df_per_con_id, nuevas)
            if cambios_detectados:
                registrar_historial(cambios_detectados)

            final = pd.concat([base_limpia, nuevas], ignore_index=True)
            final = sort_by_fecha(final)
            save("gastos", final)
            if cambios_detectados:
                st.success(f"✅ Guardado. {len(nuevas)} filas actualizadas. {len(cambios_detectados)} cambio(s) registrado(s) en el historial.")
            else:
                st.success(f"✅ Guardado. {len(nuevas)} filas actualizadas.")
            st.rerun()

    with st.expander("🕓 Historial de cambios (fecha/monto editados a mano)"):
        hist_df = load("historial")
        if hist_df.empty:
            st.caption("Todavía no se registró ningún cambio de fecha o monto.")
        else:
            hist_df = hist_df.sort_values("Timestamp", ascending=False)
            for _, hr in hist_df.head(50).iterrows():
                st.markdown(
                    f"<div class='tx-info' style='padding:0.4rem 1rem;border-bottom:1px solid #14141e'>"
                    f"<b>{hr['Concepto']}</b> ({hr['Tarjeta']}) · {hr['Campo']}: "
                    f"<span class='c-neg'>{hr['Valor anterior']}</span> → <span class='c-pos'>{hr['Valor nuevo']}</span>"
                    f"<br><span style='color:#333'>{hr['Timestamp']}</span></div>",
                    unsafe_allow_html=True
                )

    # FIX PROBLEMA 3: botón de blanqueo repuesto, con dos modos.
    with st.expander("🧹 Blanquear tarjeta"):
        st.markdown(
            "<div class='warn-strip'>Esto borra gastos de forma permanente. No se puede deshacer "
            "(hacé un backup desde la pestaña Gastos antes si no estás seguro).</div>",
            unsafe_allow_html=True
        )
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"<div class='danger-btn'>", unsafe_allow_html=True)
            if st.button(f"🗑️ Vaciar solo este período ({len(df_per)} gastos)", key="blanquear_periodo"):
                st.session_state["_confirmar_blanqueo_periodo"] = True
            st.markdown("</div>", unsafe_allow_html=True)
        with c2:
            st.markdown(f"<div class='danger-btn'>", unsafe_allow_html=True)
            cant_total_tarjeta = len(gastos_df[gastos_df["Tarjeta"].astype(str).str.strip() == t_sel.strip()])
            if st.button(f"☢️ Resetear TODA la tarjeta ({cant_total_tarjeta} gastos)", key="blanquear_total"):
                st.session_state["_confirmar_blanqueo_total"] = True
            st.markdown("</div>", unsafe_allow_html=True)

        if st.session_state.get("_confirmar_blanqueo_periodo"):
            st.warning(f"¿Confirmás borrar los {len(df_per)} gastos de {t_sel} en este período ({inicio_p.strftime('%d/%m')}→{fin_p.strftime('%d/%m')})?")
            cc1, cc2 = st.columns(2)
            if cc1.button("Sí, borrar este período", key="confirm_blanq_per"):
                ids_a_borrar = set(df_per_con_id["_row_id"].astype(int)) if not df_per.empty else set()
                base = load("gastos")
                base["_row_id"] = range(len(base))
                resto = base[~base["_row_id"].isin(ids_a_borrar)].drop(columns=["_row_id"]).reset_index(drop=True)
                save("gastos", resto)
                st.session_state["_confirmar_blanqueo_periodo"] = False
                st.success("Período vaciado.")
                st.rerun()
            if cc2.button("Cancelar", key="cancel_blanq_per"):
                st.session_state["_confirmar_blanqueo_periodo"] = False
                st.rerun()

        if st.session_state.get("_confirmar_blanqueo_total"):
            st.warning(f"¿Confirmás borrar TODOS los {cant_total_tarjeta} gastos de {t_sel}, de todos los períodos? Esto NO se puede deshacer.")
            cc1, cc2 = st.columns(2)
            if cc1.button("Sí, resetear todo", key="confirm_blanq_total"):
                base = load("gastos")
                resto = base[base["Tarjeta"].astype(str).str.strip() != t_sel.strip()].reset_index(drop=True)
                save("gastos", resto)
                st.session_state["_confirmar_blanqueo_total"] = False
                st.success(f"{t_sel} reseteada por completo.")
                st.rerun()
            if cc2.button("Cancelar", key="cancel_blanq_total"):
                st.session_state["_confirmar_blanqueo_total"] = False
                st.rerun()


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
    ing_show = ingresos_mes.sort_values("Fecha", ascending=False) if not ingresos_mes.empty else ingresos_mes
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
# TAB 4 — COMPARTIDOS (FIX PROBLEMA 1)
# ══════════════════════════════════════════════════════════════════════════════
# Ya no es una entidad propia (mis_compartidos.csv eliminado). Es una VISTA
# agrupada sobre gastos_df, usando las columnas que ya se completan directo
# en la tabla de la pestaña Tarjetas: "Con quien" (persona) y "Cuanto recupero"
# (cuánto te corresponde recuperar de ESE gasto puntual — puede ser parcial).
with tabs[4]:
    con_persona_df = gastos_df[gastos_df["Con quien"].astype(str).str.strip() != ""].copy() if not gastos_df.empty else gastos_df.copy()

    if con_persona_df.empty:
        st.markdown(
            "<div class='empty'><big>🤝</big>Todavía no compartiste ningún gasto.<br>"
            "Para agregar uno, completá la columna <b>Con quién</b> en la tabla de un gasto "
            "dentro de la pestaña Tarjetas.</div>",
            unsafe_allow_html=True
        )
    else:
        modo = st.radio("Ver por", ["Persona", "Período"], horizontal=True, key="compartidos_modo", label_visibility="collapsed")

        if modo == "Persona":
            # VISTA 1: resumen agrupado por persona, sin filtrar por período —
            # cuánto compartiste en TOTAL con cada una a lo largo del tiempo.
            resumen_personas = con_persona_df.groupby("Con quien").agg(
                total_gastado=("Monto", "sum"),
                total_recupero=("Cuanto recupero", "sum"),
                cant=("Concepto", "count"),
            ).reset_index().sort_values("total_recupero", ascending=False)

            st.markdown("<div class='sec'>Por persona</div>", unsafe_allow_html=True)
            for _, rp in resumen_personas.iterrows():
                tiene_pend = rp["total_recupero"] > 0
                color_val = "c-yel" if tiene_pend else "c-dim"
                sub = f"{int(rp['cant'])} gasto(s) compartido(s)"
                st.markdown(
                    "<div class='tarjeta-row'>"
                    f"<div class='tarjeta-pip' style='background:{'#f5c542' if tiene_pend else '#333'}'></div>"
                    f"<div style='flex:1'><div class='tarjeta-label'>{rp['Con quien']}</div>"
                    f"<div class='tarjeta-meta-small'>{sub}</div></div>"
                    f"<div class='tarjeta-amount {color_val}'>{fmt_ars(rp['total_recupero'])}</div>"
                    "</div>", unsafe_allow_html=True
                )
            st.markdown(
                "<div class='total-strip'>"
                "<span class='total-strip-label'>Total a recuperar (todas las personas)</span>"
                f"<span class='total-strip-val c-yel'>{fmt_ars(con_persona_df['Cuanto recupero'].sum())}</span>"
                "</div>", unsafe_allow_html=True
            )

            personas_lista = resumen_personas["Con quien"].tolist()
            persona_sel = st.selectbox("Ver detalle por período de", ["— elegí una persona —"] + personas_lista, key="persona_detalle_sel")
            if persona_sel != "— elegí una persona —":
                det_persona = con_persona_df[con_persona_df["Con quien"] == persona_sel].copy()
                det_persona["_periodo"] = pd.to_datetime(det_persona["Fecha"], errors="coerce").dt.to_period("M")
                por_periodo = det_persona.groupby("_periodo").agg(
                    total_gastado=("Monto", "sum"), total_recupero=("Cuanto recupero", "sum")
                ).reset_index().sort_values("_periodo", ascending=False)

                st.markdown(f"<div class='sec'>{persona_sel} · por período</div>", unsafe_allow_html=True)
                for _, rp in por_periodo.iterrows():
                    periodo_label = f"{calendar.month_name[rp['_periodo'].month].capitalize()} {rp['_periodo'].year}"
                    st.markdown(
                        "<div class='tarjeta-row'>"
                        "<div class='tarjeta-pip' style='background:#f5c542'></div>"
                        f"<div style='flex:1'><div class='tarjeta-label'>{periodo_label}</div></div>"
                        f"<div class='tarjeta-amount c-yel'>{fmt_ars(rp['total_recupero'])}</div>"
                        "</div>", unsafe_allow_html=True
                    )

                st.markdown(f"<div class='sec'>Detalle de gastos con {persona_sel}</div>", unsafe_allow_html=True)
                for _, r in det_persona.sort_values("Fecha", ascending=False).iterrows():
                    cuota_act_c, cuota_tot_c = parsear_cuotas(r.get("Cuotas", 1))
                    cuotas_c = f" · {cuota_act_c}/{cuota_tot_c}" if cuota_tot_c > 1 else ""
                    st.markdown(
                        "<div class='tx'>"
                        f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                        "<div class='tx-main'>"
                        f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                        f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]} · {r.get('Tarjeta','')}{cuotas_c}</div>"
                        "</div>"
                        f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Cuanto recupero',0))}</div>"
                        "</div>", unsafe_allow_html=True
                    )

        else:  # modo == "Período"
            # VISTA 2: elegís un período (mes calendario) y ves el total
            # compartido en ese mes, desagregado por persona.
            con_persona_df["_periodo"] = pd.to_datetime(con_persona_df["Fecha"], errors="coerce").dt.to_period("M")
            periodos_disponibles = sorted(con_persona_df["_periodo"].dropna().unique(), reverse=True)

            if not periodos_disponibles:
                st.markdown("<div class='empty'><big>📅</big>Sin fechas válidas en los gastos compartidos.</div>", unsafe_allow_html=True)
            else:
                labels_periodo = [f"{calendar.month_name[p.month].capitalize()} {p.year}" for p in periodos_disponibles]
                idx_periodo = st.selectbox("Período", range(len(labels_periodo)), format_func=lambda i: labels_periodo[i], key="periodo_compartido_sel")
                periodo_elegido = periodos_disponibles[idx_periodo]

                del_periodo = con_persona_df[con_persona_df["_periodo"] == periodo_elegido]
                resumen_del_periodo = del_periodo.groupby("Con quien").agg(
                    total_gastado=("Monto", "sum"), total_recupero=("Cuanto recupero", "sum"), cant=("Concepto", "count")
                ).reset_index().sort_values("total_recupero", ascending=False)

                st.markdown(
                    "<div class='total-strip'>"
                    f"<span class='total-strip-label'>Total compartido · {labels_periodo[idx_periodo]}</span>"
                    f"<span class='total-strip-val c-yel'>{fmt_ars(del_periodo['Cuanto recupero'].sum())}</span>"
                    "</div>", unsafe_allow_html=True
                )

                st.markdown("<div class='sec'>Desagregado por persona</div>", unsafe_allow_html=True)
                for _, rp in resumen_del_periodo.iterrows():
                    st.markdown(
                        "<div class='tarjeta-row'>"
                        "<div class='tarjeta-pip' style='background:#f5c542'></div>"
                        f"<div style='flex:1'><div class='tarjeta-label'>{rp['Con quien']}</div>"
                        f"<div class='tarjeta-meta-small'>{int(rp['cant'])} gasto(s)</div></div>"
                        f"<div class='tarjeta-amount c-yel'>{fmt_ars(rp['total_recupero'])}</div>"
                        "</div>", unsafe_allow_html=True
                    )

                st.markdown("<div class='sec'>Detalle del período</div>", unsafe_allow_html=True)
                for _, r in del_periodo.sort_values("Fecha", ascending=False).iterrows():
                    st.markdown(
                        "<div class='tx'>"
                        f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                        "<div class='tx-main'>"
                        f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                        f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]} · {r.get('Con quien','')}</div>"
                        "</div>"
                        f"<div class='tx-amt c-yel'>{fmt_ars(r.get('Cuanto recupero',0))}</div>"
                        "</div>", unsafe_allow_html=True
                    )
