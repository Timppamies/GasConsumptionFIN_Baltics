"""FinBalt Regional Gas Consumption: monthly and yearly consumption per country.

Data: ENTSOG Transparency Platform (gas), FMI open data (temperature).
Run with:  streamlit run finbalt_gas_consumption.py
"""
import math
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

st.set_page_config(page_title="FinBalt Gas Consumption", page_icon="📈", layout="wide")

URL = "https://transparency.entsog.eu/api/v1/operationalData.json"
FMI_URL = "https://opendata.fmi.fi/wfs"
FMI_FMISID = "100968"  # Helsinki-Vantaa airport weather station
HISTORY_START = date(2024, 1, 1)  # first full year shown in the yearly chart
CHUNK_DAYS = 180  # one ENTSOG request covers at most this many days

# (group, operatorKey, pointKey, direction, label)
SERIES = [
    # Direct consumption points (aggregated "Final consumers")
    ("EE_cons", "EE-TSO-0001", "FNC-00037", "exit", "Estonia final consumers"),
    ("LV_cons", "LV-TSO-0001", "FNC-00205", "exit", "Latvia domestic consumption"),
    # Finland: balance (no consumption point reported)
    ("FI_in", "FI-TSO-0003", "ITP-00550", "entry", "Balticconnector (EE→FI)"),
    ("FI_in", "FI-TSO-0003", "LNG-00011", "entry", "Hamina LNG"),
    ("FI_in", "FI-TSO-0003", "LNG-00072", "entry", "Inkoo LNG"),
    ("FI_out", "FI-TSO-0003", "ITP-00550", "exit", "Balticconnector (FI→EE)"),
    # Lithuania: balance (no consumption point reported)
    ("LT_in", "LT-TSO-0001", "LNG-00030", "entry", "Klaipėda LNG"),
    ("LT_in", "LT-TSO-0001", "ITP-00054", "entry", "Kiemenai (LV→LT)"),
    ("LT_in", "LT-TSO-0001", "ITP-00556", "entry", "Santaka (PL→LT)"),
    ("LT_in", "LT-TSO-0001", "ITP-00085", "entry", "Kotlovka (BY→LT)"),
    ("LT_out_lv", "LT-TSO-0001", "ITP-00054", "exit", "Kiemenai (LT→LV)"),
    ("LT_gipl", "LT-TSO-0001", "ITP-00556", "exit", "Santaka (LT→PL)"),
    ("LT_kal", "LT-TSO-0001", "ITP-00050", "exit", "Sakiai (LT→RU)"),
]
GROUPS = ["EE_cons", "LV_cons", "FI_in", "FI_out",
          "LT_in", "LT_out_lv", "LT_gipl", "LT_kal"]

COUNTRY_COLS = ["Finland", "Estonia", "Latvia", "Lithuania"]
COLORS = {
    "Finland": "#1f77b4",
    "Estonia": "#2ca02c",
    "Latvia": "#ff7f0e",
    "Lithuania": "#9467bd",
}
TEMP_COLOR = "#d62728"
TEMP_COL = "Helsinki-Vantaa temp (°C)"


# ------------------------------------------------------------ ENTSOG fetch
def _request(params):
    """GET with retries. Raises RuntimeError if all attempts fail."""
    err = "unknown error"
    for attempt in range(4):
        try:
            r = requests.get(URL, params=params, timeout=120)
            if r.status_code == 200:
                return r.json().get("operationalData", [])
            err = f"HTTP {r.status_code}"
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break  # retrying will not help
        except (requests.RequestException, ValueError) as e:
            err = type(e).__name__
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(err)


def _chunks(start, end, days):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_series(operator, point, direction, start_str, end_str):
    """Daily Physical Flow (kWh/d) for one operator/point/direction.

    Raises if any chunk fails, so partial results are never cached.
    """
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)

    def one(rng):
        a, b = rng
        return _request({
            "indicator": "Physical Flow",
            "periodType": "day",
            "from": a.isoformat(),
            "to": b.isoformat(),
            "pointKey": point,
            "directionKey": direction,
            "limit": -1,
        })

    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = list(ex.map(one, list(_chunks(start, end, CHUNK_DAYS))))

    days, dupes = {}, 0
    for recs in parts:
        for x in recs:
            if (x.get("operatorKey") != operator
                    or x.get("pointKey") != point
                    or x.get("directionKey") != direction):
                continue
            day = str(x.get("periodFrom", ""))[:10]
            try:
                value = float(x.get("value") or 0)
            except (TypeError, ValueError):
                value = 0.0
            if day in days:
                dupes += 1
            days[day] = value
    return {"days": days, "dupes": dupes}


