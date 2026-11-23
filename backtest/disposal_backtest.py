# -*- coding: utf-8 -*-
"""
處置股回測 — 用「權證小哥公開談過的處置股規則」回測
=====================================================
規則來源(推測整理,不是小哥官方程式):
  * 鏡週刊 2026/04/30〈處置股越關越大尾? 權證小哥揭「人棄我取」進場術〉
      - 上升月線是定海神針;強勢股回檔到上升月線後反彈(浪子回頭 → 東山再起)
      - 主力倒貨、籌碼轉差的不碰
      - 關禁閉期間已有約 10% 獲利,出關當天開高先落袋
      - 出關日常有獲利了結賣壓,多半開高走低,近期常「開小高」就下殺
  * 處置神器 App 介紹:「浪子回頭」(快速回檔月線或橫盤)、「深蹲蓄力」(短線大跌超過一根跌停)
  * 直播常說的口訣:「處置前找空點、出關前找買點」

★ 籌碼條件(5 日/10 日籌碼集中度、關鍵分點)需要分點資料,這裡拿不到,
  所以用「收盤守住月線」當作「主力沒有倒貨」的替代條件。

執行:python backtest/disposal_backtest.py
輸出:backtest/results/ 底下的 summary.md、summary.csv、trades.csv、events.csv
"""

# ★★★★★★★★★★★★★★★★★★★★ 設定區 ★★★★★★★★★★★★★★★★★★★★
START = "2024-01-01"      # 回測處置事件的起始日
END = None                # None = 到今天
TARGET = 0.10             # 小哥說的 10% 停利
MA_N = 20                 # 月線 = 20 日均線
# 交易成本(單邊/來回),現股:手續費 0.1425% 打 6 折,證交稅賣出 0.3%,當沖 0.15%
FEE = 0.001425 * 0.6
TAX = 0.003
TAX_DT = 0.0015
BORROW = 0.0008           # 融券/借券費用粗估
SSF_COST = 0.0005         # 個股期來回成本粗估(期交稅十萬分之二 ×2 + 手續費)
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★

import json
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (personal backtest)"}
ROC = re.compile(r"(\d{2,3})\s*[/年.-]\s*(\d{1,2})\s*[/月.-]\s*(\d{1,2})")


def log(*a):
    print(*a, flush=True)


def roc_dates(text):
    out = []
    for y, m, d in ROC.findall(str(text or "")):
        y = int(y)
        y = y + 1911 if y < 1911 else y
        out.append(date(y, int(m), int(d)))
    return out


def clean(text):
    text = re.sub(r"<.*?>", "", str(text or ""))
    text = re.sub(r"\((?:\.\./|/|http)[^)]*\)", "", text)
    return text.strip()


