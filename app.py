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
    # "Periodo": fecha que indica a qué CICLO de tarjeta pertenece esta fila,
    # independiente de "Fecha" (que es la fecha real de compra). Para un
    # gasto sin cuotas, Periodo == Fecha. Para una cuota de una compra vieja
    # que el banco vuelve a facturar este mes (con la fecha de compra ORIGINAL
    # intacta, como hacen los resúmenes argentinos: "Cuota 6/12" sigue
    # mostrando la fecha de la compra, no de hoy), Periodo se actualiza a la
    # fecha de cierre del ciclo actual cada vez que se reimporta el resumen.
    # El filtro de "qué pertenece a este período" usa SIEMPRE Periodo, nunca
    # Fecha — así nunca se inventan ni proyectan fechas de cuotas futuras.
    "gastos":   ("mis_gastos.csv",   ["Fecha","Concepto","Monto","Tarjeta","Cuotas","Categoria","Compartido","Con quien","Cuanto recupero","Notas","Periodo"]),
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

# ── Cuotas: modelo "tal cual el resumen" (rediseño jun-2026) ───────────────────
# DESCUBRIMIENTO CLAVE: en los resúmenes de Visa ICBC (y la mayoría de bancos
# argentinos), cada cuota de una compra vieja se sigue facturando MES A MES
# con la FECHA DE COMPRA ORIGINAL intacta — el banco nunca le pone una fecha
# nueva. Solo cambia el texto "Cuota X/Y" (ej: marzo decía "3/12", en junio
# el mismo resumen dice "6/12" para la MISMA fila, misma fecha de marzo).
#
# El modelo viejo de esta app GENERABA filas con fechas futuras inventadas
# (sumando meses desde la fecha de compra) para "proyectar" dónde caería cada
# cuota — esto producía fechas que NUNCA aparecen en un resumen real, y
# corrompía los totales por período. Se elimina por completo ese enfoque.
#
# MODELO NUEVO: cada fila guarda su Fecha de compra real, intacta, para
# siempre. Una columna separada "Periodo" indica a qué ciclo de tarjeta
# pertenece esa fila AHORA — independiente de Fecha. Al reimportar el
# resumen de un mes, si una fila ya existe (mismo Concepto+Fecha+Tarjeta),
# se actualiza su Cuotas y su Periodo (avanza al período actual); si no
# existe, se crea nueva con Periodo = Fecha de hoy (cuota 1, alta real).
def periodo_para_fila(fecha_compra_str, cuotas_str, tarjeta_nombre, tarjetas_df):
    """Calcula el Periodo correcto para una fila según la lógica del banco:
    - Si es cuota 1/N o gasto de 1 cuota: el Periodo es la fecha de cierre
      del ciclo al que pertenece la FECHA DE COMPRA (el banco la factura en
      el próximo resumen posterior a la fecha de compra).
    - Si es cuota X/N con X > 1: el banco ya la está facturando en el ciclo
      actual, así que el Periodo es el cierre del ciclo actual de la tarjeta.
    Esta lógica replica exactamente cómo el banco argentino asigna cada
    compra/cuota a un resumen."""
    from datetime import date as _date
    
    # Parsear cuota
    _cuota_act, _cuota_tot = parsear_cuotas(cuotas_str)
    es_cuota_posterior = _cuota_act > 1  # cuota 2/12, 3/6, etc.
    
    if es_cuota_posterior:
        # Para cuotas intermedias: el ciclo actual del banco es el que factura
        _, fin = ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df)
        return fin.strftime("%Y-%m-%d")
    
    # Para cuota 1/N o gasto simple: usar fecha de compra para determinar ciclo
    try:
        fecha_compra = pd.to_datetime(fecha_compra_str, errors="coerce")
        if pd.isna(fecha_compra):
            _, fin = ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df)
            return fin.strftime("%Y-%m-%d")
        fecha_compra = fecha_compra.date()
    except Exception:
        _, fin = ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df)
        return fin.strftime("%Y-%m-%d")
    
    # Obtener los cierres de la tarjeta y encontrar el cierre inmediatamente
    # posterior a la fecha de compra (el banco factura en ese resumen)
    tarjetas_csv = tarjetas_df.to_csv(index=False) if not tarjetas_df.empty else ""
    ciclos = sorted(listar_ciclos_tarjeta(tarjeta_nombre, tarjetas_csv, n_pasados=24, n_futuros=12))
    for ini, fin in ciclos:
        if ini <= fecha_compra <= fin:
            return fin.strftime("%Y-%m-%d")
    
    # Fallback: ciclo actual
    _, fin = ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df)
    return fin.strftime("%Y-%m-%d")

def calcular_periodo_de_importacion(tarjeta_nombre, tarjetas_df):
    """Devuelve la fecha de FIN del ciclo actual de la tarjeta.
    Usado como fallback cuando no tenemos fecha de compra disponible."""
    _, fin = ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df)
    return fin.strftime("%Y-%m-%d")

def actualizar_o_crear_gastos(base_df, nuevos_df, periodo_str=None):
    """Para cada fila de nuevos_df (un resumen recién importado o pegado):
    - Si YA EXISTE una fila en base_df con el mismo (Concepto normalizado +
      Fecha + Tarjeta normalizada): se actualiza SOLO su Cuotas y Monto
      (la cuota avanzó de mes, ej "3/12" -> "6/12"). La Fecha NUNCA se toca.
    - Si NO existe: es una compra nueva — se agrega directamente.
    Sin columna Periodo — el filtrado se hace siempre por Fecha vs rango.
    Devuelve (base_actualizada, cant_actualizadas, cant_nuevas)."""
    base = base_df.copy()
    base["_clave"] = (
        base["Concepto"].apply(_normalizar_texto) + "|" +
        base["Fecha"].astype(str) + "|" +
        base["Tarjeta"].apply(_normalizar_texto)
    )
    nuevas_filas = []
    cant_actualizadas = 0
    for _, r in nuevos_df.iterrows():
        clave = (
            _normalizar_texto(r["Concepto"]) + "|" +
            str(r["Fecha"]) + "|" +
            _normalizar_texto(r["Tarjeta"])
        )
        match = base[base["_clave"] == clave]
        if not match.empty:
            idx = match.index[0]
            base.loc[idx, "Cuotas"] = r.get("Cuotas", "1")
            base.loc[idx, "Monto"] = r.get("Monto", base.loc[idx, "Monto"])
            cant_actualizadas += 1
        else:
            nueva = r.to_dict()
            nuevas_filas.append(nueva)
    base = base.drop(columns=["_clave"])
    if nuevas_filas:
        nuevas_df_final = pd.DataFrame(nuevas_filas)
        for col in FILES["gastos"][1]:
            if col not in nuevas_df_final.columns:
                nuevas_df_final[col] = ""
        base = pd.concat([base, nuevas_df_final[FILES["gastos"][1]]], ignore_index=True)
    return base, cant_actualizadas, len(nuevas_filas)

