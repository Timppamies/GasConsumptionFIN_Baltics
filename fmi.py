"""FMI open data: daily mean temperature at Helsinki-Vantaa.

Shared by the app and the daily fetch script; no Streamlit imports here.
"""
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

from entsog import chunks

FMI_URL = "https://opendata.fmi.fi/wfs"
FMI_FMISID = "100968"  # Helsinki-Vantaa airport weather station


def fetch_temperature(start_str, end_str):
    """Daily mean temperature (tday, deg C) at Helsinki-Vantaa: {day: deg C}.
    Raises if any request fails."""
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
        parts = list(ex.map(one, list(chunks(start, end, 31))))

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
