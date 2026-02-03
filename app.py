import streamlit as st
import pandas as pd
import io
import plotly.express as px
from datetime import datetime, time

# --- KONFIGURACE STRÁNKY ---
st.set_page_config(page_title="WMS Analytics Ultimate", layout="wide", page_icon="🏭")

# --- DEFINICE PAUZ ---
BREAKS = [
    (8, 15, 8, 30),
    (11, 0, 11, 30),
    (12, 45, 13, 0),
    (16, 15, 16, 30),
    (18, 30, 19, 0),
    (20, 30, 20, 45)
]

# --- PARAMETRY SKLADU ---
ROW_CHANGE_PENALTY = 20  # "Cena" za přejetí do jiné řady (ekvivalent X pozic v regálu)
KLT_START = "00496000004606000000"
KLT_END   = "00496000004606000500"

# --- POMOCNÉ FUNKCE ---

def parse_bin_coords(bin_str):
    """
    Rozparsuje string '13-01-01-01' na (Řada, Sloupec).
    Vrací: (row, bay) jako int
    """
    s = str(bin_str).strip()
    # Očekáváme formát XX-XX-XX-XX
    parts = s.split('-')
    if len(parts) >= 2:
        try:
            row = int(parts[0]) # 13 až 18
            bay = int(parts[1]) # 01 až 37
            return row, bay
        except ValueError:
            return None, None
    return None, None

def calculate_distance_score(curr_bin, prev_bin):
    """
    Vypočítá logickou vzdálenost mezi dvěma biny.
    """
    r1, b1 = parse_bin_coords(curr_bin)
    r2, b2 = parse_bin_coords(prev_bin)
    
    if r1 is None or r2 is None:
        return 0 # Nelze spočítat
    
    # Logika: Rozdíl v řadách * Penalizace + Rozdíl v sloupcích
    row_diff = abs(r1 - r2)
    bay_diff = abs(b1 - b2)
    
    return (row_diff * ROW_CHANGE_PENALTY) + bay_diff

def calculate_net_time(start_dt, end_dt):
    """Čistý čas bez pauz."""
    if pd.isna(start_dt) or pd.isna(end_dt): return 0
    total = (end_dt - start_dt).total_seconds()
    if total < 0 or total > 43200: return max(0, total) # Limit 12h

    break_sec = 0
    day = start_dt.date()
    for h1, m1, h2, m2 in BREAKS:
        b_s = datetime.combine(day, time(h1, m1))
        b_e = datetime.combine(day, time(h2, m2))
        ov_s = max(start_dt, b_s)
        ov_e = min(end_dt, b_e)
        if ov_s < ov_e: break_sec += (ov_e - ov_s).total_seconds()
            
    return max(0, total - break_sec)

@st.cache_data
def process_data(uploaded_file):
    # 1. Načtení
    if uploaded_file.name.endswith('.csv'):
        try: df = pd.read_csv(uploaded_file)
        except: uploaded_file.seek(0); df = pd.read_csv(uploaded_file, sep=';')
    else: df = pd.read_excel(uploaded_file)

    # 2. Timestamp & Clean
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str), errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])
    
    # 3. Typ Picku
    def get_type(row):
        if pd.notna(row.get('Certificate Number', None)): return 'Paleta'
        val = str(row.get('Unloading Point', ''))
        # Fix pro vědecký formát excelu
        if 'e+' in val or '.' in val: 
            try: val = '{:.0f}'.format(float(val))
            except: pass
        if len(val) >= 18 and KLT_START <= val <= KLT_END: return 'KLT'
        return 'Ostatní'
    df['Typ'] = df.apply(get_type, axis=1)

    # 4. Řazení a výpočty (User flow)
    df = df.sort_values(by=['User', 'PickTimestamp'])
    df['PrevTimestamp'] = df.groupby('User')['PickTimestamp'].shift(1)
    df['PrevBin'] = df.groupby('User')['Source Storage Bin'].shift(1)
    
    # Časy
    df['Net_Seconds'] = df.apply(lambda r: calculate_net_time(r['PrevTimestamp'], r['PickTimestamp']), axis=1)
    df['Prodleva_min'] = df['Net_Seconds'] / 60
    
    # Vzdálenost
    df['Distance_Score'] = df.apply(lambda r: calculate_distance_score(r['Source Storage Bin'], r['PrevBin']), axis=1)
    
    # 5. Souřadnice pro mapu
    coords = df['Source Storage Bin'].apply(parse_bin_coords)
    df['Row_Num'] = [c[0] if c else None for c in coords]
    df['Bay_Num'] = [c[1] if c else None for c in coords]

    # Clean Output Columns
    cols = ['User', 'PickTimestamp', 'Prodleva_min', 'Distance_Score', 'Typ', 
            'Source Storage Bin', 'PrevBin', 'Transfer Order Number', 'Material', 'Material Description', 'Row_Num', 'Bay_Num']
    final = [c for c in cols if c in df.columns]
    return df[final]

