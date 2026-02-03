import streamlit as st
import pandas as pd
import io
import plotly.express as px
from datetime import datetime, time

# --- KONFIGURACE ---
st.set_page_config(page_title="WMS Analytics v8", layout="wide", page_icon="📦")

# --- KONSTANTY ---
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

# --- LEGENDA (Data pro Excel) ---
LEGENDA_DATA = [
    {"Sloupec": "User", "Popis": "Identifikace skladníka (osobní číslo)."},
    {"Sloupec": "PickTimestamp", "Popis": "Datum a čas potvrzení položky."},
    {"Sloupec": "Prodleva_min", "Popis": "Čas strávený od PŘEDCHOZÍHO picku do TOHOTO picku (v minutách). Očištěno o pauzy."},
    {"Sloupec": "Distance_Score", "Popis": "Index vzdálenosti. 0-5 = blízko, >20 = změna řady. Pokud je prodleva dlouhá a skóre nízké -> PROBLÉM."},
    {"Sloupec": "Typ_Picku", "Popis": "KLT (Vozík 9ks) nebo Paleta (dle certifikátu)."},
    {"Sloupec": "Source Storage Bin", "Popis": "Lokace, kde skladník bral zboží."},
    {"Sloupec": "PrevBin", "Popis": "Lokace, kde byl skladník PŘEDTÍM."},
    {"Sloupec": "Delivery", "Popis": "Číslo dodávky (sdružuje více zakázek)."},
    {"Sloupec": "Transfer Order Number", "Popis": "Číslo konkrétního TO."},
    {"Sloupec": "Clean_UP", "Popis": "Unloading Point (číslo KLT) očištěné od chyb formátu."},
    {"Sloupec": "Delivery_Duration_Min", "Popis": "Celkový čas kompletace celé dodávky (od 1. do poslední položky)."},
]

# --- FUNKCE ---

def clean_unloading_point(val):
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
    if pd.isna(bin_str): return None, None
    s = str(bin_str).strip().replace('-', '').replace(' ', '')
    if len(s) >= 4 and s.isdigit():
        try:
            row = int(s[0:2])
            bay = int(s[2:4])
            if 10 <= row <= 99 and 0 <= bay <= 99: return row, bay
        except ValueError: pass
    return None, None

def calculate_distance_score(curr_bin, prev_bin):
    r1, b1 = parse_bin_coords(curr_bin)
    r2, b2 = parse_bin_coords(prev_bin)
    if r1 is None or r2 is None: return -1
    return (abs(r1 - r2) * ROW_CHANGE_PENALTY) + abs(b1 - b2)

def calculate_net_time(start_dt, end_dt):
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
    except Exception as e: st.error(f"Chyba: {e}"); return pd.DataFrame(), pd.DataFrame()

    if 'Confirmation date.1' not in df.columns and 'Confirmation date' in df.columns:
        df['Confirmation date.1'] = df['Confirmation date']
        df['Confirmation time.1'] = df['Confirmation time']

    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])

    df['Clean_UP'] = df['Unloading Point'].apply(clean_unloading_point)
    def classify(row):
        if pd.notna(row.get('Certificate Number', None)): return 'Paleta 📦'
        up = row['Clean_UP']
        if len(up) == 20 and KLT_START <= up <= KLT_END: return 'KLT (Vozík) 🛒'
        return 'Ostatní'
    df['Typ_Picku'] = df.apply(classify, axis=1)

    # Řazení a výpočty
    df = df.sort_values(by=['User', 'PickTimestamp'])
    df['PrevTimestamp'] = df.groupby('User')['PickTimestamp'].shift(1)
    df['PrevBin'] = df.groupby('User')['Source Storage Bin'].shift(1)
    
    df['Net_Seconds'] = df.apply(lambda r: calculate_net_time(r['PrevTimestamp'], r['PickTimestamp']), axis=1)
    df['Prodleva_min'] = df['Net_Seconds'] / 60
    df['Distance_Score'] = df.apply(lambda r: calculate_distance_score(r['Source Storage Bin'], r['PrevBin']), axis=1)
    
    # Mapování
    coords = df['Source Storage Bin'].apply(parse_bin_coords)
    df['Row_Num'] = [c[0] if c else None for c in coords]
    df['Bay_Num'] = [c[1] if c else None for c in coords]

    # --- NOVÉ: VÝPOČET DELIVERIES ---
    # Seskupíme data podle Delivery, najdeme start (min time) a konec (max time)
    if 'Delivery' in df.columns:
        del_stats = df.groupby('Delivery').agg(
            Start=('PickTimestamp', 'min'),
            End=('PickTimestamp', 'max'),
            Pocet_Polozek=('Material', 'count'),
            User=('User', 'first')
        ).reset_index()
        del_stats['Trvani_min'] = (del_stats['End'] - del_stats['Start']).dt.total_seconds() / 60
        # Ošetření záporných hodnot (pokud jsou data divná)
        del_stats = del_stats[del_stats['Trvani_min'] >= 0]
    else:
        del_stats = pd.DataFrame()

    cols = ['User', 'PickTimestamp', 'Prodleva_min', 'Distance_Score', 'Typ_Picku', 
            'Source Storage Bin', 'PrevBin', 'Delivery', 'Transfer Order Number', 'Material', 
            'Material Description', 'Clean_UP', 'Row_Num', 'Bay_Num']
    
    final_cols = [c for c in cols if c in df.columns]
    return df[final_cols], del_stats

