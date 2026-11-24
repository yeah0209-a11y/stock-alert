# -*- coding: utf-8 -*-
"""出關日、出關後第1天的 60 分K(yfinance 最多約 730 天),用來回測做法三幾點回補最好"""
import time
from pathlib import Path
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
ev = pd.read_csv(HERE / "results" / "events.csv", dtype={"code": str}, parse_dates=["start", "end"])
px = pd.read_csv(HERE / "cache" / "prices.csv.gz", dtype={"code": str}, parse_dates=["date"])
cal = pd.DatetimeIndex(sorted(px[px.code == "TWII"].date.unique()))
cut = pd.Timestamp.today() - pd.Timedelta(days=720)
need = {}
for r in ev.itertuples():
    after = cal[cal > r.end]
    if len(after) < 2 or after[0] < cut:
        continue
    sym = f"{r.code}.TW" if r.market == "上市" else f"{r.code}.TWO"
    need.setdefault(sym, set()).update([after[0].date(), after[1].date()])
rows = []
for i, (sym, days) in enumerate(need.items()):
    try:
        d = yf.download(sym, period="730d", interval="60m", auto_adjust=False, progress=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        d.index = pd.to_datetime(d.index).tz_convert("Asia/Taipei").tz_localize(None)
        d = d[[x.date() in days for x in d.index]][["Open", "High", "Low", "Close", "Volume"]]
        d = d.reset_index().rename(columns={"index": "time", "Datetime": "time"})
        d.insert(0, "code", sym.split(".")[0])
        rows.append(d)
    except Exception as e:  # noqa: BLE001
        print("fail", sym, e)
    if i % 50 == 0:
        print(i, len(need), flush=True)
    time.sleep(0.3)
out = pd.concat(rows, ignore_index=True)
out.to_csv(HERE / "cache" / "hourly_release.csv.gz", index=False, compression="gzip")
print("rows", len(out), "codes", out.code.nunique())
