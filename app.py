import streamlit as st
import pandas as pd
import io
import plotly.express as px
from datetime import datetime, time

# --- KONFIGURACE APLIKACE ---
st.set_page_config(page_title="WMS Picker Analytics v7", layout="wide", page_icon="🚜")

# --- DEFINICE PAUZ ---
BREAKS = [
    (8, 15, 8, 30),
    (11, 0, 11, 30),
    (12, 45, 13, 0),
    (16, 15, 16, 30),
    (18, 30, 19, 0),
    (20, 30, 20, 45)
]

ROW_CHANGE_PENALTY = 25 
KLT_START = "00496000004606000000"
KLT_END   = "00496000004606000500"

# --- FUNKCE ---

def clean_unloading_point(val):
    """Oprava KLT kódů z Excelu (4.96E+17 -> string)."""
    if pd.isna(val): return ""
    s_val = str(val).strip()
    if s_val.endswith('.0'): s_val = s_val[:-2]
    if 'E' in s_val or 'e' in s_val:
        try: s_val = "{:.0f}".format(float(s_val))
        except: pass
    if s_val.isdigit() and len(s_val) < 20:
        return s_val.zfill(20)
    return s_val

def parse_bin_coords(bin_str):
    """
    Robustní parser souřadnic.
    Zvládne: '13-01-01-01' i '13010101' (bez pomlček).
    """
    if pd.isna(bin_str): return None, None
    
    # Odstraníme pomlčky a mezery pro jednotné zpracování
    s = str(bin_str).strip().replace('-', '').replace(' ', '')
    
    # Očekáváme formát RRRRSS... (Řada, Sloupec/Bay...)
    # Pokud je délka alespoň 4 znaky (např. 1301...)
    if len(s) >= 4 and s.isdigit():
        try:
            row = int(s[0:2]) # První 2 znaky = Řada (13-18)
            bay = int(s[2:4]) # Další 2 znaky = Bay (01-37)
            
            # Kontrola smysluplnosti souřadnic (dle vašeho plánku)
            if 10 <= row <= 99 and 0 <= bay <= 99:
                return row, bay
        except ValueError:
            pass
            
    return None, None

def calculate_distance_score(curr_bin, prev_bin):
    """Výpočet náročnosti trasy."""
    r1, b1 = parse_bin_coords(curr_bin)
    r2, b2 = parse_bin_coords(prev_bin)
    
    if r1 is None or r2 is None: return -1
    
    row_diff = abs(r1 - r2)
    bay_diff = abs(b1 - b2)
    return (row_diff * ROW_CHANGE_PENALTY) + bay_diff

def calculate_net_time(start_dt, end_dt):
    """Čistý čas bez pauz."""
    if pd.isna(start_dt) or pd.isna(end_dt): return 0
    total = (end_dt - start_dt).total_seconds()
    if total < 0: return 0
    if total > 43200: return total 

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
    try:
        if uploaded_file.name.endswith('.csv'):
            try: df = pd.read_csv(uploaded_file)
            except: uploaded_file.seek(0); df = pd.read_csv(uploaded_file, sep=';')
        else: df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Chyba souboru: {e}"); return pd.DataFrame()

    # Fallback pro názvy sloupců
    if 'Confirmation date.1' not in df.columns and 'Confirmation date' in df.columns:
        df['Confirmation date.1'] = df['Confirmation date']
        df['Confirmation time.1'] = df['Confirmation time']

    # Timestamp
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])

    # Typ Picku
    df['Clean_UP'] = df['Unloading Point'].apply(clean_unloading_point)
    def classify_row(row):
        if pd.notna(row.get('Certificate Number', None)): return 'Paleta 📦'
        up = row['Clean_UP']
        if len(up) == 20 and KLT_START <= up <= KLT_END: return 'KLT (Vozík) 🛒'
        return 'Ostatní'
    df['Typ_Picku'] = df.apply(classify_row, axis=1)

    # Výpočty
    df = df.sort_values(by=['User', 'PickTimestamp'])
    df['PrevTimestamp'] = df.groupby('User')['PickTimestamp'].shift(1)
    df['PrevBin'] = df.groupby('User')['Source Storage Bin'].shift(1)
    
    df['Net_Seconds'] = df.apply(lambda r: calculate_net_time(r['PrevTimestamp'], r['PickTimestamp']), axis=1)
    df['Prodleva_min'] = df['Net_Seconds'] / 60
    
    # Mapování
    df['Distance_Score'] = df.apply(lambda r: calculate_distance_score(r['Source Storage Bin'], r['PrevBin']), axis=1)
    coords = df['Source Storage Bin'].apply(parse_bin_coords)
    df['Row_Num'] = [c[0] if c else None for c in coords]
    df['Bay_Num'] = [c[1] if c else None for c in coords]

    cols = ['User', 'PickTimestamp', 'Prodleva_min', 'Distance_Score', 'Typ_Picku', 
            'Source Storage Bin', 'PrevBin', 'Transfer Order Number', 'Material', 
            'Material Description', 'Clean_UP', 'Row_Num', 'Bay_Num']
    return df[[c for c in cols if c in df.columns]]