# --- UI ---
st.title("🏭 Warehouse Analytics v8")
st.markdown("Obsahuje **Legendu**, **Analýzu Dodávek** a **Očištění dat**.")

uploaded_file = st.sidebar.file_uploader("Nahrát data", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Zpracovávám data...'):
        df, df_delivery = process_data(uploaded_file)
        
    if not df.empty:
        st.sidebar.header("Filtry")
        users = st.sidebar.multiselect("Skladníci", sorted(df['User'].unique()), default=sorted(df['User'].unique()))
        min_delay = st.sidebar.slider("Minimální prodleva (min)", 0, 90, 10)
        
        mask = (df['User'].isin(users)) & (df['Prodleva_min'] > min_delay) & (df['Prodleva_min'] < 480)
        df_show = df[mask].copy()

        # TABS pro přehlednost
        tab1, tab2, tab3 = st.tabs(["🕵️ Analýza Prostojů", "🚚 Analýza Dodávek", "🗺️ Mapa Skladu"])

        with tab1:
            st.subheader("Detailní přehled prostojů")
            if not df_show.empty:
                sc_data = df_show[df_show['Distance_Score'] >= 0]
                if not sc_data.empty:
                    fig = px.scatter(sc_data, x="Distance_Score", y="Prodleva_min", color="User", 
                                     size="Prodleva_min", hover_data=['Source Storage Bin'],
                                     title="Efektivita: Čas vs. Vzdálenost")
                    fig.add_vline(x=20, line_dash="dash", annotation_text="Změna řady")
                    st.plotly_chart(fig, use_container_width=True)
                
                st.dataframe(df_show.sort_values(by='Prodleva_min', ascending=False).head(100))
            else:
                st.info("Žádné prostoje v tomto nastavení.")

        with tab2:
            st.subheader("🚚 Top 20 Nejdelších Dodávek (Deliveries)")
            if not df_delivery.empty:
                top_del = df_delivery.sort_values(by='Trvani_min', ascending=False).head(20)
                
                # Formátování tabulky
                st.dataframe(
                    top_del.style.format({'Trvani_min': '{:.1f} min'}),
                    use_container_width=True
                )
                
                # Graf
                fig_del = px.bar(top_del.head(10), x='Delivery', y='Trvani_min', color='User',
                                 title="10 Nejpomalejších Dodávek", text_auto='.0f')
                st.plotly_chart(fig_del, use_container_width=True)
            else:
                st.warning("Data neobsahují sloupec 'Delivery'.")

        with tab3:
            if df_show['Row_Num'].notna().any():
                st.subheader("Heatmapa prostojů")
                map_data = df_show.groupby(['Row_Num', 'Bay_Num'])['Prodleva_min'].sum().reset_index()
                fig_map = px.density_heatmap(map_data, x="Bay_Num", y="Row_Num", z="Prodleva_min",
                                             nbinsx=37, nbinsy=6, text_auto=True, color_continuous_scale="Reds")
                fig_map.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_map, use_container_width=True)
            else:
                st.info("Chybí souřadnice.")

        # --- EXPORT ---
        st.subheader("📥 Stáhnout Report")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            # 1. Prostoje
            df_show.to_excel(writer, sheet_name='Prostoje_Detail', index=False)
            
            # 2. Statistiky lidí
            user_stats = df[mask].groupby(['User', 'Typ_Picku'])['Prodleva_min'].agg(['count', 'sum', 'mean']).reset_index()
            user_stats.to_excel(writer, sheet_name='Statistiky_Lidi', index=False)
            
            # 3. Deliveries
            if not df_delivery.empty:
                df_delivery.sort_values(by='Trvani_min', ascending=False).to_excel(writer, sheet_name='Nejdelsi_Delivery', index=False)
            
            # 4. LEGENDA
            pd.DataFrame(LEGENDA_DATA).to_excel(writer, sheet_name='LEGENDA', index=False)
            
            # Formátování šířky sloupců v legendě (volitelné vylepšení)
            worksheet = writer.sheets['LEGENDA']
            worksheet.set_column('A:A', 20)
            worksheet.set_column('B:B', 80)

        st.download_button("Stáhnout Excel Report", buffer.getvalue(), "WMS_Report_v8.xlsx")
else:
    st.info("Nahrajte soubor.")
