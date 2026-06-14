import streamlit as st
import pandas as pd
import os

DATA_FILE = "mis_gastos.csv"
INGRESOS_FILE = "mis_ingresos.csv"

def load_df(file, cols):
    if os.path.exists(file): return pd.read_csv(file)
    return pd.DataFrame(columns=cols)

st.set_page_config(page_title="Finanzas JuanB", layout="centered")
st.title("💳 Mi Gestor Diario")

# Carga de datos
gastos_df = load_df(DATA_FILE, ["Tarjeta", "Fecha", "Concepto", "Monto", "Cuotas", "Categoria", "Compartido", "Con quien", "Cuanto recupero"])
ingresos_df = load_df(INGRESOS_FILE, ["Fecha", "Concepto", "Monto"])

# --- 1. BOTÓN DE GASTO RÁPIDO ---
with st.expander("➕ Agregar Gasto Nuevo (Anotador Rápido)"):
    with st.form("nuevo_gasto", clear_on_submit=True):
        col1, col2 = st.columns(2)
        tarjeta = col1.selectbox("Tarjeta", ["Visa ICBC", "Visa Hipotecario", "Master ICBC"])
        fecha = col2.date_input("Fecha")
        concepto = st.text_input("Concepto")
        monto = st.number_input("Monto", min_value=0.0)
        submitted = st.form_submit_button("Guardar Gasto")
        if submitted:
            nuevo = pd.DataFrame([[tarjeta, fecha, concepto, monto, "", "", False, "", 0]], columns=gastos_df.columns)
            gastos_df = pd.concat([gastos_df, nuevo], ignore_index=True)
            gastos_df.to_csv(DATA_FILE, index=False)
            st.success("¡Anotado!")

# --- 2. TABLA INTERACTIVA (UX MEJORADA) ---
st.subheader("📝 Detalle de Gastos")
edited_gastos = st.data_editor(
    gastos_gastos_df,
    column_config={
        "Compartido": st.column_config.CheckboxColumn("Compartido?"),
        "Monto": st.column_config.NumberColumn("Monto ($)", format="$%d"),
    },
    num_rows="dynamic"
)

if st.button("💾 Guardar Cambios en Tabla"):
    edited_gastos.to_csv(DATA_FILE, index=False)
    st.rerun()

# --- 3. INGRESOS Y BALANCE ---
st.divider()
st.subheader("💰 Balance General")
col_ing, col_bal = st.columns(2)
with col_ing:
    total_ing = ingresos_df['Monto'].sum()
    st.metric("Total Ingresos", f"${total_ing:,.0f}")
with col_bal:
    total_gast = gastos_df['Monto'].sum()
    st.metric("Balance Neto", f"${total_ing - total_gast:,.0f}")

with st.expander("Editar Ingresos"):
    edited_ingresos = st.data_editor(ingresos_df, num_rows="dynamic")
    if st.button("Guardar Ingresos"):
        edited_ingresos.to_csv(INGRESOS_FILE, index=False)
        st.rerun()
