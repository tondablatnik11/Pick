import streamlit as st
import pandas as pd
import io
import plotly.express as px
from datetime import datetime, time

# --- KONFIGURACE ---
st.set_page_config(page_title="WMS Analytics v12", layout="wide", page_icon="🏭")

# --- KONSTANTY & NASTAVENÍ ---
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

# --- DATA PRO EXCEL LEGENDU ---
LEGENDA_DATA = [
    {"Sloupec": "User", "Popis": "Identifikace skladníka (osobní číslo)."},
    {"Sloupec": "PickTimestamp", "Popis": "Datum a čas potvrzení položky."},
    {"Sloupec": "Prodleva_min", "Popis": "Čistý čas práce na položce (bez pauz)."},
    {"Sloupec": "Distance_Score", "Popis": "Index vzdálenosti (0=blízko, >20=daleko/změna řady)."},
    {"Sloupec": "Typ_Picku", "Popis": "KLT (Vozík 9ks) nebo Paleta (dle certifikátu)."},
    {"Sloupec": "Material", "Popis": "Číslo materiálu."},
    {"Sloupec": "Material Description", "Popis": "Název/Popis materiálu."},
    {"Sloupec": "Delivery", "Popis": "Číslo dodávky (sdružuje více položek)."},
    {"Sloupec": "Trvani_min", "Popis": "Celkový čas kompletace dodávky (od 1. do posl. kusu)."},
    {"Sloupec": "Transfer Order Number", "Popis": "Číslo konkrétního TO (Transfer Order)."},
    {"Sloupec": "Source Storage Bin", "Popis": "Zdrojová lokace (odkud se bralo)."},
    {"Sloupec": "PrevBin", "Popis": "Předchozí lokace (kde byl skladník předtím)."},
    {"Sloupec": "Clean_UP", "Popis": "Unloading Point (číslo KLT) očištěné od chyb formátu."},
    {"Sloupec": "Row_Num", "Popis": "Číslo řady ve skladu (např. 13)."},
    {"Sloupec": "Bay_Num", "Popis": "Číslo sloupce/pozice v řadě (např. 01)."},
]

# --- POMOCNÉ FUNKCE ---

def clean_delivery_id(val):
    """Oprava formátu Delivery (odstraní .0 a převede na string)."""
    if pd.isna(val): return ""
    s_val = str(val).strip()
    if '.' in s_val:
        try: s_val = str(int(float(s_val)))
        except: pass
    return s_val

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

    # Čištění IDček
    df['Clean_UP'] = df['Unloading Point'].apply(clean_unloading_point)
    if 'Delivery' in df.columns:
        df['Delivery'] = df['Delivery'].apply(clean_delivery_id)

    def classify(row):
        if pd.notna(row.get('Certificate Number', None)): return 'Paleta 📦'
        up = row['Clean_UP']
        if len(up) == 20 and KLT_START <= up <= KLT_END: return 'KLT (Vozík) 🛒'
        return 'Ostatní'
    df['Typ_Picku'] = df.apply(classify, axis=1)

    df = df.sort_values(by=['User', 'PickTimestamp'])
    df['PrevTimestamp'] = df.groupby('User')['PickTimestamp'].shift(1)
    df['PrevBin'] = df.groupby('User')['Source Storage Bin'].shift(1)
    
    df['Net_Seconds'] = df.apply(lambda r: calculate_net_time(r['PrevTimestamp'], r['PickTimestamp']), axis=1)
    df['Prodleva_min'] = df['Net_Seconds'] / 60
    df['Distance_Score'] = df.apply(lambda r: calculate_distance_score(r['Source Storage Bin'], r['PrevBin']), axis=1)
    
    coords = df['Source Storage Bin'].apply(parse_bin_coords)
    df['Row_Num'] = [c[0] if c else None for c in coords]
    df['Bay_Num'] = [c[1] if c else None for c in coords]

    # Delivery Stats
    if 'Delivery' in df.columns:
        del_stats = df.groupby('Delivery').agg(
            Start=('PickTimestamp', 'min'),
            End=('PickTimestamp', 'max'),
            Pocet_Polozek=('Material', 'count'),
            User=('User', 'first')
        ).reset_index()
        del_stats['Trvani_min'] = (del_stats['End'] - del_stats['Start']).dt.total_seconds() / 60
        del_stats = del_stats[del_stats['Trvani_min'] >= 0]
    else:
        del_stats = pd.DataFrame()

    cols = ['User', 'PickTimestamp', 'Prodleva_min', 'Distance_Score', 'Typ_Picku', 
            'Source Storage Bin', 'PrevBin', 'Delivery', 'Transfer Order Number', 'Material', 
            'Material Description', 'Clean_UP', 'Row_Num', 'Bay_Num']
    return df[[c for c in cols if c in df.columns]], del_stats

