import pandas as pd

def analyza_prodlev_a_celkoveho_casu(soubor, limit_minut=15):
    # 1. Načtení dat
    try:
        df = pd.read_csv(soubor)
    except Exception:
        df = pd.read_excel(soubor)

    # 2. Vytvoření časové značky (Datum + Čas položky .1)
    df['PickTimestamp'] = pd.to_datetime(
        df['Confirmation date.1'].astype(str) + ' ' + df['Confirmation time.1'].astype(str),
        errors='coerce'
    )
    df = df.dropna(subset=['PickTimestamp'])

    # 3. Výpočet celkového času zakázky (Total Duration)
    # Seskupíme podle zakázky a najdeme minimum (start) a maximum (konec)
    stats_zakazky = df.groupby('Transfer Order Number')['PickTimestamp'].agg(['min', 'max'])
    stats_zakazky['Celkovy_cas_zakazky'] = stats_zakazky['max'] - stats_zakazky['min']
    
    # Převedeme na čitelný string (např. "0 days 01:30:00") nebo na minuty
    # Zde nechávám formát timedelta, který je v Excelu dobře čitelný
    
    # Připojíme tuto informaci zpět k hlavní tabulce
    df = df.merge(stats_zakazky[['Celkovy_cas_zakazky']], on='Transfer Order Number', how='left')

    # 4. Seřazení a výpočet prodlev mezi picky
    df_sorted = df.sort_values(by=['Transfer Order Number', 'PickTimestamp'])
    df_sorted['TimeDiff'] = df_sorted.groupby('Transfer Order Number')['PickTimestamp'].diff()
    df_sorted['Prodleva_min'] = df_sorted['TimeDiff'].dt.total_seconds() / 60

    # Kontrola změny uživatele
    df_sorted['User_Prev'] = df_sorted.groupby('Transfer Order Number')['User'].shift(1)
    df_sorted['Zmena_uzivatele'] = df_sorted['User'] != df_sorted['User_Prev']

    # 5. Filtrace a výběr sloupců
    # Filtrujeme jen ty, kde byla prodleva větší než limit
    report = df_sorted[df_sorted['Prodleva_min'] > limit_minut].copy()
    
    # Pro lepší čitelnost v Excelu převedeme celkový čas na minuty nebo hodiny (volitelné)
    # report['Celkovy_cas_hodin'] = report['Celkovy_cas_zakazky'].dt.total_seconds() / 3600

    cols_export = [
        'Transfer Order Number', 
        'Celkovy_cas_zakazky',   # NOVÝ SLOUPEC
        'Prodleva_min',
        'User', 
        'User_Prev',
        'PickTimestamp', 
        'Material',
        'Material Description'
    ]
    
    cols_final = [c for c in cols_export if c in report.columns]

    # Seřadíme podle délky prodlevy (nebo lze řadit podle Celkovy_cas_zakazky)
    return report[cols_final].sort_values(by='Prodleva_min', ascending=False)

# --- POUŽITÍ ---
soubor_data = 'EXPORT_20260203_114657.XLSX - Sheet1.csv'
vysledny_report = analyza_prodlev_a_celkoveho_casu(soubor_data, limit_minut=30)

print(vysledny_report.head(10))
vysledny_report.to_excel('report_prodlev_s_celkovym_casem.xlsx', index=False)