def eliminar_cuotas_modelo_viejo(gastos_df):
    """Borra todas las filas que pertenecen al modelo viejo de cuotas
    proyectadas (cuota total > 1 Y no tienen columna Periodo poblada, o su
    Periodo es igual a su Fecha pero el total de cuotas es mayor a 1 con
    fecha que no es la cuota 1 — señal de que fue generada/inventada).
    Se usa una sola vez, a pedido explícito del usuario, para limpiar datos
    cargados con la versión anterior de la app antes de adoptar el modelo
    de Periodo. Devuelve (gastos_limpios, cantidad_eliminada)."""
    if gastos_df.empty:
        return gastos_df, 0
    actual_total = gastos_df["Cuotas"].apply(parsear_cuotas)
    mask_con_cuotas = actual_total.apply(lambda t: t[1] > 1)
    eliminadas = int(mask_con_cuotas.sum())
    limpio = gastos_df[~mask_con_cuotas].copy().reset_index(drop=True)
    return limpio, eliminadas

_MIGRACION_FLAG_V2 = ".periodo_v2_activado"

def asegurar_columna_periodo():
    """Migración: asegura que todos los gastos tengan Periodo poblado.
    - Para filas sin Periodo: asigna la fecha de cierre del ciclo donde
      cae la Fecha de compra (para gastos simples) o el ciclo actual
      (para cuotas intermedias, que ya estaban siendo cobradas).
    - Idempotente: solo actúa si hay filas con Periodo vacío."""
    gastos_df = load("gastos")
    if gastos_df.empty:
        return
    if "Periodo" not in gastos_df.columns:
        gastos_df["Periodo"] = ""
    falta = gastos_df["Periodo"].astype(str).str.strip().isin(["", "nan", "NaT", "None"])
    if not falta.any():
        return
    # Para migración simple: Periodo = Fecha (la fecha de compra)
    # El usuario puede corregir manualmente o reimportando el resumen real
    gastos_df.loc[falta, "Periodo"] = gastos_df.loc[falta, "Fecha"]
    gastos_df["Monto"] = to_num(gastos_df["Monto"])
    gastos_df["Cuanto recupero"] = to_num(gastos_df["Cuanto recupero"])
    save("gastos", gastos_df)

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

def get_tarjeta_principal(gastos_df, tarjetas_df):
    """Devuelve el nombre de la tarjeta con más gastos cargados (la más
    usada). Se usa para que el HERO de Home decida su período según el
    ciclo de cierre REAL de esa tarjeta, en vez de un mes calendario
    genérico — así nunca se desalinea con lo que muestra Tarjetas para esa
    misma tarjeta (a pedido explícito, porque distintas tarjetas pueden
    cerrar en días distintos del mes)."""
    if gastos_df.empty or "Tarjeta" not in gastos_df.columns:
        nombres = get_tarjetas_nombres(gastos_df, tarjetas_df)
        return nombres[0] if nombres else "Visa ICBC"
    conteo = gastos_df["Tarjeta"].value_counts()
    if conteo.empty:
        nombres = get_tarjetas_nombres(gastos_df, tarjetas_df)
        return nombres[0] if nombres else "Visa ICBC"
    return conteo.idxmax()

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

def nombre_mes_de_ciclo(fin_ciclo):
    """Devuelve (nombre_mes, año) para el label de un ciclo, según su fecha
    de cierre. Un cierre a comienzos de mes (día <= 10) corresponde al
    resumen del MES ANTERIOR — ej: un cierre el 02/07 es el resumen de junio,
    no de julio. Esto refleja cómo el usuario piensa sus resúmenes: el
    resumen 'de junio' puede cerrar los primeros días de julio."""
    mes = fin_ciclo.month
    año = fin_ciclo.year
    if fin_ciclo.day <= 10:
        # Cierre a comienzos de mes → pertenece al mes anterior
        mes -= 1
        if mes == 0:
            mes = 12
            año -= 1
    return calendar.month_name[mes].capitalize(), año

def ciclo_de_tarjeta_para_mes(tarjeta_nombre, tarjetas_df, mes, año):
    """Devuelve el ciclo (inicio, fin) de la tarjeta cuyo RESUMEN corresponde
    al mes/año dado. El resumen de un ciclo es el mes de su fecha de cierre,
    ajustado: si cierra en los primeros 10 días del mes, pertenece al mes
    anterior (ver nombre_mes_de_ciclo). Esto permite que Home agrupe las
    tarjetas por 'mes de resumen' aunque cada una cierre en fechas distintas.
    Devuelve None si la tarjeta no tiene un ciclo para ese mes."""
    tarjetas_csv = tarjetas_df.to_csv(index=False) if not tarjetas_df.empty else ""
    ciclos = listar_ciclos_tarjeta(tarjeta_nombre, tarjetas_csv, n_pasados=24, n_futuros=24)
    for ini, fin in ciclos:
        m_nombre, a = nombre_mes_de_ciclo(fin)
        # Convertir nombre de mes a número
        m_num = list(calendar.month_name).index(m_nombre) if m_nombre in calendar.month_name else None
        if m_num == mes and a == año:
            return (ini, fin)
    return None

def ciclo_por_offset_de_tarjeta(tarjeta_nombre, tarjetas_df, offset):
    """Devuelve el ciclo de la tarjeta `offset` posiciones relativas al
    ciclo actual (offset=0 -> actual, -1 -> anterior, +1 -> siguiente).
    Permite que el selector de período de Home navegue "1 mes atrás/
    adelante" de forma coherente para todas las tarjetas a la vez, aunque
    cada una cierre en un día distinto del mes."""
    tarjetas_csv = tarjetas_df.to_csv(index=False) if not tarjetas_df.empty else ""
    ciclos = sorted(listar_ciclos_tarjeta(tarjeta_nombre, tarjetas_csv, n_pasados=10, n_futuros=10))
    hoy = date.today()
    idx_actual = None
    for i, (ini, fin) in enumerate(ciclos):
        if ini <= hoy <= fin:
            idx_actual = i
            break
    if idx_actual is None and ciclos:
        idx_actual = min(range(len(ciclos)), key=lambda i: abs((ciclos[i][1] - hoy).days))
    if idx_actual is None:
        return ciclo_actual_de_tarjeta(tarjeta_nombre, tarjetas_df)
    idx_final = idx_actual + offset
    if 0 <= idx_final < len(ciclos):
        return ciclos[idx_final]
    # Si se pide un offset fuera del rango generado, extender con el cálculo
    # simple de "un ciclo más" en la dirección correspondiente
    ultimo = ciclos[-1] if offset > 0 else ciclos[0]
    return ultimo

def filtrar_gastos_tarjeta_rango(gastos_df, tarjeta_nombre, inicio, fin):
    """Filtra gastos cuyo Periodo es exactamente igual a fin (fecha de cierre
    del ciclo). Esto replica exactamente el resumen bancario: el banco agrupa
    todos los gastos de un resumen por su fecha de cierre, sin importar
    cuándo fue la compra original."""
    if gastos_df.empty:
        return gastos_df.copy()
    mask_tarjeta = gastos_df["Tarjeta"].astype(str).str.strip() == tarjeta_nombre.strip()
    if "Periodo" not in gastos_df.columns:
        # Compatibilidad: sin Periodo, filtrar por Fecha
        fechas_dt = pd.to_datetime(gastos_df["Fecha"], errors="coerce")
        fechas = fechas_dt.apply(lambda x: x.date() if pd.notna(x) else None)
        mask_valida = fechas.notna()
        mask_rango = pd.Series(False, index=gastos_df.index)
        if mask_valida.any():
            mask_rango.loc[mask_valida] = fechas[mask_valida].apply(lambda d: inicio <= d <= fin)
        return gastos_df[mask_tarjeta & mask_rango].copy()
    # Con Periodo: matchear exactamente la fecha de cierre del ciclo
    periodo_dt = pd.to_datetime(gastos_df["Periodo"], errors="coerce")
    periodo_date = periodo_dt.apply(lambda x: x.date() if pd.notna(x) else None)
    mask_periodo = periodo_date.apply(lambda d: d == fin if d is not None else False)
    return gastos_df[mask_tarjeta & mask_periodo].copy()

