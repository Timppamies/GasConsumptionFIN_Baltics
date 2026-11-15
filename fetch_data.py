"""Fetch all ENTSOG series of this app into data/entsog_daily.csv and the
Helsinki-Vantaa daily temperature (FMI) into data/fmi_temperature.csv.

Run every evening by GitHub Actions (.github/workflows/update-data.yml), so the
app can read the data from the repository instead of waiting for the API.
The whole period is fetched every time, so ENTSOG's revisions are picked up too.

Usage:  python fetch_data.py
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from entsog import fetch_series, fetch_start, series_keys
from fmi import fetch_temperature

DATA_DIR = Path(__file__).parent / "data"
OUT = DATA_DIR / "entsog_daily.csv"
META = DATA_DIR / "entsog_meta.json"
TEMP_OUT = DATA_DIR / "fmi_temperature.csv"
# Refuse to overwrite if the new file is much shorter than the old one
# (a sign of an incomplete API response).
MIN_SHARE_OF_OLD = 0.95


def main():
    today = date.today()
    start = fetch_start(today)
    rows, dupes, failed = [], 0, []
    for op, pt, d in series_keys():
        try:
            r = fetch_series(op, pt, d, start.isoformat(), today.isoformat())
        except Exception as e:
            failed.append(f"{op} {pt} {d}: {e}")
            continue
        dupes += r["dupes"]
        rows += [(day, op, pt, d, v) for day, v in r["days"].items()]
        print(f"{op} {pt} {d}: {len(r['days'])} days")
    if failed:
        sys.exit("Failed series, keeping the old file:\n" + "\n".join(failed))

    df = pd.DataFrame(rows, columns=["day", "operator", "point", "direction", "kwh"])
    df = df.sort_values(["operator", "point", "direction", "day"])
    if df.empty:
        sys.exit("ENTSOG returned no data; keeping the old file.")
    if OUT.exists():
        old = pd.read_csv(OUT)
        if len(df) < MIN_SHARE_OF_OLD * len(old):
            sys.exit(f"Only {len(df)} rows fetched vs {len(old)} stored; "
                     "keeping the old file.")

    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(OUT, index=False)
    META.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "start": start.isoformat(),
        "dupes": dupes,
    }, indent=2) + "\n")
    print(f"Wrote {len(df)} rows ({df['day'].min()} - {df['day'].max()}) to {OUT}")

    # Temperature is optional for the app: if FMI fails, keep the old file.
    try:
        temps = fetch_temperature(start.isoformat(), today.isoformat())
    except Exception as e:
        print(f"WARNING: FMI temperature could not be fetched ({e}); keeping the old file.")
        return
    t = pd.DataFrame(sorted(temps.items()), columns=["day", "tday"])
    if TEMP_OUT.exists() and len(t) < MIN_SHARE_OF_OLD * len(pd.read_csv(TEMP_OUT)):
        print("WARNING: FMI returned fewer days than stored; keeping the old file.")
        return
    t.to_csv(TEMP_OUT, index=False)
    print(f"Wrote {len(t)} temperature days to {TEMP_OUT}")


if __name__ == "__main__":
    main()
