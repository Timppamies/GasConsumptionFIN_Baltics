import streamlit as st
import pandas as pd
import eurostat
import plotly.graph_objects as go
import warnings

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

with st.spinner("Haetaan uusinta dataa Eurostatista..."):
    df_melted = fetch_eurostat_data()

# Pivot-taulukko kuukausittain
pivot_df = df_melted.pivot(index='Month', columns='Country', values='Value_TWh')

# Valikko kuukausimäärälle kuukausikuvaajassa
months_to_show = st.slider("Select how many months to show:", 6, 36, 18)
df_display = pivot_df.sort_index(ascending=True).tail(months_to_show)

# 1. Kuukausittainen graafi (Stacked Bar Chart)
st.subheader("📈 Monthly consumption (TWh)")
fig_monthly = go.Figure()
countries_list = ['Finland', 'Estonia', 'Latvia', 'Lithuania']

for country in countries_list:
    if country in df_display.columns:
        fig_monthly.add_trace(go.Bar(x=df_display.index, y=df_display[country], name=country))

fig_monthly.update_layout(barmode='stack', template="plotly_white", yaxis_title="TWh / month")
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
monthly_table['Koko alue'] = monthly_table.sum(axis=1)
st.dataframe(monthly_table.round(3), use_container_width=True)