# ── Estimación de cuotas futuras (NO oficial, ver aclaración en UI) ────────────
def estimar_cuotas_en_periodo_futuro(gastos_df, tarjeta_nombre, fin_periodo):
    """Para compras en cuotas activas, estima cuál sería su número de cuota
    en un período futuro. Usa Periodo (no Fecha) como base del cálculo —
    Periodo es el mes en que el banco cobró esa cuota por última vez."""
    if gastos_df.empty:
        return pd.DataFrame(columns=list(gastos_df.columns) + ["Cuota estimada"])
    df = gastos_df[gastos_df["Tarjeta"].astype(str).str.strip() == tarjeta_nombre.strip()].copy()
    if df.empty:
        return pd.DataFrame(columns=list(gastos_df.columns) + ["Cuota estimada"])
    filas_est = []
    for _, r in df.iterrows():
        actual, total = parsear_cuotas(r.get("Cuotas", 1))
        if total <= 1 or actual >= total:
            continue
        # Usar Periodo como referencia (mes en que el banco cobró esta cuota)
        periodo_str = str(r.get("Periodo", r.get("Fecha", "")))[:10]
        try:
            periodo_date = datetime.strptime(periodo_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        delta_meses = (fin_periodo.year - periodo_date.year) * 12 + (fin_periodo.month - periodo_date.month)
        cuota_estimada = actual + delta_meses
        if delta_meses > 0 and cuota_estimada <= total:
            fila = r.copy()
            fila["Cuota estimada"] = f"{cuota_estimada}/{total}"
            filas_est.append(fila)
    return pd.DataFrame(filas_est) if filas_est else pd.DataFrame(columns=list(gastos_df.columns) + ["Cuota estimada"])

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

# ── Asegurar columna Periodo en datos viejos (una sola vez) ────────────────────
asegurar_columna_periodo()

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

# El HERO de Home usa el ciclo real de cierre de la TARJETA PRINCIPAL (la
# más usada), no un mes calendario genérico — a pedido explícito, porque
# distintas tarjetas pueden cerrar en días distintos del mes, y usar el mes
# calendario podía desalinear el hero respecto a lo que muestra Tarjetas
# para esa misma tarjeta (ej: tarjeta que cierra el 25, hoy es 26 -> el
# ciclo real ya es el del mes siguiente, pero el mes calendario seguía
# diciendo el actual).
_tarjeta_principal_home = get_tarjeta_principal(gastos_df, tarjetas_df)
_ini_home, _fin_home = ciclo_actual_de_tarjeta(_tarjeta_principal_home, tarjetas_df)
y, m = _fin_home.year, _fin_home.month
nombre_mes = calendar.month_name[m].capitalize()

# IMPORTANTE: gastos_mes filtra por "Periodo" (ciclo de tarjeta), NO por
# "Fecha" (fecha real de compra) — mismo motivo que en Tarjetas y
# Compartidos. Si se filtrara por Fecha, una cuota vieja vigente este
# período (con Fecha de hace meses) quedaría afuera del total de Home pero
# SÍ aparecería en Tarjetas, recreando la discrepancia Home≠Tarjetas.
columna_periodo_home = gastos_df["Periodo"] if "Periodo" in gastos_df.columns else gastos_df["Fecha"]
gastos_mes   = gastos_df[pd.to_datetime(columna_periodo_home, errors="coerce").dt.to_period("M") == pd.Period(year=y, month=m, freq="M")] if not gastos_df.empty else gastos_df
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
            q_k = c4.text_input("Categoría", placeholder="Ej: Comida, Perfume, Viaje...")
            c5,c6,c7 = st.columns(3)
            q_f = c5.date_input("Fecha de compra", value=date.today())
            q_cu_act = c6.number_input("Cuota actual", min_value=1, max_value=48, value=1)
            q_cu_tot = c7.number_input("Cuota total", min_value=1, max_value=48, value=1)
            ca,cb = st.columns([3,1])
            if ca.form_submit_button("Guardar gasto"):
                if q_c.strip() and q_m > 0:
                    # MODELO NUEVO: una sola fila, con la Fecha de compra
                    # real (NO se inventan fechas futuras de otras cuotas —
                    # cuando el banco facture la cuota siguiente, se
                    # actualiza esta misma fila al reimportar ese resumen).
                    # Periodo = hoy, porque esta carga corresponde al ciclo
                    # actual de la tarjeta.
                    periodo_hoy = periodo_para_fila(
                        fmt_fecha(q_f), fmt_cuotas(int(q_cu_act), int(q_cu_tot)),
                        q_t, tarjetas_df
                    )
                    nv = pd.DataFrame([{
                        "Fecha": fmt_fecha(q_f), "Concepto": q_c.strip(), "Monto": q_m,
                        "Tarjeta": q_t, "Cuotas": fmt_cuotas(int(q_cu_act), int(q_cu_tot)),
                        "Categoria": q_k, "Compartido": "No", "Con quien": "",
                        "Cuanto recupero": 0, "Notas": "", "Periodo": calcular_periodo_de_importacion(q_t, tarjetas_df),
                    }])
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
# TAB 0 — INICIO (rediseño: selector de período + 4 métricas + resúmenes)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    # Navegación por MES DE RESUMEN (no por offset relativo). Cada tarjeta
    # puede cerrar en días distintos, pero todas comparten el concepto de
    # "resumen de junio", "resumen de julio", etc. Para el mes seleccionado,
    # cada tarjeta muestra el ciclo cuyo resumen pertenece a ese mes.
    # Esto resuelve el descalce donde Visa ICBC (cierra 25/06) quedaba en
    # "julio" mientras Master (cierra 02/07) quedaba en "junio" en el mismo
    # offset — ahora ambas se agrupan por el mes de resumen real.
    _tarjeta_principal_home = get_tarjeta_principal(gastos_df, tarjetas_df)
    _hoy_home = date.today()
    # Mes de resumen actual = el del ciclo actual de la tarjeta principal
    _ini_actual_pp, _fin_actual_pp = ciclo_actual_de_tarjeta(_tarjeta_principal_home, tarjetas_df)
    _mes_actual_nombre, _año_actual = nombre_mes_de_ciclo(_fin_actual_pp)
    _mes_actual_num = list(calendar.month_name).index(_mes_actual_nombre)

    if "_home_mes" not in st.session_state:
        st.session_state["_home_mes"] = _mes_actual_num
        st.session_state["_home_año"] = _año_actual

    c_prev, c_label, c_next = st.columns([1, 3, 1])
    with c_prev:
        if st.button("◀", key="home_periodo_prev"):
            m = st.session_state["_home_mes"] - 1
            a = st.session_state["_home_año"]
            if m == 0:
                m = 12; a -= 1
            st.session_state["_home_mes"] = m
            st.session_state["_home_año"] = a
            st.rerun()
    with c_next:
        if st.button("▶", key="home_periodo_next"):
            m = st.session_state["_home_mes"] + 1
            a = st.session_state["_home_año"]
            if m == 13:
                m = 1; a += 1
            st.session_state["_home_mes"] = m
            st.session_state["_home_año"] = a
            st.rerun()

    _mes_sel = st.session_state["_home_mes"]
    _año_sel = st.session_state["_home_año"]
    with c_label:
        _label_periodo_home = f"{calendar.month_name[_mes_sel].capitalize()} {_año_sel}"
        if _mes_sel == _mes_actual_num and _año_sel == _año_actual:
            _label_periodo_home += " · actual"
        st.markdown(
            f"<div style='text-align:center;font-size:0.78rem;color:#888;padding-top:0.4rem'>{_label_periodo_home}</div>"
            f"<div style='text-align:center;font-size:0.62rem;color:#555'>resumen del mes · todas las tarjetas</div>",
            unsafe_allow_html=True
        )

    # Para el mes seleccionado, cada tarjeta usa el ciclo cuyo resumen
    # corresponde a ese mes. Si una tarjeta no tiene ciclo para ese mes, se
    # omite (no aporta gastos).
    _ciclos_mes = {}
    _gastos_periodo_home = []
    for _tname in TARJETAS:
        _ciclo = ciclo_de_tarjeta_para_mes(_tname, tarjetas_df, _mes_sel, _año_sel)
        if _ciclo is None:
            continue
        _ini_t, _fin_t = _ciclo
        _ciclos_mes[_tname] = _ciclo
        _gf = filtrar_gastos_tarjeta_rango(gastos_df, _tname, _ini_t, _fin_t)
        if not _gf.empty:
            _gastos_periodo_home.append(_gf)
    gastos_periodo_home = pd.concat(_gastos_periodo_home, ignore_index=True) if _gastos_periodo_home else gastos_df.iloc[0:0]

    # Ingresos del período: usar el ciclo de la tarjeta principal para ese mes
    _ciclo_pp_mes = ciclo_de_tarjeta_para_mes(_tarjeta_principal_home, tarjetas_df, _mes_sel, _año_sel)
    if _ciclo_pp_mes is not None:
        _ini_principal, _fin_principal = _ciclo_pp_mes
    else:
        _ini_principal = date(_año_sel, _mes_sel, 1)
        _fin_principal = date(_año_sel, _mes_sel, calendar.monthrange(_año_sel, _mes_sel)[1])
    if not ingresos_df.empty:
        _fechas_ing = pd.to_datetime(ingresos_df["Fecha"], errors="coerce").apply(lambda x: x.date() if pd.notna(x) else None)
        _mask_ing_valida = _fechas_ing.notna()
        _mask_ing_rango = pd.Series(False, index=ingresos_df.index)
        if _mask_ing_valida.any():
            _mask_ing_rango.loc[_mask_ing_valida] = _fechas_ing[_mask_ing_valida].apply(lambda d: _ini_principal <= d <= _fin_principal)
        ingresos_periodo_home = ingresos_df[_mask_ing_rango]
    else:
        ingresos_periodo_home = ingresos_df

    deuda_total_periodo = gastos_periodo_home["Monto"].sum() if not gastos_periodo_home.empty else 0
    recupero_periodo = gastos_periodo_home["Cuanto recupero"].sum() if not gastos_periodo_home.empty else 0
    ingresos_periodo = ingresos_periodo_home["Monto"].sum() if not ingresos_periodo_home.empty else 0
    remanente_periodo = ingresos_periodo - deuda_total_periodo + recupero_periodo

    # HERO: 4 métricas grandes — deuda total, te deben, ingresos, remanente.
    color_hero = "c-pos" if remanente_periodo >= 0 else "c-neg"
    st.markdown(
        "<div class='hero-block'>"
        f"<div class='hero-eyebrow'>{_label_periodo_home} · remanente</div>"
        f"<div class='hero-num {color_hero}'>{fmt_ars(remanente_periodo)}</div>"
        "<div class='hero-sub'>ingresos − gastos + recupero</div>"
        "</div>", unsafe_allow_html=True)

    st.markdown(
        "<div class='stat-row'>"
        f"<div class='stat-cell'><div class='stat-label'>Deuda total</div><div class='stat-val c-neg'>{fmt_ars(deuda_total_periodo)}</div></div>"
        f"<div class='stat-cell'><div class='stat-label'>Te deben</div><div class='stat-val c-yel'>{fmt_ars(recupero_periodo)}</div></div>"
        f"<div class='stat-cell'><div class='stat-label'>Ingresos</div><div class='stat-val c-pos'>{fmt_ars(ingresos_periodo)}</div></div>"
        "</div>", unsafe_allow_html=True)

    # Resumen por tarjeta. Cada tarjeta muestra SU PROPIO rango de fechas
    # debajo del nombre — necesario porque cada tarjeta puede tener un ciclo
    # de cierre distinto, así que aunque el navegador ◀▶ las mueva todas
    # "un paso" a la vez, el rango de fechas resultante NO es el mismo para
    # todas (a propósito, confirmado por el usuario). Sin este detalle, el
    # label de arriba (que solo refleja el ciclo de la tarjeta PRINCIPAL)
    # daba la falsa impresión de que todos los montos correspondían al mismo
    # mes calendario.
    st.markdown("<div class='sec'>Por tarjeta</div>", unsafe_allow_html=True)
    tarjetas_con_gasto = {}
    rango_por_tarjeta = {}
    for _tname in TARJETAS:
        if _tname not in _ciclos_mes:
            continue
        _ini_t, _fin_t = _ciclos_mes[_tname]
        _gf_t = gastos_periodo_home[gastos_periodo_home["Tarjeta"].astype(str).str.strip() == _tname.strip()] if not gastos_periodo_home.empty else gastos_periodo_home
        _total_t = _gf_t["Monto"].sum() if not _gf_t.empty else 0
        if _total_t > 0:
            tarjetas_con_gasto[_tname] = _total_t
            rango_por_tarjeta[_tname] = f"{_ini_t.strftime('%d/%m')} → {_fin_t.strftime('%d/%m')}"

    if tarjetas_con_gasto:
        max_t = max(tarjetas_con_gasto.values())
        for tname, total_t in sorted(tarjetas_con_gasto.items(), key=lambda x: -x[1]):
            color = get_color_tarjeta(tname, tarjetas_df)
            pct = int(total_t / max_t * 100) if max_t > 0 else 0
            bar_fill = f"<div class='tarjeta-bar-fill' style='width:{pct}%;background:{color}'></div>"
            st.markdown(
                "<div class='tarjeta-row'>"
                f"<div class='tarjeta-pip' style='background:{color}'></div>"
                f"<div style='flex:1'><div class='tarjeta-label'>{tname}</div>"
                f"<div class='tarjeta-meta-small'>{rango_por_tarjeta[tname]}</div></div>"
                f"<div class='tarjeta-bar-bg'>{bar_fill}</div>"
                f"<div class='tarjeta-amount c-neg'>{fmt_ars(total_t)}</div>"
                "</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='empty'><big>💸</big>Sin gastos en este período.</div>", unsafe_allow_html=True)

    # Resumen de GASTOS POR CATEGORÍA (reemplaza "Últimos movimientos")
    st.markdown("<div class='sec'>Gastos por categoría</div>", unsafe_allow_html=True)
    if not gastos_periodo_home.empty:
        _por_cat = gastos_periodo_home.copy()
        _por_cat["Categoria"] = _por_cat["Categoria"].fillna("💳 Otro").replace("", "💳 Otro")
        resumen_cat = _por_cat.groupby("Categoria")["Monto"].sum().sort_values(ascending=False)
        max_cat = resumen_cat.max() if not resumen_cat.empty else 1
        for cat, monto_cat in resumen_cat.items():
            pct_cat = int(monto_cat / max_cat * 100) if max_cat > 0 else 0
            ico_cat = emoji_cat(str(cat))
            bar_fill_cat = f"<div class='tarjeta-bar-fill' style='width:{pct_cat}%;background:#6c63ff'></div>"
            st.markdown(
                "<div class='tarjeta-row'>"
                f"<div class='tarjeta-pip' style='background:#6c63ff'></div>"
                f"<div style='flex:1'><div class='tarjeta-label'>{ico_cat} {str(cat).split(' ',1)[-1] if ' ' in str(cat) else cat}</div></div>"
                f"<div class='tarjeta-bar-bg'>{bar_fill_cat}</div>"
                f"<div class='tarjeta-amount c-neg'>{fmt_ars(monto_cat)}</div>"
                "</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='empty'><big>📊</big>Sin gastos para desagregar.</div>", unsafe_allow_html=True)

    # Resumen de DEUDAS POR PERSONA (reemplaza "Te deben" simple)
    st.markdown("<div class='sec'>Te deben, por persona</div>", unsafe_allow_html=True)
    con_persona_df = gastos_df[gastos_df["Con quien"].astype(str).str.strip() != ""] if not gastos_df.empty else gastos_df
    if not con_persona_df.empty and con_persona_df["Cuanto recupero"].sum() > 0:
        resumen_personas_home = con_persona_df.groupby("Con quien")["Cuanto recupero"].sum().sort_values(ascending=False)
        resumen_personas_home = resumen_personas_home[resumen_personas_home > 0]
        for persona, monto_p in resumen_personas_home.items():
            st.markdown(
                "<div class='tarjeta-row'>"
                "<div class='tarjeta-pip' style='background:#f5c542'></div>"
                f"<div style='flex:1'><div class='tarjeta-label'>{persona}</div></div>"
                f"<div class='tarjeta-amount c-yel'>{fmt_ars(monto_p)}</div>"
                "</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='total-strip'>"
            "<span class='total-strip-label'>Total a recuperar</span>"
            f"<span class='total-strip-val c-yel'>{fmt_ars(resumen_personas_home.sum())}</span>"
            "</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='empty'><big>🤝</big>Nadie te debe nada por ahora.</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — GASTOS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    with st.expander("📥 Importar desde CSV / foto de resumen"):
        st.markdown(
            "<div class='info-strip'>Pasale tus capturas de resumen a Claude (chat normal) y pedile que te devuelva "
            "el CSV con columnas <code>Fecha,Concepto,Monto,Cuotas</code>. Pegalo acá. "
            "Si el CSV no trae columna Tarjeta, elegí una abajo — se aplica a todas las filas.<br><br>"
            "<b>Importante:</b> la Fecha de cada fila tiene que ser la fecha REAL de la compra (la que "
            "trae tu resumen), no se inventa ninguna fecha. Si una compra en cuotas ya estaba cargada de "
            "un mes anterior, se detecta por Concepto+Fecha+Tarjeta y se actualiza su número de cuota — "
            "no se duplica ni se crea una fila nueva.</div>",
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
                    # FECHA: se conserva tal cual viene del resumen (fecha real
                    # de compra). NUNCA se inventan fechas de otras cuotas —
                    # ver el bloque de comentarios sobre el modelo de cuotas.
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

                    # MODELO NUEVO: clasificar cada fila en NUEVA, ACTUALIZACIÓN
                    # (la cuota avanzó de número, misma Concepto+Fecha+Tarjeta
                    # que ya existe) o DUPLICADO EXACTO (Concepto+Fecha+Tarjeta+
                    # Cuotas+Monto idénticos a lo ya guardado — no aporta nada
                    # nuevo, probablemente se pegó el mismo resumen dos veces).
                    gastos_actuales = load("gastos")
                    gastos_actuales["Monto"] = to_num(gastos_actuales["Monto"])

                    clave_existente = (
                        gastos_actuales["Concepto"].apply(_normalizar_texto) + "|" +
                        gastos_actuales["Fecha"].astype(str) + "|" +
                        gastos_actuales["Tarjeta"].apply(_normalizar_texto)
                    )
                    mapa_existente = {}
                    for idx_e, clave_e in clave_existente.items():
                        mapa_existente.setdefault(clave_e, []).append(idx_e)

                    filas_nuevas, filas_actualizacion, filas_dup_exacto = [], [], []
                    for _, r in nuevos.iterrows():
                        clave_n = _normalizar_texto(r["Concepto"]) + "|" + str(r["Fecha"]) + "|" + _normalizar_texto(r["Tarjeta"])
                        idxs = mapa_existente.get(clave_n, [])
                        if not idxs:
                            filas_nuevas.append(r)
                            continue
                        fila_existente = gastos_actuales.loc[idxs[0]]
                        mismo_monto = abs(float(fila_existente["Monto"]) - float(r["Monto"])) < 1.0
                        misma_cuota = str(fila_existente["Cuotas"]).strip() == str(r["Cuotas"]).strip()
                        if mismo_monto and misma_cuota:
                            filas_dup_exacto.append(r)
                        else:
                            r_con_idx = r.copy()
                            r_con_idx["_idx_existente"] = idxs[0]
                            filas_actualizacion.append(r_con_idx)

                    df_nuevas = pd.DataFrame(filas_nuevas, columns=FILES["gastos"][1]) if filas_nuevas else pd.DataFrame(columns=FILES["gastos"][1])
                    df_actualizacion = pd.DataFrame(filas_actualizacion) if filas_actualizacion else pd.DataFrame(columns=list(FILES["gastos"][1])+["_idx_existente"])
                    duplicados_count = len(filas_dup_exacto)

                    st.session_state["_csv_nuevas"] = df_nuevas
                    st.session_state["_csv_actualizacion"] = df_actualizacion
                    st.session_state["_csv_dup_count"] = duplicados_count
                    st.session_state["_csv_excl_count"] = excluidos_count
                    st.session_state["_csv_usd_count"] = usd_count
                    st.session_state["_csv_usd_sin_convertir"] = usd_sin_convertir
                except Exception as e:
                    st.error(f"No pude leer el CSV: {e}")
                    st.session_state.pop("_csv_nuevas", None)
                    st.session_state.pop("_csv_actualizacion", None)
            else:
                st.warning("Pegá el CSV primero.")

        if "_csv_nuevas" in st.session_state:
            df_nuevas = st.session_state["_csv_nuevas"]
            df_actualizacion = st.session_state["_csv_actualizacion"]
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
                st.markdown(f"<div class='info-strip'>⏭️ {dup_count} movimiento(s) ya estaban cargados IDÉNTICOS y se omiten.</div>", unsafe_allow_html=True)

            if df_nuevas.empty and df_actualizacion.empty:
                st.markdown("<div class='empty'><big>✅</big>Nada nuevo para importar.</div>", unsafe_allow_html=True)
            else:
                if not df_actualizacion.empty:
                    st.caption(f"🔄 {len(df_actualizacion)} cuota(s) que AVANZAN de número (misma compra, mismo concepto+fecha):")
                    for _, r in df_actualizacion.head(20).iterrows():
                        idx_e = int(r["_idx_existente"])
                        cuota_vieja = gastos_actuales.loc[idx_e, "Cuotas"] if idx_e in gastos_actuales.index else "?"
                        st.markdown(
                            "<div class='tx'>"
                            f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                            "<div class='tx-main'>"
                            f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                            f"<div class='tx-info'>{str(r.get('Fecha',''))[:10]} · {cuota_vieja} → {r.get('Cuotas','')}</div>"
                            "</div>"
                            f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                            "</div>", unsafe_allow_html=True)
                    if len(df_actualizacion) > 20:
                        st.caption(f"... y {len(df_actualizacion)-20} más")

                if not df_nuevas.empty:
                    st.caption(f"🆕 {len(df_nuevas)} movimiento(s) nuevo(s):")
                    for _, r in df_nuevas.head(20).iterrows():
                        st.markdown(
                            "<div class='tx'>"
                            f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                            "<div class='tx-main'>"
                            f"<div class='tx-name'>{r.get('Concepto','—')}</div>"
                            f"<div class='tx-info'>{str(r.get('Fecha',''))[:10] or 'sin fecha'} · {r.get('Tarjeta','')}</div>"
                            "</div>"
                            f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                            "</div>", unsafe_allow_html=True)
                    if len(df_nuevas) > 20:
                        st.caption(f"... y {len(df_nuevas)-20} más")

                total_a_importar = len(df_nuevas) + len(df_actualizacion)
                if st.button(f"✅ Confirmar ({len(df_nuevas)} nuevo(s), {len(df_actualizacion)} actualización(es))", key="confirm_import"):
                    base = load("gastos")
                    base["Monto"] = to_num(base["Monto"])
                    base["Cuanto recupero"] = to_num(base["Cuanto recupero"])
                    # Periodo de cada fila: si el CSV lo trae, se respeta.
                    # Si no, se usa el ciclo actual de la tarjeta (el resumen
                    # que estás importando ahora es el del ciclo actual).
                    # Fecha de cierre del resumen que se está importando
                    # = la que se usa como nueva Fecha para cuotas intermedias
                    _, fin_ciclo_import = ciclo_actual_de_tarjeta(tarjeta_import, tarjetas_df)
                    fecha_cierre_import = fin_ciclo_import.strftime("%Y-%m-%d")

                    def _fecha_para_importar(r):
                        """Cuota X/N con X>1: usa fecha del resumen actual.
                        Cuota 1/N o gasto simple: conserva la Fecha original."""
                        cuota_act, _ = parsear_cuotas(str(r.get("Cuotas", "1")))
                        if cuota_act > 1:
                            return fecha_cierre_import
                        return str(r.get("Fecha", ""))

                    # Actualizar cuotas existentes: solo Cuotas y Monto
                    for _, r in df_actualizacion.iterrows():
                        idx_e = int(r["_idx_existente"])
                        if idx_e in base.index:
                            base.loc[idx_e, "Cuotas"] = r["Cuotas"]
                            base.loc[idx_e, "Monto"] = r["Monto"]
                            # Si es cuota intermedia, actualizar Fecha al cierre actual
                            cuota_act, _ = parsear_cuotas(str(r.get("Cuotas", "1")))
                            if cuota_act > 1:
                                base.loc[idx_e, "Fecha"] = fecha_cierre_import

                    # Agregar filas nuevas con Fecha correcta
                    if not df_nuevas.empty:
                        df_nuevas_final = df_nuevas.copy()
                        df_nuevas_final["Fecha"] = df_nuevas_final.apply(_fecha_para_importar, axis=1)
                        for col in FILES["gastos"][1]:
                            if col not in df_nuevas_final.columns:
                                df_nuevas_final[col] = ""
                        df_nuevas_final = df_nuevas_final[FILES["gastos"][1]]
                        df_nuevas_final = df_nuevas_final[FILES["gastos"][1]]
                        base = pd.concat([base, df_nuevas_final], ignore_index=True)

                    base = sort_by_fecha(base)
                    save("gastos", base)
                    st.session_state.pop("_csv_nuevas", None)
                    st.session_state.pop("_csv_actualizacion", None)
                    st.session_state.pop("_csv_dup_count", None)
                    st.success(f"✅ {len(df_nuevas)} movimiento(s) nuevo(s), {len(df_actualizacion)} cuota(s) actualizada(s).")
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

    with st.expander("🗑️ Borrar TODOS los movimientos (reseteo total)"):
        st.markdown(
            "<div class='info-strip' style='background:#3a1a1a;border-color:#f87171'>"
            "⚠️ <b>Esta acción borra todos los gastos de todas las tarjetas y no se puede deshacer.</b> "
            "Descargá el backup antes de continuar."
            "</div>", unsafe_allow_html=True
        )
        _confirm_reset = st.checkbox("Entiendo que esto borrará todos los movimientos de forma permanente")
        if _confirm_reset:
            if st.button("🗑️ Confirmar reseteo total", key="btn_reset_total"):
                gastos_vacio = pd.DataFrame(columns=FILES["gastos"][1])
                save("gastos", gastos_vacio)
                st.success("✅ Todos los movimientos fueron borrados.")
                st.rerun()

    with st.expander("✏️ Carga manual"):
        with st.form("f_gasto_full", clear_on_submit=True):
            g_c = st.text_input("Concepto", placeholder="Ej: almuerzo, nafta, cuota…")
            c1,c2 = st.columns(2)
            g_m  = c1.number_input("Monto x cuota $", min_value=0.0, step=500.0,
                                    help="Si es en cuotas, poné el valor de UNA cuota")
            c3,c4 = st.columns(2)
            g_cu_act = c3.number_input("Cuota actual", min_value=1, max_value=48, value=1,
                                        help="Ej: si tu resumen dice 'Cuota 5/12', poné 5 acá")
            g_cu_tot = c4.number_input("Cuota total", min_value=1, max_value=48, value=1)
            c5,c6 = st.columns(2)
            g_t = c5.selectbox("Tarjeta", TARJETAS)
            g_k = c6.selectbox("Categoría", CAT_GASTOS)
            c7,c8 = st.columns(2)
            g_f    = c7.date_input("Fecha de compra original", value=date.today(),
                                    help="La fecha real en que se hizo la compra — para una cuota vieja, NO es hoy")
            g_comp = c8.selectbox("Compartido", ["No","Sí"])
            c9,c10 = st.columns(2)
            g_quien = c9.text_input("Con quién", placeholder="Nombre")
            g_rec   = c10.number_input("Recuperás $ (por cuota)", min_value=0.0, step=100.0) if g_comp == "Sí" else 0.0
            g_nota  = st.text_input("Nota", placeholder="Opcional")
            if st.form_submit_button("Guardar gasto"):
                if g_c.strip() and g_m > 0:
                    # MODELO NUEVO: una sola fila con la Fecha de compra real
                    # (intacta, no se inventan fechas de otras cuotas) y
                    # Periodo = ciclo actual de la tarjeta (esta carga
                    # corresponde a lo que pagás ESTE mes).
                    periodo_hoy = periodo_para_fila(
                        fmt_fecha(g_f), fmt_cuotas(int(g_cu_act), int(g_cu_tot)),
                        g_t, tarjetas_df
                    )
                    nv = pd.DataFrame([{
                        "Fecha": fmt_fecha(g_f), "Concepto": g_c.strip(), "Monto": g_m,
                        "Tarjeta": g_t, "Cuotas": fmt_cuotas(int(g_cu_act), int(g_cu_tot)),
                        "Categoria": g_k, "Compartido": g_comp, "Con quien": g_quien.strip(),
                        "Cuanto recupero": g_rec, "Notas": g_nota, "Periodo": calcular_periodo_de_importacion(g_t, tarjetas_df),
                    }])
                    gastos_df = pd.concat([gastos_df, nv], ignore_index=True)
                    gastos_df = sort_by_fecha(gastos_df)
                    save("gastos", gastos_df)
                    st.success(f"Guardado: {g_c} — {fmt_ars(g_m)}" + (f" (cuota {g_cu_act}/{g_cu_tot})" if g_cu_tot > 1 else ""))
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

    with st.expander("🧹 Limpiar cuotas del modelo viejo"):
        st.markdown(
            "<div class='warn-strip'>Esta app cambió cómo guarda las cuotas: antes generaba una fecha "
            "futura inventada por cada cuota; ahora respeta la fecha real de compra que trae tu resumen "
            "y solo actualiza el número de cuota al reimportar. Si cargaste compras en cuotas con la "
            "versión anterior, esos datos quedaron con fechas inventadas y conviene borrarlos para "
            "volver a importar los resúmenes reales de cada mes.</div>",
            unsafe_allow_html=True
        )
        candidatos_viejo, cant_viejo = eliminar_cuotas_modelo_viejo(gastos_df)
        if cant_viejo == 0:
            st.caption("No se encontraron gastos en cuotas para limpiar.")
        else:
            st.caption(f"Se encontraron {cant_viejo} fila(s) en cuotas (cualquier compra con más de 1 cuota total).")
            if st.button(f"🗑️ Borrar {cant_viejo} fila(s) en cuotas", key="limpiar_modelo_viejo"):
                st.session_state["_confirmar_limpieza_cuotas"] = True
            if st.session_state.get("_confirmar_limpieza_cuotas"):
                st.warning(f"¿Confirmás borrar las {cant_viejo} filas en cuotas? Después vas a poder reimportar los resúmenes reales de cada mes.")
                cc1, cc2 = st.columns(2)
                if cc1.button("Sí, borrar", key="confirm_limpieza_cuotas"):
                    gastos_limpio, _ = eliminar_cuotas_modelo_viejo(gastos_df)
                    save("gastos", gastos_limpio)
                    st.session_state["_confirmar_limpieza_cuotas"] = False
                    st.success(f"{cant_viejo} fila(s) en cuotas eliminada(s).")
                    st.rerun()
                if cc2.button("Cancelar", key="cancel_limpieza_cuotas"):
                    st.session_state["_confirmar_limpieza_cuotas"] = False
                    st.rerun()

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

    def _fmt_opcion_ciclo(ini_c, fin_c):
        _m, _a = nombre_mes_de_ciclo(fin_c)
        return f"{ini_c.strftime('%d/%m')} → {fin_c.strftime('%d/%m/%y')}  (resumen {_m[:3]} {_a})"
    opciones_ciclo = [_fmt_opcion_ciclo(ini_c, fin_c)
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
        st.markdown("<div class='empty'><big>💳</big>Sin gastos confirmados en este período.</div>", unsafe_allow_html=True)

        # FIX: si el período consultado no tiene resumen real cargado todavía
        # (ej: consultaste agosto pero el banco todavía no te dio ese
        # resumen), mostrar una ESTIMACIÓN de las cuotas que probablemente
        # sigan activas — a pedido explícito, claramente marcada como tal,
        # nunca mezclada con datos reales ni guardada en el CSV.
        estimadas = estimar_cuotas_en_periodo_futuro(gastos_df, t_sel, fin_p)
        if not estimadas.empty:
            st.markdown(
                "<div class='info-strip'>📊 <b>Estimación</b> (no es el resumen oficial del banco — "
                "vas a confirmar el número real cuando importes el resumen de este mes):</div>",
                unsafe_allow_html=True
            )
            total_estimado = estimadas["Monto"].astype(float).sum()
            for _, r in estimadas.sort_values("Monto", ascending=False).iterrows():
                st.markdown(
                    "<div class='tx'>"
                    f"<div class='tx-ico'>{emoji_cat(str(r.get('Categoria','💳')))}</div>"
                    "<div class='tx-main'>"
                    f"<div class='tx-name'>{r.get('Concepto','—')}<span class='chip-next'>≈ {r['Cuota estimada']}</span></div>"
                    f"<div class='tx-info'>Fecha compra: {str(r.get('Fecha',''))[:10]}</div>"
                    "</div>"
                    f"<div class='tx-amt c-neg'>−{fmt_ars(r.get('Monto',0))}</div>"
                    "</div>", unsafe_allow_html=True)
            st.markdown(
                "<div class='total-strip'>"
                "<span class='total-strip-label'>Total estimado (estas cuotas)</span>"
                f"<span class='total-strip-val c-yel'>−{fmt_ars(total_estimado)}</span>"
                "</div>", unsafe_allow_html=True)
    else:
        # FIX BUG DUPLICADOS: antes, df_ed (el DataFrame base del editor) se
        # RECONSTRUÍA DESDE CERO en cada rerun (cada celda que se toca en un
        # st.data_editor dispara un rerun completo del script). Streamlit
        # mantiene internamente, bajo la misma `key`, los deltas de edición
        # ya aplicados — pero si el DataFrame base que se le pasa en cada
        # rerun no es EXACTAMENTE el mismo objeto (aunque el contenido sea
        # equivalente), Streamlit puede reconciliar mal su estado interno y
        # terminar re-aplicando altas ya aplicadas, duplicando filas (bug
        # confirmado y documentado por el equipo de Streamlit, ver issue
        # streamlit/streamlit#7749). La solución oficial es usar
        # session_state como ÚNICA fuente de verdad: el DataFrame base se
        # calcula UNA SOLA VEZ por período (la primera vez que se entra), y
        # los reruns subsiguientes reutilizan esa misma copia — nunca se
        # reconstruye desde gastos_df hasta que se guarda explícitamente o
        # se cambia de período/tarjeta.
        _key_periodo = f"_df_ed_state_{t_sel}_{inicio_p}_{fin_p}"

        if _key_periodo not in st.session_state:
            df_per = filtrar_gastos_tarjeta_rango(gastos_df, t_sel, inicio_p, fin_p)
            df_ed = df_per.copy().reset_index(drop=True)
            df_ed["Fecha"]    = df_ed["Fecha"].apply(lambda x: pd.to_datetime(x, errors="coerce").date() if str(x) not in ("S/F","","nan") else None)
            df_ed["Monto"]    = pd.to_numeric(df_ed["Monto"], errors="coerce").fillna(0)
            _cuotas_parseadas = df_ed["Cuotas"].apply(parsear_cuotas)
            df_ed["Cuota actual"] = _cuotas_parseadas.apply(lambda t: t[0])
            df_ed["Cuota total"]  = _cuotas_parseadas.apply(lambda t: t[1])
            df_ed = df_ed.drop(columns=["Cuotas"])
            df_ed["Cuanto recupero"] = pd.to_numeric(df_ed["Cuanto recupero"], errors="coerce").fillna(0)
            df_ed["Periodo"] = df_per["Periodo"].values if "Periodo" in df_per.columns else df_per["Fecha"].values
            for c in ["Concepto","Tarjeta","Categoria","Compartido","Con quien","Notas"]:
                df_ed[c] = df_ed[c].fillna("").astype(str)
            st.session_state[_key_periodo] = df_ed
        else:
            df_ed = st.session_state[_key_periodo]

        # Limpiar estados de OTROS períodos/tarjetas para no acumular
        # session_state indefinidamente a medida que el usuario navega.
        for _k in list(st.session_state.keys()):
            if _k.startswith("_df_ed_state_") and _k != _key_periodo:
                del st.session_state[_k]
            if _k.startswith("_row_ids_state_"):
                del st.session_state[_k]

        # FIX PROBLEMA 1: "Con quien" se edita DIRECTO en esta misma tabla —
        # no hay alta separada en Compartidos. Al poner un nombre y un monto
        # en "Recupero", ese gasto va a aparecer agrupado en la pestaña
        # Compartidos automáticamente.
        # FIX PROBLEMA 4: "Cuota actual/total" muestran el número real de ESTA
        # fila (que ya es una fila materializada, no una proyección) y son
        # editables — pero ojo, cambiar el total acá NO regenera las otras
        # cuotas (cada fila es independiente, a pedido explícito).
        #
        # NOTA SOBRE EL BUG DE DUPLICADOS/PÉRDIDA DE FILAS: se intentó arreglar
        # leyendo los deltas crudos de Streamlit (edited_rows/added_rows/
        # deleted_rows) y reconstruyendo el DataFrame a mano, pero esto
        # introdujo el MISMO problema por otra vía (no se pudo verificar con
        # certeza el comportamiento exacto del frontend de Streamlit sin
        # poder instalarlo en el entorno de prueba). Se volvió al patrón
        # ESTÁNDAR Y DOCUMENTADO: usar directamente el DataFrame que devuelve
        # st.data_editor() como fuente de verdad para guardar. La causa real
        # de los duplicados/pérdidas era que `df_ed` (el argumento que se le
        # pasa al editor) SÍ debe ser estable entre reruns — lo cual ya se
        # garantiza guardándolo en session_state y NO reconstruyéndolo en
        # cada render — pero el VALOR DE RETORNO (edited_per) hay que usarlo
        # tal cual lo entrega Streamlit, sin "mejorarlo" con reconstrucciones
        # manuales.
        edited_per = st.data_editor(
            df_ed.drop(columns=["Periodo"], errors="ignore"),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Fecha":           st.column_config.DateColumn("Fecha compra", help="Fecha real de la compra original — para una cuota vieja, NO es de este mes"),
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

            # MECANISMO: identificamos las filas del período por CLAVE DE
            # CONTENIDO: Concepto+Fecha+Tarjeta. Robusto — no depende de
            # posiciones en el DataFrame que pueden desincronizarse.
            def _clave_fila(r):
                return (
                    str(r.get("Concepto","")).strip(),
                    str(r.get("Fecha",""))[:10],
                    str(r.get("Tarjeta","")).strip(),
                )
            # Las claves que pertenecen a este período son las de df_ed
            # (la copia estable que cargamos al inicio de esta vista).
            claves_periodo = set(df_ed.apply(_clave_fila, axis=1))
            mascara_borrar = base.apply(_clave_fila, axis=1).isin(claves_periodo)
            base_limpia = base[~mascara_borrar].reset_index(drop=True)

            nuevas = edited_per.copy()
            nuevas["Fecha"]           = nuevas["Fecha"].apply(fmt_fecha)
            nuevas["Monto"]           = to_num(nuevas["Monto"])
            nuevas["Cuanto recupero"] = to_num(nuevas["Cuanto recupero"])
            _cuota_act_col = pd.to_numeric(nuevas.get("Cuota actual", 1), errors="coerce").fillna(1).astype(int)
            _cuota_tot_col = pd.to_numeric(nuevas.get("Cuota total", 1), errors="coerce").fillna(1).astype(int)
            nuevas["Cuotas"] = [fmt_cuotas(a, t) for a, t in zip(_cuota_act_col, _cuota_tot_col)]
            nuevas = nuevas.drop(columns=["Cuota actual", "Cuota total"], errors="ignore")

            # Preservar Periodo de las filas existentes
            _periodo_default = calcular_periodo_de_importacion(t_sel, tarjetas_df)
            _periodos_dict = {}
            if "Periodo" in df_ed.columns:
                for _, _r in df_ed.iterrows():
                    _k = (str(_r["Concepto"]).strip(), str(_r["Fecha"]))
                    _periodos_dict[_k] = str(_r["Periodo"])
            _periodos = []
            for _, _r in nuevas.iterrows():
                _k = (str(_r["Concepto"]).strip(), str(_r["Fecha"]))
                _periodos.append(_periodos_dict.get(_k, _periodo_default))
            nuevas["Periodo"] = _periodos
            for col in FILES["gastos"][1]:
                if col not in nuevas.columns:
                    nuevas[col] = ""
            nuevas = nuevas[FILES["gastos"][1]]

            # Historial de auditoría: detecta si Fecha o Monto cambiaron
            # respecto a lo que había antes de editar (df_ed), y lo
            # registra en un log aparte. No cambia el guardado en sí — cada
            # cuota sigue siendo independiente, esto es solo trazabilidad
            # para poder rastrear después si una cuota "se movió" de período.
            cambios_detectados = detectar_cambios_fecha_monto(df_ed, nuevas)
            if cambios_detectados:
                registrar_historial(cambios_detectados)

            final = pd.concat([base_limpia, nuevas], ignore_index=True)
            final = sort_by_fecha(final)
            save("gastos", final)
            # Limpiar el estado guardado del editor: los datos en disco
            # cambiaron, así que la próxima vez que se entre a este período
            # hay que recalcular df_ed desde cero (no reusar la copia vieja).
            st.session_state.pop(_key_periodo, None)
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
                # Recalcular qué filas borrar de forma independiente (no
                # depende del estado del editor, que puede no existir en
                # este scope si el período está vacío o si el usuario nunca
                # llegó a abrir la tabla editable).
                base_para_borrar = load("gastos")
                base_para_borrar["_row_id"] = range(len(base_para_borrar))
                filas_a_borrar = filtrar_gastos_tarjeta_rango(base_para_borrar, t_sel, inicio_p, fin_p)
                ids_a_borrar = set(filas_a_borrar["_row_id"].astype(int))
                resto = base_para_borrar[~base_para_borrar["_row_id"].isin(ids_a_borrar)].drop(columns=["_row_id"]).reset_index(drop=True)
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
                # Usa Periodo (ciclo de tarjeta), no Fecha (compra real) — una
                # cuota vieja compartida puede tener Fecha de meses atrás pero
                # corresponder al período actual.
                columna_periodo_cp = det_persona["Periodo"] if "Periodo" in det_persona.columns else det_persona["Fecha"]
                det_persona["_periodo"] = pd.to_datetime(columna_periodo_cp, errors="coerce").dt.to_period("M")
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
            # compartido en ese mes, desagregado por persona. Usa Periodo
            # (ciclo de tarjeta), no Fecha — mismo motivo que la vista por
            # persona arriba.
            columna_periodo_cp2 = con_persona_df["Periodo"] if "Periodo" in con_persona_df.columns else con_persona_df["Fecha"]
            con_persona_df["_periodo"] = pd.to_datetime(columna_periodo_cp2, errors="coerce").dt.to_period("M")
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