# --- UI ---
st.title("🏭 Ultimate Warehouse Analytics")
st.markdown("Pokročilá analýza zohledňující **pauzy**, **typ balení** a **vzdálenost ve skladu**.")

uploaded_file = st.sidebar.file_uploader("Nahrát data", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Počítám trasy a časy...'):
        df = process_data(uploaded_file)
        
    # Filtry
    st.sidebar.header("Filtry")
    users = st.sidebar.multiselect("Skladníci", sorted(df['User'].unique()), default=sorted(df['User'].unique()))
    min_delay = st.sidebar.slider("Minimální prodleva (min)", 0, 90, 10)
    
    # Aplikace filtru
    # Ignorujeme první pick dne (kde není předchozí čas) a extrémy nad 8 hodin
    mask = (df['User'].isin(users)) & (df['Prodleva_min'] > min_delay) & (df['Prodleva_min'] < 480) & (df['Distance_Score'] > -1)
    df_show = df[mask].copy()
    
    # --- 1. MATICE PODEZŘENÍ (Scatter) ---
    st.subheader("🕵️ Matice Podezření: Čas vs. Vzdálenost")
    st.info("💡 **Jak číst graf:** Body vlevo nahoře jsou **kritické** (Dlouhý čas + Malá vzdálenost). Body vpravo nahoře jsou OK (Dlouhý čas, ale musel jet daleko).")
    
    fig_scatter = px.scatter(
        df_show, 
        x="Distance_Score", 
        y="Prodleva_min", 
        color="User",
        hover_data=['Source Storage Bin', 'PrevBin', 'Material'],
        size='Prodleva_min',
        title="Efektivita přesunu (Osa X: Vzdálenost, Osa Y: Čas)"
    )
    # Přidáme "hranici efektivity" (volitelně)
    st.plotly_chart(fig_scatter, use_container_width=True)
    
    # --- 2. MAPA SKLADU (Heatmap) ---
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🗺️ Kde se nejvíce 'stojí'? (Mapa skladu)")
        if df_show['Row_Num'].notna().any():
            # Agregace prostojů podle pozice
            map_data = df_show.groupby(['Row_Num', 'Bay_Num'])['Prodleva_min'].sum().reset_index()
            fig_map = px.density_heatmap(
                map_data, x="Bay_Num", y="Row_Num", z="Prodleva_min",
                nbinsx=37, nbinsy=6, text_auto=True,
                color_continuous_scale="Reds",
                title="Suma prostojů dle lokace (Řada 13-18)"
            )
            fig_map.update_yaxes(autorange="reversed") # Aby řada 13 byla nahoře
            st.plotly_chart(fig_map, use_container_width=True)
        else:
            st.warning("Nelze zobrazit mapu - nepodařilo se načíst souřadnice binů.")

    with col2:
        st.subheader("📊 Statistiky")
        st.metric("Počet podezřelých picků", len(df_show))
        if not df_show.empty:
            avg_speed = (df_show['Distance_Score'] / df_show['Prodleva_min']).mean()
            st.metric("Průměrná efektivita pohybu", f"{avg_speed:.2f} score/min")
        
        # Top 5 "Hříšníků" (dle sumy času na místě)
        top_sinners = df_show.groupby('User')['Prodleva_min'].sum().sort_values(ascending=False).head(5)
        st.write("Top 5 uživatelů s prostoji (suma minut):")
        st.dataframe(top_sinners)

    # --- 3. DETAILNÍ DATA ---
    st.subheader("📋 Detailní seznam")
    st.dataframe(df_show.sort_values(by='Prodleva_min', ascending=False), use_container_width=True)
    
    # Export
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df_show.to_excel(writer, sheet_name='Detaily', index=False)
    
    st.download_button("📥 Stáhnout Report (.xlsx)", buffer.getvalue(), "Warehouse_Ultimate.xlsx", "application/vnd.ms-excel")

else:
    st.info("Nahrajte soubor.")