def get(url, params, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            log(f"  retry {i+1} {url} {params}: {e}")
            time.sleep(5 * (i + 1))
    return None


def tables_of(js):
    """證交所/櫃買的 JSON 有兩種格式:{fields,data} 或 {tables:[{fields,data}]}"""
    if not isinstance(js, dict):
        return []
    if js.get("tables"):
        return js["tables"]
    if js.get("fields") and js.get("data") is not None:
        return [js]
    return []


def parse_rows(js, market):
    out = []
    for t in tables_of(js):
        fields = [str(f) for f in (t.get("fields") or [])]

        def idx(*keys):
            for i, f in enumerate(fields):
                if any(k in f for k in keys):
                    return i
            return None

        ic, iname = idx("證券代號", "代號"), idx("證券名稱", "名稱")
        iper, imeas = idx("處置起訖", "處置起迄", "期間"), idx("處置措施")
        icont, iann = idx("處置內容"), idx("公布日期")
        if ic is None or iper is None:
            continue
        for row in t.get("data") or []:
            cell = lambda i: clean(row[i]) if i is not None and i < len(row) else ""  # noqa: E731
            code = cell(ic)
            if not re.fullmatch(r"\d{4}", code):
                continue
            ds = roc_dates(cell(iper))
            if len(ds) < 2:
                continue
            meas = cell(imeas) + " " + cell(icont)
            if "第二次" in meas or "二十分鐘" in meas or "20分鐘" in meas:
                level = "第二次(20分盤)"
            elif "第一次" in meas or "五分鐘" in meas or "5分鐘" in meas:
                level = "第一次(5分盤)"
            else:
                level = "其他"
            ann = roc_dates(cell(iann))
            out.append({"code": code, "name": cell(iname), "market": market,
                        "start": ds[0], "end": ds[1], "level": level,
                        "announced": ann[0] if ann else None})
    return out


def month_ranges(a: date, b: date):
    cur = date(a.year, a.month, 1)
    while cur <= b:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        yield max(cur, a), min(nxt - timedelta(days=1), b)
        cur = nxt


def fetch_events(a: date, b: date):
    ev = []
    for s, e in month_ranges(a, b):
        js = get("https://www.twse.com.tw/rwd/zh/announcement/punish",
                 {"startDate": f"{s:%Y%m%d}", "endDate": f"{e:%Y%m%d}", "response": "json"})
        r1 = parse_rows(js, "上市")
        time.sleep(3)
        js = get("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal",
                 {"startDate": f"{s:%Y/%m/%d}", "endDate": f"{e:%Y/%m/%d}", "response": "json"})
        r2 = parse_rows(js, "上櫃")
        time.sleep(3)
        log(f"{s:%Y-%m}: 上市 {len(r1)} 筆, 上櫃 {len(r2)} 筆")
        ev += r1 + r2
    df = pd.DataFrame(ev)
    if df.empty:
        return df
    df = df.drop_duplicates(["code", "start"]).sort_values(["code", "start"]).reset_index(drop=True)
    # 合併重疊的處置:例如第一次(5分盤)還沒結束就被加碼成第二次(20分盤),
    # 這時第一次的「出關日」其實沒有出關,要合併成同一段處置期間
    merged = []
    for r in df.to_dict("records"):
        if merged and merged[-1]["code"] == r["code"] and r["start"] <= merged[-1]["end"]:
            m = merged[-1]
            m["end"] = max(m["end"], r["end"])
            m["n_merged"] += 1
            if r["level"].startswith("第二次"):
                m["level"] = r["level"]
        else:
            r["n_merged"] = 1
            merged.append(r)
    df = pd.DataFrame(merged).sort_values("start").reset_index(drop=True)
    log(f"合併重疊後處置期間 {len(df)} 段")
    return df


def ssf_codes():
    try:
        rows = requests.get("https://openapi.taifex.com.tw/v1/SSFLists", headers=UA, timeout=30).json()
        codes = set()
        for r in rows:
            for k, v in r.items():
                if any(t in k for t in ("證券代號", "StockCode", "UnderlyingCode", "標的證券代號")):
                    v = str(v).strip()
                    if re.fullmatch(r"\d{4}", v):
                        codes.add(v)
        return codes
    except Exception as e:  # noqa: BLE001
        log("SSF list failed", e)
        return set()


def fetch_prices(events, a: date, b: date):
    import yfinance as yf
    tick = {}
    for code, mkt in events[["code", "market"]].drop_duplicates().itertuples(index=False):
        tick[code] = f"{code}.TW" if mkt == "上市" else f"{code}.TWO"
    prices = {}
    lst = list(tick.items())
    for i in range(0, len(lst), 40):
        chunk = lst[i:i + 40]
        syms = [s for _, s in chunk]
        try:
            raw = yf.download(syms, start=str(a - timedelta(days=150)), end=str(b + timedelta(days=10)),
                              group_by="ticker", auto_adjust=False, progress=False, threads=True)
        except Exception as e:  # noqa: BLE001
            log("yf fail", e)
            continue
        for code, s in chunk:
            try:
                d = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
                d = d[["Open", "High", "Low", "Close", "Volume"]].dropna()
                d = d[d["Volume"] > 0]
                if len(d) > 30:
                    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
                    prices[code] = d
            except Exception:  # noqa: BLE001
                pass
        log(f"prices {min(i+40, len(lst))}/{len(lst)}")
        time.sleep(1)
    return prices


# ======================================================================
# 策略
# ======================================================================
def trade(strategy, ev, side, entry_date, entry, exit_date, exit_, reason, cost_kind):
    g = (exit_ / entry - 1) if side == "long" else (entry / exit_ - 1)
    if cost_kind == "long":
        c = 2 * FEE + TAX
    elif cost_kind == "daytrade":
        c = 2 * FEE + TAX_DT
    else:  # short multi-day
        c = 2 * FEE + TAX + BORROW
    return {"strategy": strategy, "code": ev["code"], "name": ev["name"], "market": ev["market"],
            "level": ev["level"], "ssf": ev["ssf"], "start": ev["start"], "end": ev["end"],
            "side": side, "entry_date": entry_date.date(), "entry": round(entry, 2),
            "exit_date": exit_date.date(), "exit": round(exit_, 2), "reason": reason,
            "gross": g, "net_spot": g - c, "net_ssf": g - SSF_COST}


def run_event(ev, d):
    out = []
    ma = d["Close"].rolling(MA_N).mean()
    idx = d.index
    s, e = pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"])
    days = idx[(idx >= s) & (idx <= e)]
    after = idx[idx > e]
    before = idx[idx < s]
    if len(days) < 3 or len(after) == 0 or len(before) < 25:
        return out, None
    rel, pre = after[0], before[-1]
    first, last = days[0], days[-1]
    O, H, L, C = d["Open"], d["High"], d["Low"], d["Close"]

    def pos(t):
        return idx.get_loc(t)

    def ma_up(t):
        p = pos(t)
        return p >= 5 and not np.isnan(ma.iloc[p - 5]) and ma.iloc[p] > ma.iloc[p - 5]

    info = {"code": ev["code"], "start": ev["start"], "level": ev["level"], "market": ev["market"],
            "bias_pre": C[pre] / ma[pre] - 1, "ma_up_pre": ma_up(pre),
            "chg_in_disposal": C[last] / C[pre] - 1, "rel_gap": O[rel] / C[last] - 1,
            "rel_open_to_close": C[rel] / O[rel] - 1}

    # ---------- A0 基準:每檔都做「出關前買」:最後一天收盤買、出關日開盤賣
    out.append(trade("A0 基準:每檔出關前買(末日收→出關開)", ev, "long", last, C[last], rel, O[rel], "出關開盤", "long"))
    # ---------- A1 基準:處置第一天開盤買,抱到出關日開盤
    out.append(trade("A1 基準:每檔處置首日買抱到出關", ev, "long", first, O[first], rel, O[rel], "出關開盤", "long"))

    # ---------- B 小哥版:出關前找買點(上升月線+浪子回頭/深蹲,守月線,10%停利,出關開盤走)
    if C[pre] > ma[pre]:  # 處置前是強勢股(站上月線)
        for t in days:
            p = pos(t)
            if np.isnan(ma[t]) or not ma_up(t):
                continue
            hi5 = C.iloc[max(0, p - 5):p].max()
            langzi = L[t] <= ma[t] * 1.02 and C[t] >= ma[t] * 0.98          # 浪子回頭:碰月線守住
            squat = C[t] / hi5 - 1 <= -0.10 and C[t] >= ma[t] * 0.95       # 深蹲蓄力:短線跌超過一根跌停
            if not (langzi or squat):
                continue
            entry, tag = C[t], ("浪子回頭" if langzi else "深蹲蓄力")
            tgt = entry * (1 + TARGET)
            path = idx[(idx > t) & (idx <= rel)]
            done = None
            for u in path:
                if u == rel:
                    done = (u, O[u], "出關開盤" if O[u] < tgt else "出關開盤(達10%)")
                    break
                if O[u] >= tgt:
                    done = (u, O[u], "跳空達10%")
                    break
                if H[u] >= tgt:
                    done = (u, tgt, "盤中達10%")
                    break
                if C[u] < ma[u] * 0.95:
                    done = (u, C[u], "跌破月線5%停損")
                    break
            if done:
                out.append(trade(f"B 小哥版:出關前買({'浪子回頭/深蹲'})", ev, "long", t, entry, done[0], done[1],
                                 tag + "→" + done[2], "long"))
            break  # 每次處置只做一次

    # ---------- C0 基準:出關日當沖空(開盤空、收盤補)
    out.append(trade("C0 基準:每檔出關日當沖空", ev, "short", rel, O[rel], rel, C[rel], "收盤回補", "daytrade"))
    # ---------- C1 小哥版:關禁閉期間有漲 + 出關日「開小高」(0~5%) 才空
    gap = O[rel] / C[last] - 1
    if C[last] > C[pre] and 0 < gap <= 0.05:
        out.append(trade("C1 小哥版:有漲+出關開小高才當沖空", ev, "short", rel, O[rel], rel, C[rel],
                         "收盤回補", "daytrade"))

    # ---------- D0 基準:處置首日開盤空,第3天收盤補
    ex = days[min(2, len(days) - 1)]
    out.append(trade("D0 基準:每檔處置首日空(抱3天)", ev, "short", first, O[first], ex, C[ex], "第3天收盤", "short"))
    # ---------- D1 小哥版(處置前找空點):處置前乖離月線 >=15% 的過熱股才空,漲 7% 停損
    if C[pre] / ma[pre] - 1 >= 0.15:
        entry = O[first]
        done = None
        for u in days[:3]:
            if C[u] >= entry * 1.07:
                done = (u, C[u], "反彈7%停損")
                break
        if not done:
            done = (ex, C[ex], "第3天收盤")
        out.append(trade("D1 小哥版:過熱(乖離>=15%)才處置首日空", ev, "short", first, entry, done[0], done[1],
                         done[2], "short"))
    return out, info


def stats(g, col):
    r = g[col]
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    return pd.Series({
        "筆數": len(r), "勝率%": round((r > 0).mean() * 100, 1),
        "平均%": round(r.mean() * 100, 2), "中位數%": round(r.median() * 100, 2),
        "獲利因子": round(wins / losses, 2) if losses > 0 else np.nan,
        "最差%": round(r.min() * 100, 1), "最好%": round(r.max() * 100, 1),
    })


def main():
    b = date.fromisoformat(END) if END else date.today()
    a = date.fromisoformat(START)
    log(f"== 抓處置事件 {a} ~ {b}")
    events = fetch_events(a, b)
    if events.empty:
        raise SystemExit("沒有抓到任何處置事件")
    ssf = ssf_codes()
    events["ssf"] = events["code"].isin(ssf)
    log(f"處置事件 {len(events)} 筆,個股期標的 {events['ssf'].sum()} 筆")
    log("== 抓股價")
    prices = fetch_prices(events, a, b)
    trades, infos = [], []
    for ev in events.to_dict("records"):
        d = prices.get(ev["code"])
        if d is None:
            continue
        try:
            t, info = run_event(ev, d)
            trades += t
            if info:
                infos.append(info)
        except Exception as e:  # noqa: BLE001
            log("event fail", ev["code"], ev["start"], e)
    tr = pd.DataFrame(trades)
    events.to_csv(OUT / "events.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(infos).to_csv(OUT / "event_features.csv", index=False, encoding="utf-8-sig")
    tr.to_csv(OUT / "trades.csv", index=False, encoding="utf-8-sig")

    lines = [f"# 處置股回測結果({a} ~ {b})", "",
             f"- 處置事件:{len(events)} 筆(上市 {(events.market=='上市').sum()}、上櫃 {(events.market=='上櫃').sum()})",
             f"- 有股價可回測:{len(infos)} 筆", ""]
    summ = []
    for col, label in (("net_spot", "現股淨報酬(扣手續費+稅)"), ("net_ssf", "個股期淨報酬(粗估成本)"), ("gross", "毛報酬")):
        s = tr.groupby("strategy").apply(stats, col=col)
        s.insert(0, "口徑", label)
        summ.append(s)
        lines += [f"## {label}", "", s.drop(columns="口徑").to_markdown(), ""]
    pd.concat(summ).to_csv(OUT / "summary.csv", encoding="utf-8-sig")

    for key, title in (("level", "依處置等級"), ("ssf", "依是否有個股期"), ("market", "依上市/上櫃")):
        s = tr.groupby(["strategy", key]).apply(stats, col="net_spot")
        lines += [f"## {title}(現股淨報酬)", "", s.to_markdown(), ""]
    tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
    s = tr.groupby(["strategy", "year"]).apply(stats, col="net_spot")
    lines += ["## 依年份(現股淨報酬)", "", s.to_markdown(), ""]
    b_tr = tr[tr.strategy.str.startswith("B")]
    if len(b_tr):
        s = b_tr.groupby(b_tr["reason"].str.split("→").str[1]).apply(stats, col="net_spot")
        lines += ["## B 策略出場原因", "", s.to_markdown(), ""]
        s = b_tr.groupby(b_tr["reason"].str.split("→").str[0]).apply(stats, col="net_spot")
        lines += ["## B 策略進場型態", "", s.to_markdown(), ""]
    (OUT / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    log("\n".join(lines[:60]))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        (OUT / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        traceback.print_exc()
        sys.exit(1)
