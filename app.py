import streamlit as st
import pandas as pd
import eurostat
import requests
import plotly.graph_objects as go
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

st.set_page_config(page_title="Eurostat natural gas data", layout="wide")
st.title("📊 Gas consumption (TWh) – Baltics & Finland")

@st.cache_data(ttl=86400) # Välimuistitetaan haku 24 tunniksi
def fetch_eurostat_data():
    df_raw = eurostat.get_data_df("nrg_cb_gasm")
    df_raw = df_raw.reset_index()
    df_raw.columns = [str(c).split('\\')[-1].split(',')[-1].strip().lower() for c in df_raw.columns]
    
    geo_col = 'time_period' if 'time_period' in df_raw.columns else 'geo'
    countries = {'FI': 'Finland', 'EE': 'Estonia', 'LV': 'Latvia', 'LT': 'Lithuania'}
    
    mask = (
        df_raw[geo_col].isin(countries.keys()) & 
        (df_raw['siec'] == 'G3000') & 
        (df_raw['nrg_bal'] == 'IC_OBS')
    )
    df_filtered = df_raw[mask].copy()
    
    is_tj = 'unit' in df_filtered.columns and 'TJ_GCV' in df_filtered['unit'].values
    if is_tj:
        df_filtered = df_filtered[df_filtered['unit'] == 'TJ_GCV']
    else:
        df_filtered = df_filtered[df_filtered['unit'] == 'MIO_M3']
        
    df_filtered['Country'] = df_filtered[geo_col].map(countries)
    date_cols = [c for c in df_filtered.columns if pd.Series(c).astype(str).str.match(r'^\d{4}-\d{2}$').any()]
    
    df_melted = df_filtered.melt(id_vars=['Country'], value_vars=date_cols, var_name='Month', value_name='Volume_Raw')
    df_melted['Volume_Num'] = pd.to_numeric(df_melted['Volume_Raw'], errors='coerce')
    df_melted = df_melted.dropna(subset=['Volume_Num'])
    
    # Varmistetaan että Month-sarake on merkkijono ja suodatetaan vain muotoa YYYY-MM
    df_melted['Month'] = df_melted['Month'].astype(str)
    df_melted = df_melted[df_melted['Month'].str.match(r'^\d{4}-\d{2}$')]
    
    if is_tj:
        df_melted['Value_TWh'] = df_melted['Volume_Num'] * 0.000277778
    else:
        df_melted['Value_TWh'] = (df_melted['Volume_Num'] * 10.55) / 1000
        
    return df_melted

@st.cache_data(ttl=86400)
def fetch_helsinki_temperatures(start_year=2020):
    """
    Hakee Helsinki-Vantaan vuorokauden keskilämpötilat Open-Meteo Archive API:sta,
    laskee kuukausittaiset keskiarvot ja pyöristää ne 1 desimaaliin.
    """
    today_str = datetime.today().strftime('%Y-%m-%d')
    url = f"https://archive-api.open-meteo.com/v1/archive?latitude=60.3172&longitude=24.9633&start_date={start_year}-01-01&end_date={today_str}&daily=temperature_2m_mean&timezone=Europe%2FHelsinki"
    
    try:
        res = requests.get(url, timeout=15)
        data = res.json()
        
        df_temp = pd.DataFrame({
            'date': data['daily']['time'],
            'temp': data['daily']['temperature_2m_mean']
        })
        
        df_temp['date'] = pd.to_datetime(df_temp['date'])
        df_temp['Month'] = df_temp['date'].dt.strftime('%Y-%m')
        
        # Ryhmitellään kuukausittain, lasketaan keskiarvo ja pyöristetään 1 desimaaliin
        monthly_temp = df_temp.groupby('Month')['temp'].mean().round(1).reset_index()
        monthly_temp.rename(columns={'temp': 'Temp_Helsinki'}, inplace=True)
        return monthly_temp.set_index('Month')
    except Exception as e:
        st.warning(f"Could not fetch temperature data: {e}")
        return pd.DataFrame(columns=['Temp_Helsinki'])

with st.spinner("Haetaan uusinta dataa Eurostatista ja Open-Meteosta..."):
    df_melted = fetch_eurostat_data()
    df_temp = fetch_helsinki_temperatures()

# Pivot-taulukko kuukausittain
pivot_df = df_melted.pivot(index='Month', columns='Country', values='Value_TWh')

