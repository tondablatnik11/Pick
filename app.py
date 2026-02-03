import streamlit as st
import pandas as pd
import io

# Nastavení stránky
st.set_page_config(page_title="Analýza Pickování", layout="wide")

st.title("📦 Analýza prodlev v pickování")
st.write("Nahrajte export zakázek (Excel nebo CSV) a aplikace najde prodlevy.")

def analyza_prodlev(uploaded_file, limit_minut):
    # Detekce typu souboru podle přípony
    if uploaded_file.name.endswith('.csv'):
        # Pro CSV musíme specifikovat oddělovač, pokud je to Excel-CSV, bývá to středník nebo čárka
        try:
            df = pd.read_csv(uploaded_file)
        except:
            # Fallback, zkusíme jiný oddělovač nebo encoding, pokud první selže
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=';')
    else:
        df = pd.read_excel(uploaded_file)

    # 1. Vytvoření časové značky (Datum + Čas položky .1)
    # Spojíme sloupce a převedeme na datetime
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    
    # Odstraníme řádky bez platného času
    df = df.dropna(subset=['PickTimestamp'])

    # 2. Výpočet celkového času zakázky
    stats_zakazky = df.groupby('Transfer Order Number')['PickTimestamp'].agg(['min', 'max'])
    stats_zakazky['Celkovy_cas_zakazky'] = stats_zakazky['max'] - stats_zakazky['min']
    
    # Připojíme info o celkovém čase zpět
    df = df.merge(stats_zakazky[['Celkovy_cas_zakazky']], on='Transfer Order Number', how='left')

    # 3. Seřazení a výpočet prodlev (delt)
    df_sorted = df.sort_values(by=['Transfer Order Number', 'PickTimestamp'])
    
    # Výpočet rozdílu časů v rámci jedné zakázky
    df_sorted['TimeDiff'] = df_sorted.groupby('Transfer Order Number')['PickTimestamp'].diff()
    df_sorted['Prodleva_min'] = df_sorted['TimeDiff'].dt.total_seconds() / 60

    # Kontrola změny uživatele (zda předchozí pick dělal někdo jiný)
    df_sorted['User_Prev'] = df_sorted.groupby('Transfer Order Number')['User'].shift(1)
    
    # 4. Filtrace výsledků
    report = df_sorted[df_sorted['Prodleva_min'] > limit_minut].copy()
    
    # Formátování pro hezčí výpis
    report['Celkovy_cas_str'] = report['Celkovy_cas_zakazky'].astype(str).str.split('.').str[0] # Odstraní milisekundy

    cols_export = [
        'Transfer Order Number', 
        'Celkovy_cas_str',
        'Prodleva_min',
        'User', 
        'User_Prev',
        'PickTimestamp', 
        'Material',
        'Material Description'
    ]
    
    # Vybereme jen existující sloupce
    cols_final = [c for c in cols_export if c in report.columns]
    
    return report[cols_final].sort_values(by='Prodleva_min', ascending=False)

# --- HLAVNÍ ČÁST STREAMLIT APLIKACE ---

# Widget pro nahrání souboru
uploaded_file = st.file_uploader("Vyberte soubor", type=['xlsx', 'csv'])

# Posuvník pro nastavení limitu minut
limit_minut = st.slider("Minimální délka prodlevy (minuty)", min_value=5, max_value=120, value=30, step=5)

if uploaded_file is not None:
    try:
        with st.spinner('Analyzuji data...'):
            vysledny_report = analyza_prodlev(uploaded_file, limit_minut)
        
        st.success(f"Nalezeno {len(vysledny_report)} záznamů s prodlevou > {limit_minut} minut.")
        
        # Zobrazení tabulky
        st.dataframe(vysledny_report, use_container_width=True)
        
        # Tlačítko pro stažení výsledku
        # Převedeme dataframe do Excelu v paměti
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            vysledny_report.to_excel(writer, index=False, sheet_name='Report')
            
        st.download_button(
            label="📥 Stáhnout report jako Excel",
            data=buffer.getvalue(),
            file_name="report_prodlev.xlsx",
            mime="application/vnd.ms-excel"
        )
            
    except Exception as e:
        st.error(f"Došlo k chybě při zpracování souboru: {e}")
        st.info("Zkontrolujte, zda soubor obsahuje sloupce 'Confirmation date.1' a 'Confirmation time.1'.")
