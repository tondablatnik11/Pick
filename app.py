import streamlit as st
import pandas as pd
import io
import plotly.express as px
from datetime import datetime, time, timedelta

# --- KONFIGURACE STRÁNKY ---
st.set_page_config(page_title="Warehouse Performance Pro", layout="wide", page_icon="🏭")

# --- DEFINICE PAUZ ---
# Formát: (Hodina_od, Minuta_od, Hodina_do, Minuta_do)
BREAKS = [
    (8, 15, 8, 30),
    (11, 0, 11, 30),
    (12, 45, 13, 0),
    (16, 15, 16, 30),
    (18, 30, 19, 0),
    (20, 30, 20, 45)
]

def is_time_in_break(dt_check):
    """Pomocná funkce: Zjistí, zda je daný čas uvnitř pauzy."""
    t = dt_check.time()
    for h_start, m_start, h_end, m_end in BREAKS:
        start = time(h_start, m_start)
        end = time(h_end, m_end)
        if start <= t <= end:
            return True
    return False

def calculate_net_delay(start_dt, end_dt):
    """
    Vypočítá dobu trvání mezi dvěma časy a odečte oficiální pauzy.
    Vrací: (celková_doba_sec, čistá_doba_sec, strávený_čas_na_pauze_sec)
    """
    if pd.isna(start_dt) or pd.isna(end_dt):
        return 0, 0, 0
    
    total_duration = (end_dt - start_dt).total_seconds()
    
    if total_duration < 0: 
        return 0, 0, 0 # Chyba v datech (konec před začátkem)

    # Pokud je prodleva velmi dlouhá (např. přes noc), pauzy neřešíme tak detailně,
    # ale pro směnu (do 12h) to projdeme minutu po minutě pro přesnost, 
    # nebo rychleji pomocí intervalů. Zde robustní varianta intervalů:
    
    break_seconds = 0
    
    # Procházíme definované pauzy
    # Vytvoříme plné datetime objekty pro pauzy v den "start_dt" a "end_dt"
    # (zjednodušení: předpokládáme, že pick netrvá přes půlnoc do dalšího dne s pauzami)
    
    current_day = start_dt.date()
    
    for h_start, m_start, h_end, m_end in BREAKS:
        b_start = datetime.combine(current_day, time(h_start, m_start))
        b_end = datetime.combine(current_day, time(h_end, m_end))
        
        # Průnik intervalů [start_dt, end_dt] a [b_start, b_end]
        overlap_start = max(start_dt, b_start)
        overlap_end = min(end_dt, b_end)
        
        if overlap_start < overlap_end:
            break_seconds += (overlap_end - overlap_start).total_seconds()
            
    net_duration = max(0, total_duration - break_seconds)
    
    return total_duration, net_duration, break_seconds

# --- NAČTENÍ A ZPRACOVÁNÍ ---
@st.cache_data
def process_data(uploaded_file):
    # 1. Načtení
    if uploaded_file.name.endswith('.csv'):
        try:
            df = pd.read_csv(uploaded_file)
        except:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=';')
    else:
        df = pd.read_excel(uploaded_file)

    # 2. Timestamp
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])
    
    # 3. Sort pro výpočet pick-to-pick
    # Řadíme podle uživatele a času, abychom viděli jeho workflow
    df = df.sort_values(by=['User', 'PickTimestamp'])
    
    # 4. Výpočty prodlev (User based)
    df['PrevTimestamp'] = df.groupby('User')['PickTimestamp'].shift(1)
    
    # Aplikace logiky odečtu pauz (chvíli to trvá, proto progress bar)
    # Vektorizace je složitá kvůli časům, použijeme apply
    def calc_row_delay(row):
        return calculate_net_delay(row['PrevTimestamp'], row['PickTimestamp'])

    # Výsledek je tuple, rozdělíme do sloupců
    delay_stats = df.apply(calc_row_delay, axis=1, result_type='expand')
    df['Gross_Duration_Sec'] = delay_stats[0]
    df['Net_Duration_Sec'] = delay_stats[1]
    df['Break_Duration_Sec'] = delay_stats[2]
    
    df['Prodleva_min_Net'] = df['Net_Duration_Sec'] / 60
    df['Prodleva_min_Gross'] = df['Gross_Duration_Sec'] / 60
    
    # Detekce změny zakázky (pro kontext)
    df['PrevOrder'] = df.groupby('User')['Transfer Order Number'].shift(1)
    df['New_Task'] = df['Transfer Order Number'] != df['PrevOrder']

    # 5. Delivery Analytics (Doba trvání Dodávky)
    # Pokud sloupec Delivery neexistuje, použijeme Transfer Order
    group_col = 'Delivery' if 'Delivery' in df.columns else 'Transfer Order Number'
    
    delivery_stats = df.groupby(group_col).agg(
        Del_Start=('PickTimestamp', 'min'),
        Del_End=('PickTimestamp', 'max'),
        Del_Items=('Material', 'count'),
        Del_User=('User', 'first') # Předpoklad: dodávku dělá jeden člověk (nebo bere prvního)
    ).reset_index()
    
    delivery_stats['Delivery_Duration'] = delivery_stats['Del_End'] - delivery_stats['Del_Start']
    delivery_stats['Delivery_Duration_Min'] = delivery_stats['Delivery_Duration'].dt.total_seconds() / 60
    
    # Merge zpět do hlavního DF
    df = df.merge(delivery_stats[[group_col, 'Delivery_Duration_Min', 'Del_Items']], on=group_col, how='left')

    return df, delivery_stats

