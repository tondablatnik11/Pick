import streamlit as st
import pandas as pd
import io
import plotly.express as px  # Pro hezčí grafy (nutné přidat do requirements.txt)

# --- KONFIGURACE STRÁNKY ---
st.set_page_config(page_title="Warehouse Picking Dashboard", layout="wide", page_icon="📦")

# --- STYLOVÁNÍ ---
st.markdown("""
    <style>
    .big-font { font-size:20px !important; font-weight: bold; }
    .metric-card { background-color: #f0f2f6; padding: 15px; border-radius: 10px; border-left: 5px solid #ff4b4b; }
    </style>
    """, unsafe_allow_html=True)

st.title("📦 Warehouse Picking Analytics")
st.markdown("Profesionální přehled efektivity a prostojů v pickování.")

# --- FUNKCE PRO ZPRACOVÁNÍ DAT ---
@st.cache_data
def load_and_process_data(uploaded_file):
    # Detekce typu
    if uploaded_file.name.endswith('.csv'):
        try:
            df = pd.read_csv(uploaded_file)
        except:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=';')
    else:
        df = pd.read_excel(uploaded_file)

    # Vytvoření Timestamp
    # Předpokládáme sloupce s koncovkou .1 pro položky
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])

    # Seřazení
    df = df.sort_values(by=['Transfer Order Number', 'PickTimestamp'])

    # Výpočty
    df['TimeDiff'] = df.groupby('Transfer Order Number')['PickTimestamp'].diff()
    df['Prodleva_min'] = df['TimeDiff'].dt.total_seconds() / 60
    
    # Hodina dne (pro analýzu kdy dochází k prostojům)
    df['Hodina'] = df['PickTimestamp'].dt.hour
    
    # Kontrola změny uživatele
    df['User_Prev'] = df.groupby('Transfer Order Number')['User'].shift(1)
    df['Is_Same_User'] = df['User'] == df['User_Prev']

    return df

# --- HLAVNÍ LOGIKA ---
uploaded_file = st.sidebar.file_uploader("📂 Nahrát export dat", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Načítám a analyzuji data...'):
        df = load_and_process_data(uploaded_file)

    # --- SIDEBAR FILTRY ---
    st.sidebar.header("🔍 Filtry")
    
    # Filtr na minimální prodlevu
    min_delay = st.sidebar.slider("Minimální prodleva (minuty)", 5, 120, 15)
    
    # Filtr na uživatele
    all_users = sorted(df['User'].unique().astype(str))
    selected_users = st.sidebar.multiselect("Vybrat skladníky", all_users, default=all_users)
    
    # Filtrace dat
    # Bereme jen řádky, kde je prodleva > limit A je to stejný uživatel (aby to nebyla prodleva při předání směny)
    # Volitelně můžeme zahrnout i změnu uživatele, ale pro čistotu dat dáváme defaultně Same User
    only_same_user = st.sidebar.checkbox("Ignorovat změnu uživatele (předání zakázky)", value=True)
    
    mask = (df['Prodleva_min'] > min_delay) & (df['User'].isin(selected_users))
    if only_same_user:
        mask = mask & (df['Is_Same_User'] == True)
        
    df_delays = df[mask].copy()

    # --- KPI SEKCE ---
    st.markdown("### 📊 Hlavní přehled")
    col1, col2, col3, col4 = st.columns(4)
    
    total_delay_hours = df_delays['Prodleva_min'].sum() / 60
    count_delays = len(df_delays)
    worst_offender = df_delays['User'].mode()[0] if not df_delays.empty else "N/A"
    avg_delay = df_delays['Prodleva_min'].mean() if not df_delays.empty else 0

    col1.metric("Celkový ztracený čas", f"{total_delay_hours:.1f} hod", delta_color="inverse")
    col2.metric("Počet incidentů", count_delays)
    col3.metric("Nejčastější 'čekač'", worst_offender)
    col4.metric("Průměrná prodleva", f"{avg_delay:.1f} min")

    st.divider()

    # --- GRAFY ---
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader("🏆 Top uživatelé podle součtu prodlev")
        if not df_delays.empty:
            user_stats = df_delays.groupby('User')['Prodleva_min'].sum().reset_index()
            fig1 = px.bar(user_stats, x='User', y='Prodleva_min', 
                          title="Suma prostojů (minuty)", 
                          color='Prodleva_min', color_continuous_scale='Reds')
            st.plotly_chart(fig1, use_container_width=True)
        else:
            st.info("Žádná data pro zobrazení.")

    with col_chart2:
        st.subheader("⏰ Kdy dochází k prostojům?")
        if not df_delays.empty:
            fig2 = px.histogram(df_delays, x='Hodina', nbins=24, 
                                title="Rozložení prodlev během dne (Hodina)",
                                color_discrete_sequence=['#ff4b4b'])
            fig2.update_layout(xaxis_title="Hodina", yaxis_title="Počet incidentů")
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Žádná data pro zobrazení.")

    # --- DETAILNÍ DATA ---
    st.subheader("📋 Detailní seznam incidentů")
    
    # Výběr sloupců pro tabulku
    cols_show = ['Transfer Order Number', 'User', 'PickTimestamp', 'Prodleva_min', 'Material', 'Material Description']
    final_cols = [c for c in cols_show if c in df_delays.columns]
    
    st.dataframe(
        df_delays[final_cols].sort_values(by='Prodleva_min', ascending=False),
        use_container_width=True
    )

    # --- DOWNLOAD SEKCE ---
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df_delays[final_cols].to_excel(writer, index=False, sheet_name='Prostoje')
        
    st.download_button(
        label="📥 Stáhnout report (Excel)",
        data=buffer.getvalue(),
        file_name="warehouse_report_pro.xlsx",
        mime="application/vnd.ms-excel"
    )

else:
    st.info("👈 Nahrajte soubor v levém menu pro zahájení analýzy.")
