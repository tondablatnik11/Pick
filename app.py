import streamlit as st
import pandas as pd
import io
import plotly.express as px
from datetime import datetime, time

# --- KONFIGURACE APLIKACE ---
st.set_page_config(page_title="WMS Picker Analytics v6", layout="wide", page_icon="🚜")

# --- PARAMETRY PROVOZU ---
# Definice pauz (Začátek hod, min, Konec hod, min)
BREAKS = [
    (8, 15, 8, 30),
    (11, 0, 11, 30),
    (12, 45, 13, 0),
    (16, 15, 16, 30),
    (18, 30, 19, 0),
    (20, 30, 20, 45)
]

# Penalizace: Kolik "bodů" stojí změna uličky (otočení s ještěrkou trvá)
ROW_CHANGE_PENALTY = 25 

# Rozsah KLT (dle vašeho zadání)
KLT_START = "00496000004606000000"
KLT_END   = "00496000004606000500"

# --- FUNKCE PRO PRÁCI S ČASEM A DATY ---

def clean_unloading_point(val):
    """
    Kritická oprava: Excel exportuje dlouhá čísla jako 4.96E+17.
    Tato funkce to vrátí zpět na plný textový řetězec KLT kódu.
    """
    if pd.isna(val): return ""
    s_val = str(val).strip()
    
    # Odstraníme .0 (pokud vzniklo floatem)
    if s_val.endswith('.0'): s_val = s_val[:-2]
    
    # Oprava vědeckého formátu (4.96E+17 -> 49600...)
    if 'E' in s_val or 'e' in s_val:
        try:
            s_val = "{:.0f}".format(float(s_val))
        except:
            pass # Necháme jak je, pokud to nejde
            
    # Doplnění nul na 20 znaků (formát Unloading Point)
    if s_val.isdigit() and len(s_val) < 20:
        return s_val.zfill(20)
        
    return s_val

def parse_bin_coords(bin_str):
    """Získá souřadnice z Bin Code (např. 13-01-01-01 -> Řada 13, Sloupec 01)."""
    if pd.isna(bin_str): return None, None
    s = str(bin_str).strip().replace(' ', '')
    parts = s.split('-')
    
    # Logika pro formát XX-XX-XX-XX
    if len(parts) >= 2:
        try:
            row = int(parts[0]) # Řada
            bay = int(parts[1]) # Sloupec (Bay)
            return row, bay
        except ValueError:
            pass
            
    return None, None

def calculate_distance_score(curr_bin, prev_bin):
    """
    Počítá logistickou náročnost přesunu.
    Vyšší číslo = delší cesta / náročnější manévr.
    """
    r1, b1 = parse_bin_coords(curr_bin)
    r2, b2 = parse_bin_coords(prev_bin)
    
    if r1 is None or r2 is None: return -1 # Neznámá vzdálenost
    
    # Změna řady je pro ještěrku náročná (vycouvat z uličky, přejet, najet)
    row_diff = abs(r1 - r2)
    # Změna sloupce je jen jízda rovně
    bay_diff = abs(b1 - b2)
    
    return (row_diff * ROW_CHANGE_PENALTY) + bay_diff

def calculate_net_time(start_dt, end_dt):
    """Vypočítá čistý pracovní čas (odečte pauzy)."""
    if pd.isna(start_dt) or pd.isna(end_dt): return 0
    total = (end_dt - start_dt).total_seconds()
    
    # Ošetření chyb (záporný čas) nebo extrémů (přes noc > 12h)
    if total < 0: return 0
    if total > 43200: return total # Necháme hrubý čas, je to podezřelé tak jako tak

    break_sec = 0
    day = start_dt.date()
    
    # Projdeme všechny pauzy a odečteme průniky
    for h1, m1, h2, m2 in BREAKS:
        b_start = datetime.combine(day, time(h1, m1))
        b_end = datetime.combine(day, time(h2, m2))
        
        ov_start = max(start_dt, b_start)
        ov_end = min(end_dt, b_end)
        
        if ov_start < ov_end:
            break_sec += (ov_end - ov_start).total_seconds()
            
    return max(0, total - break_sec)