def load_gas(start_str, end_str):
    frames, failed, dupes = [], [], 0
    bar = st.progress(0.0, text="Loading ENTSOG data...")
    for i, (grp, op, pt, d, label) in enumerate(SERIES):
        bar.progress(i / len(SERIES), text=f"Loading {label} ({i + 1}/{len(SERIES)})")
        try:
            res = fetch_series(op, pt, d, start_str, end_str)
        except Exception as e:
            failed.append(f"{label} [{op} {pt} {d}]: {e}")
            continue
        dupes += res["dupes"]
        if res["days"]:
            frames.append(pd.DataFrame({
                "day": list(res["days"].keys()),
                "TWh": [v / 1e9 for v in res["days"].values()],  # kWh/d -> TWh
                "group": grp,
                "series": f"{grp}: {label}",
            }))
    bar.empty()
    if frames:
        raw = pd.concat(frames, ignore_index=True)
    else:
        raw = pd.DataFrame(columns=["day", "TWh", "group", "series"])
    return raw, failed, dupes


def build_monthly(raw):
    raw = raw.copy()
    raw["Month"] = raw["day"].str[:7]
    g = raw.pivot_table(index="Month", columns="group", values="TWh", aggfunc="sum")
    g = g.reindex(columns=GROUPS).fillna(0.0)

    out = pd.DataFrame(index=g.index)
    out["Finland"] = g["FI_in"] - g["FI_out"]
    out["Estonia"] = g["EE_cons"]
    out["Latvia"] = g["LV_cons"]
    out["Lithuania"] = (g["LT_in"] - g["LT_out_lv"] - g["LT_gipl"] - g["LT_kal"])
    return out.sort_index()


# ------------------------------------------------------------- FMI weather
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_temperature(start_str, end_str):
    """Daily mean temperature (tday, deg C) at Helsinki-Vantaa from FMI open data."""
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)

    def one(rng):
        a, b = rng
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": "fmi::observations::weather::daily::simple",
            "fmisid": FMI_FMISID,
            "parameters": "tday",
            "starttime": f"{a.isoformat()}T00:00:00Z",
            "endtime": f"{b.isoformat()}T00:00:00Z",
        }
        err = "unknown error"
        for attempt in range(3):
            try:
                r = requests.get(FMI_URL, params=params, timeout=60)
                if r.status_code == 200:
                    return r.content
                err = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                err = type(e).__name__
            time.sleep(3 * (attempt + 1))
        raise RuntimeError(err)

    with ThreadPoolExecutor(max_workers=4) as ex:
        parts = list(ex.map(one, list(_chunks(start, end, 31))))

    temps = {}
    for content in parts:
        root = ET.fromstring(content)
        for el in root.iter():
            if not el.tag.endswith("BsWfsElement"):
                continue
            t = v = None
            for c in el:
                name = c.tag.rsplit("}", 1)[-1]
                if name == "Time":
                    t = c.text
                elif name == "ParameterValue":
                    v = c.text
            if t and v and v.strip().lower() != "nan":
                try:
                    temps[t[:10]] = float(v)
                except ValueError:
                    pass
    return temps


def monthly_temperature(temps):
    if not temps:
        return pd.Series(dtype=float)
    df = pd.DataFrame({"day": list(temps.keys()), "t": list(temps.values())})
    df["Month"] = df["day"].str[:7]
    return df.groupby("Month")["t"].mean()


# ---------------------------------------------------------------------- UI
st.title("📈 FinBalt Regional Gas Consumption")
st.markdown(
    "Gas consumption per country in **Finland, Estonia, Latvia and Lithuania** "
    "(ENTSOG, daily Physical Flow), monthly with Helsinki-Vantaa temperature, "
    "and yearly totals."
)

