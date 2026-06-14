import streamlit as st
import pandas as pd
import os

# Nombre del archivo donde se va a guardar tu base de datos
DATA_FILE = "mis_gastos.csv"

def load_data():
    """Carga los datos del CSV o crea un DataFrame vacío con las columnas si no existe."""
    if os.path.exists(DATA_FILE):
        df = pd.read_csv(DATA_FILE)
    else:
        df = pd.DataFrame(columns=[
            "Tarjeta", "Fecha", "Concepto", "Monto", 
            "Categoria", "Compartido", "Con quien", "Cuanto recupero"
        ])
    return df

def save_data(df):
    """Guarda el DataFrame en el archivo CSV."""
    df.to_csv(DATA_FILE, index=False)

# Configuración de la página
st.set_page_config(page_title="Gestor de Gastos", page_icon="💳", layout="wide")
st.title("📊 Gestor de Gastos Casero")
st.markdown("Bienvenido, **Juan Bautista**. Acá podés editar y analizar toda tu data.")

# Cargar la data
df = load_data()

# Limpiar la columna Monto para poder sumar (por si le metés el símbolo $)
# Usamos una columna temporal para cálculos matemáticos
df['Monto_Num'] = pd.to_numeric(df['Monto'].replace('[\$,]', '', regex=True), errors='coerce').fillna(0)

# --- 1. SECCIÓN DE RESUMEN Y MÉTRICAS ---
st.subheader("📈 Resumen Rápido")
total_general = df['Monto_Num'].sum()
# Calculamos el total de los gastos que le pusiste la categoría específica
gastos_viaje = df[df['Categoria'].str.contains('Viaje con lauti', case=False, na=False)]['Monto_Num'].sum()

col1, col2 = st.columns(2)
col1.metric("Total Acumulado", f"$ {total_general:,.2f}")
col2.metric("Total Viaje Mendoza", f"$ {gastos_viaje:,.2f}")
st.divider()

# --- 2. SECCIÓN DE EDICIÓN (LA MAGIA) ---
st.subheader("📝 Tabla Interactiva (Editá, agregá o borrá filas)")
st.info("Podés hacer doble clic en cualquier celda para editarla. Al final de la tabla podés agregar filas nuevas.")

# st.data_editor permite modificar el dataframe en tiempo real desde la web
edited_df = st.data_editor(
    df.drop(columns=['Monto_Num']), # Ocultamos la columna temporal de los cálculos
    num_rows="dynamic", # Permite agregar o eliminar filas
    use_container_width=True,
    height=400
)

# Botón para guardar los cambios en el CSV local
if st.button("💾 Guardar Cambios"):
    save_data(edited_df)
    st.success("¡Datos guardados joya! Actualizá la página para ver los nuevos totales.")
    st.rerun()

st.divider()

# --- 3. SECCIÓN DE FILTROS ---
st.subheader("🔍 Analizar por Tarjeta")
lista_tarjetas = ["Todas"] + list(edited_df['Tarjeta'].dropna().unique())
tarjeta_sel = st.selectbox("Elegí una tarjeta para filtrar:", lista_tarjetas)

if tarjeta_sel != "Todas":
    df_filtrado = edited_df[edited_df['Tarjeta'] == tarjeta_sel]
    st.dataframe(df_filtrado, use_container_width=True)
    
    # Calcular subtotales de la tarjeta filtrada
    df_filtrado['Monto_Num'] = pd.to_numeric(df_filtrado['Monto'].replace('[\$,]', '', regex=True), errors='coerce').fillna(0)
    st.caption(f"**Total gastado en {tarjeta_sel}:** $ {df_filtrado['Monto_Num'].sum():,.2f}")