# --- NAČÍTÁNÍ DAT ---
@st.cache_data
def process_data(uploaded_file):
    try:
        if uploaded_file.name.endswith('.csv'):
            try: df = pd.read_csv(uploaded_file)
            except: uploaded_file.seek(0); df = pd.read_csv(uploaded_file, sep=';')
        else: 
            df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Chyba formátu souboru: {e}")
        return pd.DataFrame()

    # 1. Kontrola sloupců
    required_cols = ['Confirmation date.1', 'Confirmation time.1', 'User', 'Unloading Point']
    missing = [c for c in required_cols if c not in df.columns]
    
    # Fallback pokud chybí .1 sloupce (některé exporty je nemají)
    if 'Confirmation date.1' in missing:
        if 'Confirmation date' in df.columns:
            df['Confirmation date.1'] = df['Confirmation date']
            df['Confirmation time.1'] = df['Confirmation time']
        else:
            st.error("Chybí sloupce s časem (Confirmation date/time).")
            return pd.DataFrame()

    # 2. Vytvoření časové osy
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])

    # 3. Oprava a Klasifikace KLT / Paleta
    df['Clean_UP'] = df['Unloading Point'].apply(clean_unloading_point)

    def classify_row(row):
        # Paleta má certifikát
        if pd.notna(row.get('Certificate Number', None)): return 'Paleta 📦'
        
        # KLT podle Unloading Point (Batch 9 KLT)
        up = row['Clean_UP']
        if len(up) == 20 and KLT_START <= up <= KLT_END:
            return 'KLT (Vozík) 🛒'
            
        return 'Ostatní'

    df['Typ_Picku'] = df.apply(classify_row, axis=1)

    # 4. Řazení a Výpočet Pick-to-Pick (User Flow)
    df = df.sort_values(by=['User', 'PickTimestamp'])
    
    # Posun o 1 řádek -> předchozí akce téhož člověka
    df['PrevTimestamp'] = df.groupby('User')['PickTimestamp'].shift(1)
    df['PrevBin'] = df.groupby('User')['Source Storage Bin'].shift(1)
    
    # Výpočet časů
    df['Net_Seconds'] = df.apply(lambda r: calculate_net_time(r['PrevTimestamp'], r['PickTimestamp']), axis=1)
    df['Prodleva_min'] = df['Net_Seconds'] / 60
    
    # Výpočet tras
    df['Distance_Score'] = df.apply(lambda r: calculate_distance_score(r['Source Storage Bin'], r['PrevBin']), axis=1)
    
    # Extrakce souřadnic pro mapu
    coords = df['Source Storage Bin'].apply(parse_bin_coords)
    df['Row_Num'] = [c[0] if c else None for c in coords]
    df['Bay_Num'] = [c[1] if c else None for c in coords]

    # Vyčištění datasetu pro export
    cols = ['User', 'PickTimestamp', 'Prodleva_min', 'Distance_Score', 'Typ_Picku', 
            'Source Storage Bin', 'PrevBin', 'Transfer Order Number', 'Material', 
            'Material Description', 'Clean_UP', 'Row_Num', 'Bay_Num']
    
    return df[[c for c in cols if c in df.columns]]

# --- UI LOGIKA ---
st.title("🚜 Picker Performance Analytics v6")
st.markdown("""
**Specializace:** Ještěrky & Retraky | Batch Picking (9 KLT) | Palety
""")

uploaded_file = st.sidebar.file_uploader("Nahrát export (.xlsx / .csv)", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Analyzuji trasy, odečítám pauzy, opravuji KLT kódy...'):
        df = process_data(uploaded_file)
        
    if not df.empty:
        # --- SIDEBAR FILTRY ---
        st.sidebar.header("Filtry")
        users = st.sidebar.multiselect("Skladníci", sorted(df['User'].unique()), default=sorted(df['User'].unique()))
        min_delay = st.sidebar.slider("Minimální prodleva (min)", 0, 60, 10)
        types = st.sidebar.multiselect("Typ Picku", df['Typ_Picku'].unique(), default=df['Typ_Picku'].unique())
        
        # Aplikace filtrů
        mask = (
            (df['User'].isin(users)) & 
            (df['Prodleva_min'] > min_delay) & 
            (df['Prodleva_min'] < 480) & # Ignorujeme extrémy > 8h
            (df['Typ_Picku'].isin(types))
        )
        df_show = df[mask].copy()
        
        # --- 1. MATICE EFEKTIVITY ---
        st.subheader("🕵️ Matice Efektivity (Čas vs. Trasa)")
        st.info("Levý horní roh = **Podezřelé** (Dlouho stál a nikam nejel). Pravý horní = **OK** (Jel daleko).")
        
        if not df_show.empty:
            # Filtrujeme jen ty, kde známe vzdálenost (-1 jsou chyby souřadnic)
            scatter_data = df_show[df_show['Distance_Score'] >= 0]
            
            fig = px.scatter(
                scatter_data, x="Distance_Score", y="Prodleva_min", 
                color="User", size="Prodleva_min",
                hover_data=['Source Storage Bin', 'PrevBin', 'Material'],
                title=f"Analýza {len(scatter_data)} incidentů"
            )
            # Přidáme svislou čáru oddělující "krátké" a "dlouhé" přesuny
            fig.add_vline(x=20, line_dash="dash", annotation_text="Změna řady")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Žádná data neodpovídají filtrům.")

        # --- 2. MAPA PROVOZU ---
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("🗺️ Heatmapa prostojů (Řady 13-18)")
            if df_show['Row_Num'].notna().any():
                # Agregace: Kde se nejvíc "proflákalo" času
                map_data = df_show.groupby(['Row_Num', 'Bay_Num'])['Prodleva_min'].sum().reset_index()
                
                fig_map = px.density_heatmap(
                    map_data, x="Bay_Num", y="Row_Num", z="Prodleva_min",
                    nbinsx=37, nbinsy=6, text_auto=True,
                    color_continuous_scale="Reds",
                    title="Suma minut prostojů dle lokace"
                )
                fig_map.update_yaxes(autorange="reversed") # Aby řada 13 byla nahoře
                st.plotly_chart(fig_map, use_container_width=True)
            else:
                st.info("Chybí data o souřadnicích pro mapu.")
                
        with col2:
            st.subheader("🏆 Top Skladníci (dle typu)")
            # Pivot table pro přehled Paleta vs KLT
            stats = df[mask].groupby(['User', 'Typ_Picku'])['Prodleva_min'].sum().unstack(fill_value=0)
            st.dataframe(stats.style.format("{:.1f} min").background_gradient(cmap='Reds'))

        # --- 3. EXPORT ---
        st.subheader("📥 Export dat")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_show.to_excel(writer, sheet_name='Prostoje_Detail', index=False)
            # Exportujeme i raw data pro vaši kontrolu
            df.head(1000).to_excel(writer, sheet_name='Ukazka_Raw_Data', index=False)
            
        st.download_button(
            "Stáhnout kompletní report (.xlsx)", 
            buffer.getvalue(), 
            "WMS_Report_Final.xlsx", 
            "application/vnd.ms-excel"
        )
else:
    st.info("👈 Nahrajte soubor vlevo.")