if st.sidebar.button("Clear cache & refresh 🔄"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(
    "Data is cached for 6 hours. Series that fail to load are retried on the "
    "next refresh; successful ones stay cached."
)

today = date.today()
raw, failed, dupes = load_gas(HISTORY_START.isoformat(), today.isoformat())

if failed:
    st.warning(
        "Some ENTSOG series could not be loaded, so the figures below may be "
        "incomplete. Press the refresh button to retry (the rest is cached)."
    )
    with st.expander("Failed series"):
        for f in failed:
            st.write(f)

if raw.empty:
    st.error("No flow data retrieved from ENTSOG.")
    st.stop()

monthly = build_monthly(raw)

# Temperature (optional: the app still works without it)
try:
    temps = fetch_temperature(HISTORY_START.isoformat(), today.isoformat())
    temp_m = monthly_temperature(temps)
except Exception as e:
    temps, temp_m = {}, pd.Series(dtype=float)
    st.warning(f"Temperature data could not be loaded from FMI ({e}). "
               "The chart is shown without the temperature line.")

n_months = len(monthly)
max_m = max(n_months, 4)
months_to_show = st.sidebar.slider("Months in monthly chart:", 3, max_m,
                                   min(12, max_m), 1)
bar_mode = st.sidebar.radio("Monthly chart style:", ["Stacked", "Grouped"])

last_day = date.fromisoformat(raw["day"].max())
latest = monthly.index[-1]
month_end = pd.Period(latest, freq="M").end_time.date()
partial = last_day < month_end

df_m = monthly.tail(months_to_show).copy()
df_m[TEMP_COL] = temp_m.reindex(df_m.index)

# --- KPI cards
label = f"{latest} (partial, data through {last_day})" if partial else latest
st.subheader(f"Latest month: {label}")
row = monthly.loc[latest]
k = st.columns(6)
k[0].metric("Total", f"{row[COUNTRY_COLS].sum():.1f} TWh")
for i, c in enumerate(COUNTRY_COLS, start=1):
    k[i].metric(c, f"{row[c]:.1f} TWh")
t_latest = temp_m.get(latest)
k[5].metric("Mean temp (Helsinki-Vantaa)",
            f"{t_latest:.1f} °C" if pd.notna(t_latest) else "n/a")

st.markdown("---")

# --- Monthly chart with temperature on the secondary axis
st.subheader("Monthly consumption per country (TWh) and temperature (°C)")
fig = make_subplots(specs=[[{"secondary_y": True}]])
for c in COUNTRY_COLS:
    fig.add_trace(
        go.Bar(x=df_m.index, y=df_m[c], name=c, marker_color=COLORS[c],
               hovertemplate="%{y:.1f} TWh"),
        secondary_y=False,
    )
if df_m[TEMP_COL].notna().any():
    fig.add_trace(
        go.Scatter(x=df_m.index, y=df_m[TEMP_COL], name="Helsinki-Vantaa mean temp",
                   mode="lines+markers", line=dict(color=TEMP_COLOR, width=2),
                   marker=dict(size=6), hovertemplate="%{y:.1f} °C"),
        secondary_y=True,
    )
fig.update_layout(
    barmode="stack" if bar_mode == "Stacked" else "group",
    template="plotly_white", height=540, hovermode="x unified",
    xaxis_tickangle=-45, legend_title_text="",
    legend=dict(orientation="h", y=1.08, x=0),
)
fig.update_yaxes(title_text="Consumption (TWh / month)", tickformat=".1f",
                 secondary_y=False)
# Temperature axis: ticks every 5 °C, range always includes 0 °C
temp_axis = dict(tick0=0, dtick=5)
if df_m[TEMP_COL].notna().any():
    t_lo = min(0, math.floor(df_m[TEMP_COL].min() / 5) * 5)
    t_hi = max(0, math.ceil(df_m[TEMP_COL].max() / 5) * 5)
    temp_axis["range"] = [t_lo, t_hi]
fig.update_yaxes(title_text="Mean temperature (°C)", tickformat=".0f",
                 showgrid=False, zeroline=False, secondary_y=True, **temp_axis)
st.plotly_chart(fig, use_container_width=True)

if (df_m["Lithuania"] < 0).any() or (df_m["Finland"] < 0).any():
    st.caption(
        "Note: Finland and Lithuania are calculated as balances, so a single "
        "month can come out low or even negative because of linepack changes."
    )

st.subheader("Monthly table (TWh)")
table_m = df_m.copy()
table_m.insert(len(COUNTRY_COLS), "Total", table_m[COUNTRY_COLS].sum(axis=1))
st.dataframe(table_m.style.format("{:.1f}", na_rep="n/a"), use_container_width=True)
st.download_button(
    "Download monthly CSV 📥",
    table_m.round(1).to_csv().encode("utf-8"),
    file_name=f"finbalt_gas_consumption_monthly_{latest}.csv",
    mime="text/csv",
)

st.markdown("---")

# --- Yearly chart
st.subheader("Yearly consumption per country (TWh)")
yearly = monthly.copy()
yearly["Year"] = yearly.index.str[:4]
y = yearly.groupby("Year")[COUNTRY_COLS].sum()


def year_label(yr):
    ytd = int(yr) == last_day.year and last_day < date(last_day.year, 12, 31)
    return f"{yr} YTD" if ytd else yr


y.index = [year_label(i) for i in y.index]
y_total = y.sum(axis=1)

fig_y = go.Figure()
for c in COUNTRY_COLS:
    fig_y.add_trace(go.Bar(x=y.index, y=y[c], name=c, marker_color=COLORS[c],
                           texttemplate="%{y:.1f}", textposition="inside",
                           hovertemplate="%{y:.1f} TWh"))
fig_y.add_trace(go.Scatter(x=y.index, y=y_total, mode="text",
                           text=[f"{v:.1f}" for v in y_total],
                           textposition="top center", showlegend=False,
                           hoverinfo="skip"))
fig_y.update_layout(barmode="stack", template="plotly_white", height=460,
                    legend_title_text="", hovermode="x unified",
                    legend=dict(orientation="h", y=1.08, x=0))
fig_y.update_yaxes(title_text="Consumption (TWh / year)", tickformat=".1f")
fig_y.update_xaxes(type="category")
st.plotly_chart(fig_y, use_container_width=True)

if any("YTD" in str(i) for i in y.index):
    st.caption(f"YTD = from 1 January to {last_day} (latest ENTSOG data).")

table_y = y.copy()
table_y.insert(len(COUNTRY_COLS), "Total", y_total)
st.dataframe(table_y.style.format("{:.1f}"), use_container_width=True)
st.download_button(
    "Download yearly CSV 📥",
    table_y.round(1).to_csv().encode("utf-8"),
    file_name=f"finbalt_gas_consumption_yearly_{latest}.csv",
    mime="text/csv",
)

# --- Method and data quality
with st.expander("Method and caveats"):
    st.markdown(
        """
**Gas:** ENTSOG Transparency Platform, indicator *Physical Flow*, daily values
(kWh/d), summed per month and converted to TWh.

- **Estonia, Latvia:** ENTSOG aggregated *Final consumers* exit points
  (`FNC-00037`, `FNC-00205`). Read directly, no calculation.
- **Finland:** no consumption point is reported, so it is a balance:
  Balticconnector (EE→FI) + Hamina LNG + Inkoo LNG − Balticconnector (FI→EE).
  Imatra (Russia) is excluded because the border is closed.
- **Lithuania:** no consumption point is reported, so it is a balance:
  Klaipėda LNG + Kiemenai (LV→LT) + Santaka (PL→LT) + Kotlovka − Kiemenai (LT→LV)
  − Santaka (LT→PL) − Sakiai (LT→RU). Sakiai is Kaliningrad transit and is
  subtracted, not counted as Lithuanian consumption.
- Balances ignore linepack changes, losses and domestic biogas, so individual months
  are approximate.
- The latest month and the current year are incomplete.

**Temperature:** Finnish Meteorological Institute open data, Helsinki-Vantaa airport
station (fmisid 100968), daily mean temperature (`tday`), averaged per month.
The temperature is for Helsinki only and is shown as an indicator of the weather,
not as the temperature of the whole region.
        """
    )

with st.expander("Data quality"):
    st.write(f"Duplicate daily records dropped: {dupes}")
    st.write(f"Temperature days loaded: {len(temps)}")
    sub = raw.copy()
    sub["Month"] = sub["day"].str[:7]
    sub = sub[sub["Month"].isin(monthly.tail(months_to_show).index)]
    st.markdown("**Days of data per month and series**")
    st.dataframe(sub.groupby(["Month", "series"]).size().unstack("series").fillna(0).astype(int),
                 use_container_width=True)
    st.markdown("**Monthly TWh per series**")
    st.dataframe(sub.pivot_table(index="Month", columns="series", values="TWh",
                                 aggfunc="sum").fillna(0).style.format("{:.3f}"),
                 use_container_width=True)