# --- UI APLIKACE ---
st.title("🏭 Warehouse Analytics v12")
st.markdown("Dashboard pro analýzu pickování, dodávek a materiálů.")

uploaded_file = st.sidebar.file_uploader("Nahrát data", type=['xlsx', 'csv'])

if uploaded_file:
    with st.spinner('Zpracovávám data...'):
        df, df_delivery = process_data(uploaded_file)
        
    if not df.empty:
        # FILTRY
        st.sidebar.header("Filtry")
        users = st.sidebar.multiselect("Skladníci", sorted(df['User'].unique()), default=sorted(df['User'].unique()))
        min_delay = st.sidebar.slider("Minimální prodleva (min)", 0, 90, 10)
        
        # 1. Dataset filtrovaný podle uživatelů (ale bez limitu prodlevy - pro celkové průměry)
        df_users_only = df[df['User'].isin(users)].copy()
        
        # 2. Dataset plně filtrovaný (včetně limitu prodlevy - pro zobrazení incidentů)
        mask = (df['User'].isin(users)) & (df['Prodleva_min'] > min_delay) & (df['Prodleva_min'] < 480)
        df_show = df[mask].copy()

        # ZÁLOŽKY
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "🕵️ Analýza Prostojů", 
            "🚚 Analýza Dodávek", 
            "🗺️ Mapa Skladu", 
            "📦 Analýza Materiálů",
            "📈 Průměrné Statistiky"
        ])

        # 1. PROSTOJE
        with tab1:
            st.info("""
            **Jak číst tento graf:**
            * **Osa X:** Vzdálenost (Distance Score).
            * **Osa Y:** Čas (minuty).
            * **Levý Horní Roh = 🚩 PODEZŘELÉ** (Stál a nejel).
            """)
            if not df_show.empty:
                sc_data = df_show[df_show['Distance_Score'] >= 0]
                if not sc_data.empty:
                    fig = px.scatter(sc_data, x="Distance_Score", y="Prodleva_min", color="User", 
                                     size="Prodleva_min", hover_data=['Source Storage Bin', 'Material', 'Material Description'],
                                     title="Efektivita: Čas vs. Vzdálenost")
                    fig.add_vline(x=20, line_dash="dash", annotation_text="Změna řady")
                    st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df_show.sort_values(by='Prodleva_min', ascending=False).head(100), use_container_width=True)

        # 2. DODÁVKY
        with tab2:
            st.info("""
            **Jak číst tento graf:**
            * Ukazuje **celkový čas** kompletace jedné dodávky.
            * Vysoké sloupce = Problematické dodávky.
            """)
            if not df_delivery.empty:
                top_del = df_delivery.sort_values(by='Trvani_min', ascending=False).head(20)
                st.dataframe(top_del.style.format({'Trvani_min': '{:.1f} min'}), use_container_width=True)
                fig_del = px.bar(top_del.head(10), x='Delivery', y='Trvani_min', color='User',
                                 title="10 Nejpomalejších Dodávek")
                fig_del.update_xaxes(type='category')
                st.plotly_chart(fig_del, use_container_width=True)

        # 3. MAPA
        with tab3:
            st.info("""
            **Jak číst tento graf:**
            * Tmavě červená místa = **Zóny častých prostojů**.
            """)
            if df_show['Row_Num'].notna().any():
                map_data = df_show.groupby(['Row_Num', 'Bay_Num'])['Prodleva_min'].sum().reset_index()
                fig_map = px.density_heatmap(map_data, x="Bay_Num", y="Row_Num", z="Prodleva_min",
                                             nbinsx=37, nbinsy=6, text_auto=True, color_continuous_scale="Reds")
                fig_map.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_map, use_container_width=True)

        # 4. MATERIÁLY
        with tab4:
            st.info("""
            **Jak číst tento graf:**
            * **Osa X:** Frekvence.
            * **Osa Y:** Rychlost (čas).
            * **Pravý Horní Roh = 🚩 KRITICKÉ POLOŽKY.**
            """)
            df_mat = df[df['Prodleva_min'] < 480].copy()
            if not df_mat.empty:
                mat_stats = df_mat.groupby(['Material', 'Material Description']).agg(
                    Pocet_Vyskytu=('Prodleva_min', 'count'),
                    Prumerna_Prodleva=('Prodleva_min', 'mean'),
                    Celkova_Prodleva=('Prodleva_min', 'sum')
                ).reset_index()
                mat_stats = mat_stats[mat_stats['Pocet_Vyskytu'] > 1]
                
                if not mat_stats.empty:
                    fig_mat = px.scatter(
                        mat_stats, x="Pocet_Vyskytu", y="Prumerna_Prodleva", 
                        size="Celkova_Prodleva", color="Prumerna_Prodleva",
                        hover_name="Material Description", color_continuous_scale="RdYlGn_r"
                    )
                    st.plotly_chart(fig_mat, use_container_width=True)
                    st.dataframe(mat_stats.sort_values('Celkova_Prodleva', ascending=False).head(20).style.format({'Prumerna_Prodleva': '{:.1f} min'}), use_container_width=True)

        # 5. PRŮMĚRNÉ STATISTIKY (NOVÉ)
        with tab5:
            st.subheader("📊 Průměrné ukazatele výkonnosti")
            st.markdown("Data vypočítána pro vybrané uživatele.")
            
            # Výpočty nad VŠEMI daty uživatelů (nejen nad incidenty)
            # aby průměr 'Tempo' byl reálný
            avg_pick_time = df_users_only['Prodleva_min'].mean()
            
            # Výpočty nad INCIDENTY (df_show)
            avg_incident = df_show['Prodleva_min'].mean() if not df_show.empty else 0
            
            if not df_delivery.empty:
                avg_delivery = df_delivery['Trvani_min'].mean()
                avg_items = df_delivery['Pocet_Polozek'].mean()
            else:
                avg_delivery = 0
                avg_items = 0

            # KARTY
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Průměrné Tempo (Vše)", f"{avg_pick_time:.1f} min", help="Průměrný čas mezi jakýmikoliv dvěma picky (včetně rychlých).")
            col2.metric(f"Průměrný Incident (>{min_delay} min)", f"{avg_incident:.1f} min", help="Když už nastane prodleva, jak dlouho průměrně trvá.")
            col3.metric("Průměrná Dodávka", f"{avg_delivery:.1f} min", help="Průměrný čas kompletace celé dodávky.")
            col4.metric("Položek na Dodávku", f"{avg_items:.1f} ks")
            
            st.divider()
            
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Porovnání KLT vs Paleta (Průměrný čas)")
                type_stats = df_users_only.groupby('Typ_Picku')['Prodleva_min'].mean().reset_index()
                fig_type = px.bar(type_stats, x='Typ_Picku', y='Prodleva_min', color='Typ_Picku', 
                                  text_auto='.1f', title="Průměrný čas na 1 pick (minuty)")
                st.plotly_chart(fig_type, use_container_width=True)
                
            with c2:
                st.subheader("Výkonnost v čase (Hodinový průměr)")
                # Přidáme hodinu
                df_users_only['Hodina'] = df_users_only['PickTimestamp'].dt.hour
                hourly_stats = df_users_only.groupby('Hodina')['Prodleva_min'].mean().reset_index()
                
                fig_hour = px.line(hourly_stats, x='Hodina', y='Prodleva_min', markers=True,
                                   title="Průměrná doba picku dle hodiny dne")
                fig_hour.update_layout(xaxis=dict(tickmode='linear', dtick=1))
                st.plotly_chart(fig_hour, use_container_width=True)

        # --- EXPORT ---
        st.subheader("📥 Stáhnout Report")
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_show.to_excel(writer, sheet_name='Prostoje_Detail', index=False)
            if 'mat_stats' in locals():
                 mat_stats.sort_values('Celkova_Prodleva', ascending=False).to_excel(writer, sheet_name='Analyza_Materialu', index=False)
            user_stats = df[mask].groupby(['User', 'Typ_Picku'])['Prodleva_min'].agg(['count', 'sum', 'mean']).reset_index()
            user_stats.to_excel(writer, sheet_name='Statistiky_Lidi', index=False)
            if not df_delivery.empty:
                df_delivery.sort_values(by='Trvani_min', ascending=False).to_excel(writer, sheet_name='Nejdelsi_Delivery', index=False)
            
            # Legenda
            pd.DataFrame(LEGENDA_DATA).to_excel(writer, sheet_name='LEGENDA', index=False)
            worksheet = writer.sheets['LEGENDA']
            worksheet.set_column('A:A', 25)
            worksheet.set_column('B:B', 80)

        st.download_button("Stáhnout Kompletní Report (.xlsx)", buffer.getvalue(), "WMS_Master_Report_v12.xlsx")
else:
    st.info("Nahrajte soubor.")