# Yhdistetään lämpötiladata pivot-taulukkoon
pivot_df = pivot_df.join(df_temp, how='left')

# Valikko kuukausimäärälle kuukausikuvaajassa
months_to_show = st.slider("Select how many months to show:", 6, 36, 18)
df_display = pivot_df.sort_index(ascending=True).tail(months_to_show)

# 1. Kuukausittainen graafi (Stacked Bar + Temperature Line)
st.subheader("📈 Monthly consumption (TWh)")
fig_monthly = go.Figure()
countries_list = ['Finland', 'Estonia', 'Latvia', 'Lithuania']

# 1a. Kulutuspylväät (Ensimmäinen Y-akseli)
for country in countries_list:
    if country in df_display.columns:
        fig_monthly.add_trace(go.Bar(
            x=df_display.index, 
            y=df_display[country], 
            name=country,
            yaxis='y'
        ))

# 1b. Lämpötilaviiva (Toinen Y-akseli)
if 'Temp_Helsinki' in df_display.columns:
    fig_monthly.add_trace(go.Scatter(
        x=df_display.index,
        y=df_display['Temp_Helsinki'],
        name='Temp Helsinki (°C)',
        mode='lines+markers',
        line=dict(color='#d62728', width=3), # Punainen viiva
        marker=dict(size=6),
        yaxis='y2' # Ohjataan toiselle Y-akselille
    ))

# Kaksois-Y-akselin asetukset
fig_monthly.update_layout(
    barmode='stack', 
    template="plotly_white", 
    yaxis=dict(
        title="TWh / month"
    ),
    yaxis2=dict(
        title="Temperature Helsinki",
        overlaying='y',
        side='right',
        showgrid=False, # Ei sotketa ruudukkoa
        tickmode='linear',
        tick0=0,
        dtick=5 # Asteikon luvut 5 asteen välein (-15, -10, -5, 0, 5, 10...)
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1
    ),
    hovermode="x unified"
)

st.plotly_chart(fig_monthly, use_container_width=True)

# 2. Vuosittainen yhteenveto (Vuosigraafi + Vuositaulukko)
st.subheader("📅 Annual consumption (TWh)")

df_melted['Year'] = df_melted['Month'].astype(str).str[:4]
annual_df = df_melted.groupby(['Year', 'Country'])['Value_TWh'].sum().unstack()

# Tunnistetaan uusin vuosi ja kuinka monelta kuukaudelta dataa on
max_year = annual_df.index.max()
latest_month = df_melted['Month'].max()
latest_month_num = int(latest_month.split('-')[1])

# Vuosittainen pinoava pylväskaavio (Stacked Bar Chart)
fig_annual = go.Figure()
for country in countries_list:
    if country in annual_df.columns:
        fig_annual.add_trace(go.Bar(
            x=[f"{y} (YTD)" if str(y) == str(max_year) else str(y) for y in annual_df.index],
            y=annual_df[country],
            name=country
        ))

fig_annual.update_layout(
    barmode='stack', 
    template="plotly_white", 
    yaxis_title="TWh / year",
    xaxis_title="Year"
)
st.plotly_chart(fig_annual, use_container_width=True)

# Muotoillaan riviotsikot taulukkoa varten
annual_df.index = [
    f"{y} (YTD 1-{latest_month_num}kk)" if str(y) == str(max_year) else str(y) 
    for y in annual_df.index
]

annual_df['Total'] = annual_df.sum(axis=1)
annual_df = annual_df.sort_index(ascending=False)

cols_order = [c for c in countries_list if c in annual_df.columns] + ['Total']
annual_df = annual_df[cols_order]

st.dataframe(annual_df.round(3), use_container_width=True)

# 3. Kuukausittainen taulukko
st.subheader("📆 Monthly table (TWh)")
monthly_table = pivot_df.sort_index(ascending=False).head(months_to_show).copy()

table_cols = [c for c in countries_list if c in monthly_table.columns]
monthly_table['Total'] = monthly_table[table_cols].sum(axis=1)

# Pyöristetään kaasudata 3 desimaaliin ja lämpötila 1 desimaaliin taulukkoa varten
formatted_table = monthly_table[table_cols + ['Total']].round(3)
if 'Temp_Helsinki' in monthly_table.columns:
    formatted_table['Temp_Helsinki'] = monthly_table['Temp_Helsinki'].round(1)

st.dataframe(formatted_table, use_container_width=True)