# --- UI ---
st.title("🚜 Picker Analytics v7 (Fix)")
uploaded_file = st.sidebar.file_uploader("Nahrát data", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Analyzuji...'):
        df = process_data(uploaded_file)
        
    if not df.empty:
        st.sidebar.header("Filtry")
        users = st.sidebar.multiselect("Skladníci", sorted(df['User'].unique()), default=sorted(df['User'].unique()))
        min_delay = st.sidebar.slider("Minimální prodleva (min)", 0, 60, 10)
        
        mask = (df['User'].isin(users)) & (df['Prodleva_min'] > min_delay) & (df['Prodleva_min'] < 480)
        df_show = df[mask].copy()
        
        # 1. SCATTER PLOT
        st.subheader("🕵️ Matice Efektivity")
        if not df_show.empty:
            sc_data = df_show[df_show['Distance_Score'] >= 0]
            if not sc_data.empty:
                fig = px.scatter(sc_data, x="Distance_Score", y="Prodleva_min", color="User", 
                                 size="Prodleva_min", hover_data=['Source Storage Bin', 'Material'])
                fig.add_vline(x=20, line_dash="dash", annotation_text="Změna řady")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Nepodařilo se spočítat vzdálenosti (zkontrolujte formát 'Source Storage Bin').")
        
        # 2. MAPA
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("🗺️ Heatmapa (Řada 13-18)")
            if df_show['Row_Num'].notna().any():
                map_data = df_show.groupby(['Row_Num', 'Bay_Num'])['Prodleva_min'].sum().reset_index()
                fig_map = px.density_heatmap(map_data, x="Bay_Num", y="Row_Num", z="Prodleva_min",
                                             nbinsx=37, nbinsy=6, text_auto=True, color_continuous_scale="Reds")
                fig_map.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_map, use_container_width=True)
            else:
                st.warning("Chybí souřadnice pro mapu.")
        
        with c2:
            st.subheader("🏆 Statistiky Uživatelů")
            stats = df[mask].groupby(['User', 'Typ_Picku'])['Prodleva_min'].sum().unstack(fill_value=0)
            
            # BEZPEČNÝ VÝPIS BEZ PÁDU APP
            try:
                # Pokus o barevné formátování (vyžaduje matplotlib)
                st.dataframe(stats.style.format("{:.1f} min").background_gradient(cmap='Reds'))
            except ImportError:
                # Fallback, pokud matplotlib chybí
                st.warning("Pro barevné zobrazení tabulky přidejte 'matplotlib' do requirements.txt")
                st.dataframe(stats) # Obyčejná tabulka
            except Exception:
                st.dataframe(stats)

        # 3. EXPORT
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_show.to_excel(writer, sheet_name='Prostoje', index=False)
        st.download_button("Stáhnout Report", buffer.getvalue(), "Report.xlsx")
else:
    st.info("Nahrajte soubor.")
