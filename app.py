"""FinBalt Regional Gas Consumption: monthly and yearly consumption per country.

Data: ENTSOG Transparency Platform (gas), FMI open data (temperature).
Both are fetched every evening by GitHub Actions into data/ (fetch_data.py), so
the app normally reads them from the repository and calls the APIs only if the
files are missing or out of date.
Run with:  streamlit run app.py
"""
import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from entsog import HISTORY_START, SERIES
from fmi import fetch_temperature
from store import STALE_DAYS, TOPUP_OVERLAP_DAYS, fetch_live, load_flows, source_text

st.set_page_config(page_title="FinBalt Gas Consumption", page_icon="📈", layout="wide")

TEMP_FILE = Path(__file__).parent / "data" / "fmi_temperature.csv"

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


# ---------------------------------------------------------------- data load
def load_gas(start, end, force_live=False):
    """Stored data (updated every evening), topped up live from ENTSOG if needed."""
    flows, failed_keys, dupes, info = load_flows(start, end, force_live)
    failed = [f"Stored file: {failed_keys[None]}"] if None in failed_keys else []
    frames = []
    for grp, op, pt, d, label in SERIES:
        if (op, pt, d) in failed_keys:
            failed.append(f"{label} [{op} {pt} {d}]: {failed_keys[(op, pt, d)]}")
        days = flows.get((op, pt, d), {})
        if days:
            frames.append(pd.DataFrame({
                "day": list(days.keys()),
                "TWh": [v / 1e9 for v in days.values()],  # kWh/d -> TWh
                "group": grp,
                "series": f"{grp}: {label}",
            }))
    if frames:
        raw = pd.concat(frames, ignore_index=True)
    else:
        raw = pd.DataFrame(columns=["day", "TWh", "group", "series"])
    return raw, failed, dupes, info


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
@st.cache_data(show_spinner=False)
def read_stored_temperature(_path, mtime):
    """{day: deg C} from the stored file (mtime is the cache key)."""
    t = pd.read_csv(_path, dtype={"day": str})
    return dict(zip(t["day"], t["tday"]))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_temperature_live(start_str, end_str):
    return fetch_temperature(start_str, end_str)


def load_temperature(start, end, force_live=False):
    """Stored temperatures, topped up live from FMI if missing or out of date."""
    temps = {}
    if TEMP_FILE.exists():
        temps = read_stored_temperature(TEMP_FILE, TEMP_FILE.stat().st_mtime)
    last = date.fromisoformat(max(temps)) if temps else None
    if last is None or force_live or (end - last).days > STALE_DAYS:
        live_from = start if last is None else max(
            start, last - timedelta(days=TOPUP_OVERLAP_DAYS))
        temps = {**temps, **fetch_temperature_live(live_from.isoformat(), end.isoformat())}
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

if st.sidebar.button("Fetch latest from ENTSOG 🔄"):
    fetch_live.clear()
    fetch_temperature_live.clear()
    # Kept for the session, so the live data stays when other settings change.
    st.session_state["force_live"] = True
if st.sidebar.button("Clear cache"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(
    "Data is fetched from ENTSOG into the repository every evening, so the app "
    "opens without waiting for the API. ENTSOG publishes each gas day on the "
    "following day."
)
force_live = st.session_state.get("force_live", False)

today = date.today()
raw, failed, dupes, info = load_gas(HISTORY_START, today, force_live)

if failed:
    st.warning(
        "Some ENTSOG series could not be loaded, so the figures below may be "
        "incomplete. Press \"Fetch latest from ENTSOG\" to retry."
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
    temps = load_temperature(HISTORY_START, today, force_live)
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
- The whole period is re-fetched every evening, so ENTSOG's revisions to old
  days show up the next day.

**Temperature:** Finnish Meteorological Institute open data, Helsinki-Vantaa airport
station (fmisid 100968), daily mean temperature (`tday`), averaged per month.
The temperature is for Helsinki only and is shown as an indicator of the weather,
not as the temperature of the whole region.
        """
    )

with st.expander("Data quality"):
    st.write(f"Duplicate daily records dropped: {dupes}")
    st.write(f"Temperature days loaded: {len(temps)}")
    st.write(source_text(info))
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