# --- UI LOGIKA ---
st.title("🏭 Profesionální Analýza Pickování & Dodávek")
st.markdown("""
Tato aplikace analyzuje efektivitu skladu. 
**Automaticky odečítá pauzy:** 8:15, 11:00, 12:45, 16:15, 18:30, 20:30.
""")

uploaded_file = st.sidebar.file_uploader("📂 Nahrát data (XLSX/CSV)", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Počítám čisté časy, odečítám pauzy...'):
        df, df_delivery = process_data(uploaded_file)

    # --- FILTRY ---
    st.sidebar.header("🔍 Nastavení reportu")
    min_delay = st.sidebar.slider("Zobrazit prodlevy delší než (minuty):", 5, 120, 15)
    users = st.sidebar.multiselect("Filtrovat skladníky:", df['User'].unique(), default=df['User'].unique())
    
    # Filtrace
    mask = (df['Prodleva_min_Net'] > min_delay) & (df['User'].isin(users))
    # Ignorujeme první pick dne (kde je prev time NaT)
    mask = mask & (df['PrevTimestamp'].notna())
    # Ignorujeme extrémy (např. přes víkend - limit 8 hodin)
    mask = mask & (df['Prodleva_min_Net'] < 480) 
    
    df_filtered = df[mask].copy()

    # --- 1. KPI PŘEHLED ---
    st.subheader("📊 Manažerský přehled")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkový čistý prostoj", f"{df_filtered['Prodleva_min_Net'].sum()/60:.1f} hod")
    c2.metric("Počet incidentů", len(df_filtered))
    c3.metric("Průměrná čistá prodleva", f"{df_filtered['Prodleva_min_Net'].mean():.1f} min")
    
    # Nejhorší dodávka
    slowest_del = df_delivery.sort_values('Delivery_Duration_Min', ascending=False).iloc[0]
    c4.metric(f"Nejpomalejší Dodávka", f"{slowest_del['Delivery_Duration_Min']:.0f} min", help=str(slowest_del['Delivery']))

    st.divider()

    # --- 2. GRAFY ---
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🏆 Efektivita dle uživatelů (Čisté prostoje)")
        user_sum = df_filtered.groupby('User')['Prodleva_min_Net'].sum().reset_index().sort_values('Prodleva_min_Net', ascending=False)
        fig = px.bar(user_sum, x='User', y='Prodleva_min_Net', color='Prodleva_min_Net', 
                     title="Suma minut prostoje (očištěno o pauzy)", color_continuous_scale='RdYlGn_r')
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("### 📦 Délka trvání Dodávek (Delivery)")
        # Histogram délek dodávek
        fig2 = px.histogram(df_delivery[df_delivery['Delivery_Duration_Min'] < 300], x="Delivery_Duration_Min", 
                            nbins=30, title="Rozložení času kompletace dodávek (minuty)")
        st.plotly_chart(fig2, use_container_width=True)

    # --- 3. DETAILNÍ DATA ---
    st.subheader("📋 Detailní analýza prostojů")
    
    # Příprava detailní tabulky pro zobrazení
    cols_display = [
        'User', 'Transfer Order Number', 'Delivery', 'Material', 
        'PickTimestamp', 'Prodleva_min_Net', 'Prodleva_min_Gross', 'Break_Duration_Sec',
        'Source Storage Bin', 'Dest.Storage Bin', 'Target quantity'
    ]
    # Ošetření, aby sloupce existovaly
    cols_final = [c for c in cols_display if c in df_filtered.columns]
    
    st.dataframe(
        df_filtered[cols_final].sort_values(by='Prodleva_min_Net', ascending=False).style.format({
            'Prodleva_min_Net': '{:.1f}', 
            'Prodleva_min_Gross': '{:.1f}'
        }),
        use_container_width=True
    )

    # --- 4. EXPORT ---
    st.subheader("📥 Export dat")
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        # List 1: Prostoje
        df_export = df_filtered.copy()
        df_export.to_excel(writer, sheet_name='Prostoje_Detail', index=False)
        
        # List 2: Statistiky Dodávek
        df_delivery.to_excel(writer, sheet_name='Delivery_Stats', index=False)
        
        # List 3: Kompletní data (volitelné, může být velké)
        # df.to_excel(writer, sheet_name='Raw_Data', index=False)
        
    st.download_button(
        label="Stáhnout kompletní Profesionální Report (.xlsx)",
        data=buffer.getvalue(),
        file_name=f"Warehouse_Report_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.ms-excel"
    )

else:
    st.info("Nahrajte soubor v bočním menu.")
