# -*- coding: utf-8 -*-
"""把回測要用的原始資料存進 backtest/cache,之後的研究可以離線重跑
- prices.csv.gz : 所有處置過的股票 + 加權指數(^TWII) 日K(未還原)
- ssf_contracts.json : 期交所個股期清單(含小型個股期)
"""
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)
ev = pd.read_csv(HERE / "results" / "events.csv", dtype={"code": str})
a = date(2023, 6, 1)
b = date.today() + timedelta(days=2)

tick = {c: (f"{c}.TW" if m == "上市" else f"{c}.TWO")
        for c, m in ev[["code", "market"]].drop_duplicates().itertuples(index=False)}
tick["TWII"] = "^TWII"
rows = []
lst = list(tick.items())
for i in range(0, len(lst), 40):
    chunk = lst[i:i + 40]
    raw = yf.download([s for _, s in chunk], start=str(a), end=str(b), group_by="ticker",
                      auto_adjust=False, progress=False, threads=True)
    for code, s in chunk:
        try:
            d = raw[s][["Open", "High", "Low", "Close", "Volume"]].dropna()
            d = d[(d["Volume"] > 0) | (code == "TWII")]
            d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
            d = d.reset_index().rename(columns={"index": "date", "Date": "date"})
            d.insert(0, "code", code)
            rows.append(d)
        except Exception as e:  # noqa: BLE001
            print("fail", code, e)
    print(f"{min(i + 40, len(lst))}/{len(lst)}", flush=True)
    time.sleep(1)
px = pd.concat(rows, ignore_index=True)
px.to_csv(CACHE / "prices.csv.gz", index=False, compression="gzip")
print("prices rows", len(px), "codes", px.code.nunique())

try:
    js = requests.get("https://openapi.taifex.com.tw/v1/SSFLists", timeout=30,
                      headers={"User-Agent": "Mozilla/5.0"}).json()
    (CACHE / "ssf_contracts.json").write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
    print("ssf rows", len(js))
except Exception as e:  # noqa: BLE001
    print("ssf fail", e)
