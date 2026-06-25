import streamlit as st
import pandas as pd
import os
from datetime import date, datetime
import calendar

st.set_page_config(page_title="Gestor Financiero", layout="wide")

# ==========================================
# 1. CSS ORIGINAL RECONSTRUIDO
# ==========================================
st.markdown("""
<style>
.tarjeta-row { display: flex; align-items: center; padding: 0.5rem 0; border-bottom: 1px solid rgba(150,150,150,0.2); }
.tarjeta-pip { width: 14px; height: 14px; border-radius: 50%; margin-right: 12px; flex-shrink: 0; box-shadow: 0px 0px 4px rgba(0,0,0,0.3);}
.tarjeta-label { font-size: 1.05rem; font-weight: 600; flex: 1; color: var(--text-color); }
.tarjeta-amount { font-size: 1.1rem; font-weight: 700; text-align: right; }
.gasto-detalle { padding:0.3rem 0 0.3rem 1.5rem; font-size:0.85rem; color:#888; display: flex; justify-content: space-between; border-bottom: 1px dotted rgba(150,150,150,0.3); }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. CONFIGURACIÓN Y ARCHIVOS BASE
# ==========================================
FILES = {
    "gastos": ("mis_gastos.csv", ["Fecha", "Concepto", "Monto", "Tarjeta", "Cuotas", "Categoria", "Compartido", "Con quien", "Cuanto recupero", "Notas"]),
    "ingresos": ("mis_ingresos.csv", ["Fecha", "Concepto", "Monto", "Categoria"]),
    "tarjetas": ("mis_tarjetas.csv", ["Nombre", "Dia cierre", "Dia vencimiento", "Color", "Cierre anterior", "Proximo cierre", "Dias entre cierres"])
}

CAT_GASTOS = ["Comida", "Educación", "Transporte", "Salidas", "Viaje", "Salud", "Servicios", "Casa", "Farmacia", "Ropa", "Regalos", "Otro"]
CAT_ING = ["Sueldo", "Freelance", "Inversión", "Regalo", "Otro"]

def load(key):
    f, cols = FILES[key]
    if os.path.exists(f):
        try:
            df = pd.read_csv(f, dtype=str).fillna("")
        except:
            df = pd.DataFrame(columns=cols)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        if "Fecha" in df.columns:
            df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.strftime("%Y-%m-%d")
        return df[cols]
    return pd.DataFrame(columns=cols)

def save(key, df):
    f, _ = FILES[key]
    df.to_csv(f, index=False)

def to_num(series):
    return pd.to_numeric(pd.Series(series).astype(str).str.replace(r"[^\d\.\-]", "", regex=True), errors="coerce").fillna(0)

# ==========================================
# 3. LÓGICA CORE (RECONSTRUIDA DEL PDF)
# ==========================================
def fmt_ars(val):
    return f"$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def get_color_tarjeta(tname, df_tarjetas):
    try:
        color = df_tarjetas.loc[df_tarjetas['Nombre'] == tname, 'Color'].values[0]
        return color if color else "#888888"
    except:
        return "#888888"

def obtener_periodo_resumen(fecha_str, tname, t_dict):
    """Asigna un gasto a su mes de RESUMEN exacto (Cruza el día de compra con el día de cierre)."""
    try:
        dt = pd.to_datetime(fecha_str).date()
    except:
        dt = date.today()
        
    tname_str = str(tname).strip()
    if not tname_str or tname_str in ["Efectivo", "Débito"]:
        return dt.year, dt.month
        
    if tname_str in t_dict:
        row = t_dict[tname_str]
        
        # Modo simple: Dia de cierre
        try:
            dia_cierre = int(float(str(row.get("Dia cierre", 0))))
        except:
            dia_cierre = 0
            
        if dia_cierre > 0:
            cierre_real = min(dia_cierre, calendar.monthrange(dt.year, dt.month)[1])
            # Si compro DESPUÉS del cierre, entra en el resumen del mes que viene
            if dt.day > cierre_real:
                if dt.month == 12:
                    return dt.year + 1, 1
                else:
                    return dt.year, dt.month + 1
                    
    return dt.year, dt.month

def procesar_gastos_con_periodos(df_gastos, df_tarjetas):
    """Enriquece el DF con los periodos para cruzar Home y Tarjetas de manera idéntica."""
    if df_gastos.empty:
        df_gastos["Periodo_Año"] = []
        df_gastos["Periodo_Mes"] = []
        df_gastos["Periodo_Str"] = []
        return df_gastos
        
    t_dict = df_tarjetas.set_index("Nombre").to_dict("index") if not df_tarjetas.empty else {}
    
    py_list, pm_list, pstr_list = [], [], []
    for _, row in df_gastos.iterrows():
        py, pm = obtener_periodo_resumen(row.get("Fecha", ""), row.get("Tarjeta", ""), t_dict)
        py_list.append(py)
        pm_list.append(pm)
        pstr_list.append(f"{pm:02d}/{py}")
        
    df_gastos["Periodo_Año"] = py_list
    df_gastos["Periodo_Mes"] = pm_list
    df_gastos["Periodo_Str"] = pstr_list
    return df_gastos

def filtrar_gastos_tarjeta_periodo(df_completo, tname, py, pm):
    return df_completo[(df_completo["Tarjeta"] == tname) & 
                       (df_completo["Periodo_Año"] == py) & 
                       (df_completo["Periodo_Mes"] == pm)]

# ==========================================
# 4. EXPANSIÓN FÍSICA DE CUOTAS (NUEVO FIX)
# ==========================================
def registrar_gasto_expandido(fecha, concepto, monto_total, tarjeta, cuotas, categoria, con_quien, notas):
    df = load("gastos")
    nuevas = []
    
    monto_float = float(monto_total)
    ctas_int = max(int(cuotas), 1)
    monto_cuota = monto_float / ctas_int
    
    fecha_base = pd.to_datetime(fecha).date()
    
    for i in range(1, ctas_int + 1):
        # Adelantamos calendario físico
        mes_avance = fecha_base.month - 1 + (i - 1)
        año_i = fecha_base.year + (mes_avance // 12)
        mes_i = (mes_avance % 12) + 1
        dia_i = min(fecha_base.day, calendar.monthrange(año_i, mes_i)[1])
        
        cuotas_str = f"{i}/{ctas_int}" if ctas_int > 1 else "1"
        compartido_flag = "Sí" if str(con_quien).strip() else "No"
        
        nuevas.append({
            "Fecha": date(año_i, mes_i, dia_i).strftime("%Y-%m-%d"),
            "Concepto": concepto,
            "Monto": f"{monto_cuota:.2f}",
            "Tarjeta": tarjeta,
            "Cuotas": cuotas_str,
            "Categoria": categoria,
            "Compartido": compartido_flag,
            "Con quien": con_quien,
            "Cuanto recupero": "0",
            "Notas": notas
        })
        
    df_nuevas = pd.DataFrame(nuevas)
    df = pd.concat([df, df_nuevas], ignore_index=True)
    save("gastos", df)

# ==========================================
# 5. CARGA DE DATOS MAESTRA
# ==========================================
tarjetas_df = load("tarjetas")
gastos_df = load("gastos")
gastos_df["Monto_Num"] = to_num(gastos_df["Monto"])
gastos_df = procesar_gastos_con_periodos(gastos_df, tarjetas_df)

ingresos_df = load("ingresos")
ingresos_df["Monto_Num"] = to_num(ingresos_df["Monto"])

lista_tarjetas = ["Efectivo", "Débito"] + list(tarjetas_df["Nombre"].unique())

# ==========================================
# 6. INTERFAZ: 5 PESTAÑAS
# ==========================================
st.title("Gestor Financiero")
tabs = st.tabs(["🏠 HOME", "💳 GASTOS", "🏦 TARJETAS", "💵 INGRESOS", "🤝 COMPARTIDOS"])

# --- TAB 1: HOME ---
with tabs[0]:
    st.markdown("<h3 style='text-align: center;'>Resumen General del Período</h3>", unsafe_allow_html=True)
    
    if gastos_df.empty:
        st.info("No hay gastos registrados.")
    else:
        periodos_disp = sorted(gastos_df["Periodo_Str"].unique(), key=lambda x: (int(x.split('/')[1]), int(x.split('/')[0])), reverse=True)
        col1, col2, col3 = st.columns([1, 2, 1])
        sel_per = col2.selectbox("Seleccionar Resumen (Mes/Año de cobro)", periodos_disp)
        
        pm_sel, py_sel = map(int, sel_per.split("/"))
        df_mes = gastos_df[(gastos_df["Periodo_Año"] == py_sel) & (gastos_df["Periodo_Mes"] == pm_sel)]
        
        total_mes = df_mes["Monto_Num"].sum()
        col2.metric("Total Gastos del Resumen", fmt_ars(total_mes))
        
        st.markdown("---")
        detalle_tarjeta = df_mes.groupby("Tarjeta")["Monto_Num"].sum().to_dict()
        
        # UI RECONSTRUIDA DE LA PÁGINA 48 DEL PDF
        col_list, col_gap = st.columns([2, 1])
        with col_list:
            for tname, t_total in sorted(detalle_tarjeta.items(), key=lambda x: x[0]):
                color = get_color_tarjeta(tname, tarjetas_df)
                
                # Header Tarjeta
                st.markdown(
                    f"<div class='tarjeta-row'>"
                    f"<div style='display:flex; align-items:center; flex:1;'><div class='tarjeta-pip' style='background: {color}'></div>"
                    f"<div class='tarjeta-label'>{tname}</div></div>"
                    f"<div class='tarjeta-amount' style='color: {color}'>{fmt_ars(t_total)}</div>"
                    f"</div>", unsafe_allow_html=True
                )
                
                # Detalle Cuotas
                gf_detalle = filtrar_gastos_tarjeta_periodo(df_mes, tname, py_sel, pm_sel)
                if not gf_detalle.empty:
                    for _, r in gf_detalle.sort_values("Monto_Num", ascending=False).iterrows():
                        cuota_txt = f" (Cuota {r['Cuotas']})" if str(r.get("Cuotas", "1")) not in ["1", "", "nan"] else ""
                        comp_txt = f" 🧑‍🤝‍🧑 {r['Con quien']}" if str(r.get("Con quien", "")).strip() else ""
                        
                        st.markdown(
                            f"<div class='gasto-detalle'>"
                            f"<span>{r['Concepto']}{cuota_txt}{comp_txt}</span>"
                            f"<span>{fmt_ars(r['Monto_Num'])}</span>"
                            f"</div>", unsafe_allow_html=True
                        )
                st.write("") # Espacio

# --- TAB 2: GASTOS ---
with tabs[1]:
    st.header("Cargar Nuevo Gasto")
    with st.form("form_gasto"):
        col1, col2 = st.columns(2)
        f_fecha = col1.date_input("Fecha de Compra", date.today())
        f_concepto = col2.text_input("Concepto")
        
        st.info("Si es en cuotas, poné el monto TOTAL de la compra. El sistema lo divide solo.")
        f_monto = col1.number_input("Monto TOTAL ($)", min_value=0.0, format="%.2f")
        f_tarjeta = col2.selectbox("Tarjeta / Medio", lista_tarjetas)
        
        f_cuotas = col1.number_input("Cantidad de Cuotas", min_value=1, value=1, step=1)
        f_cat = col2.selectbox("Categoría", CAT_GASTOS)
        
        f_con_quien = col1.text_input("¿Gasto compartido? ¿Con quién? (Dejar vacío si es 100% tuyo)")
        f_notas = col2.text_input("Notas adicionales")
        
        if st.form_submit_button("Guardar Gasto"):
            if f_concepto and f_monto > 0:
                registrar_gasto_expandido(f_fecha, f_concepto, f_monto, f_tarjeta, f_cuotas, f_cat, f_con_quien, f_notas)
                st.success("¡Gasto guardado correctamente!")
                st.rerun()
            else:
                st.error("Completá el Concepto y el Monto.")
                
    st.markdown("---")
    st.subheader("Historial Crudo de Gastos")
    edited_gastos = st.data_editor(load("gastos"), num_rows="dynamic", use_container_width=True)
    if st.button("Guardar Ediciones Manuales de Gastos"):
        save("gastos", edited_gastos)
        st.success("Base actualizada.")
        st.rerun()

# --- TAB 3: TARJETAS ---
with tabs[2]:
    st.header("Gestión de Tarjetas")
    
    st.subheader("Mis Tarjetas (Configuración)")
    edited_tarjetas = st.data_editor(tarjetas_df, num_rows="dynamic", use_container_width=True)
    if st.button("Guardar Configuración de Tarjetas"):
        save("tarjetas", edited_tarjetas)
        st.success("¡Tarjetas actualizadas!")
        st.rerun()
        
    st.markdown("---")
    # BOTÓN PARA BLANQUEAR TARJETA (PUNTO 3 DEL PEDIDO)
    st.subheader("🚨 Opciones Avanzadas")
    with st.expander("Blanquear / Borrar TODOS los gastos de una tarjeta"):
        st.warning("¡Cuidado! Esta acción borra todo el historial de la tarjeta elegida y no se puede deshacer.")
        col_b1, col_b2 = st.columns([2, 1])
        t_borrar = col_b1.selectbox("Seleccionar Tarjeta a borrar:", lista_tarjetas, key="t_borrar")
        if col_b2.button(f"🗑️ ELIMINAR GASTOS DE {t_borrar}"):
            g_actual = load("gastos")
            g_nuevo = g_actual[g_actual["Tarjeta"] != t_borrar]
            save("gastos", g_nuevo)
            st.success(f"Se eliminó el historial de {t_borrar}.")
            st.rerun()

# --- TAB 4: INGRESOS ---
with tabs[3]:
    st.header("Gestión de Ingresos")
    with st.form("form_ingresos"):
        col1, col2 = st.columns(2)
        i_fecha = col1.date_input("Fecha", date.today())
        i_concepto = col2.text_input("Concepto")
        i_monto = col1.number_input("Monto ($)", min_value=0.0, format="%.2f")
        i_cat = col2.selectbox("Categoría", CAT_ING)
        
        if st.form_submit_button("Registrar Ingreso"):
            if i_concepto and i_monto > 0:
                i_df = load("ingresos")
                nuevo_i = pd.DataFrame([{
                    "Fecha": i_fecha.strftime("%Y-%m-%d"),
                    "Concepto": i_concepto,
                    "Monto": f"{i_monto:.2f}",
                    "Categoria": i_cat
                }])
                save("ingresos", pd.concat([i_df, nuevo_i], ignore_index=True))
                st.success("¡Ingreso adentro!")
                st.rerun()
            else:
                st.error("Completá el Concepto y el Monto.")
                
    st.subheader("Historial de Ingresos")
    edited_ing = st.data_editor(load("ingresos"), num_rows="dynamic", use_container_width=True)
    if st.button("Guardar Ediciones de Ingresos"):
        save("ingresos", edited_ing)
        st.success("Ingresos actualizados.")
        st.rerun()

# --- TAB 5: COMPARTIDOS ---
with tabs[4]:
    st.header("🤝 Gastos Compartidos (Cruce Automático)")
    st.caption("Esto cruza directo con tus tarjetas. Lo que ves acá es exactamente lo que cargaste con la columna 'Con quien' llena.")
    
    comp_df = gastos_df[gastos_df["Con quien"].str.strip() != ""]
    
    if comp_df.empty:
        st.info("No tenés gastos compartidos actualmente.")
    else:
        modo = st.radio("Filtro de Análisis:", ["Ver por Periodo de Resumen", "Ver por Persona Específica"], horizontal=True)
        
        if modo == "Ver por Periodo de Resumen":
            periodos_c = sorted(comp_df["Periodo_Str"].unique(), key=lambda x: (int(x.split('/')[1]), int(x.split('/')[0])), reverse=True)
            sel_per_c = st.selectbox("Seleccionar Resumen", periodos_c)
            df_p = comp_df[comp_df["Periodo_Str"] == sel_per_c]
            
            st.metric(f"Total compartido en {sel_per_c}", fmt_ars(df_p["Monto_Num"].sum()))
            
            st.markdown("### ¿Quiénes te deben en este resumen?")
            agrup_pers = df_p.groupby("Con quien")["Monto_Num"].sum().reset_index()
            for _, rp in agrup_pers.iterrows():
                st.markdown(f"**{rp['Con quien']}**: {fmt_ars(rp['Monto_Num'])}")
                
            st.markdown("### Desglose:")
            st.dataframe(df_p[["Fecha", "Concepto", "Monto", "Tarjeta", "Con quien", "Cuotas"]], use_container_width=True)
            
        else:
            personas = sorted(comp_df["Con quien"].unique())
            sel_pers = st.selectbox("Seleccionar Persona", personas)
            df_p = comp_df[comp_df["Con quien"] == sel_pers]
            
            st.metric(f"Total histórico compartido con {sel_pers}", fmt_ars(df_p["Monto_Num"].sum()))
            
            st.markdown("### ¿Cuánto fue por cada Resumen?")
            agrup_res = df_p.groupby("Periodo_Str")["Monto_Num"].sum().reset_index()
            # Ordenar por periodo
            agrup_res['sort_val'] = agrup_res['Periodo_Str'].apply(lambda x: f"{x.split('/')[1]}{x.split('/')[0]}")
            agrup_res = agrup_res.sort_values('sort_val', ascending=False)
            
            for _, rr in agrup_res.iterrows():
                st.markdown(f"**Resumen {rr['Periodo_Str']}**: {fmt_ars(rr['Monto_Num'])}")
                
            st.markdown("### Desglose:")
            st.dataframe(df_p[["Fecha", "Concepto", "Monto", "Tarjeta", "Periodo_Str", "Cuotas"]], use_container_width=True)

```
