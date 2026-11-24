# -*- coding: utf-8 -*-
"""處置股策略研究(離線版):讀 backtest/cache 的股價、法人資料,做參數掃描與樣本外檢驗
三個時期分開看,避免「挑參數挑到過去剛好賺錢」:
  IS  = 2024-01 ~ 2025-12(用來挑參數)
  OOS = 2026-01 ~ 2026-08-07(舊制,沒參與挑參數)
  NEW = 2026-08-10 之後(新制:處置 5 天)
"""
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
NEW_FROM = pd.Timestamp("2026-08-10")
FEE = 0.001425 * 0.28          # 國泰 2.8 折
COST_SPOT = 2 * FEE + 0.003     # 現股一買一賣
COST_SSF = 0.0006               # 小型個股期來回粗估(期交稅+手續費+1檔滑價另計)
SLIP = 0.002                    # 處置股分盤撮合,進出各假設 0.1% 滑價

# ---------------------------------------------------------------- 資料
ev = pd.read_csv(HERE / "results/events.csv", dtype={"code": str},
                 parse_dates=["start", "end", "announced", "upgrade_start"])
px_all = pd.read_csv(HERE / "cache/prices.csv.gz", dtype={"code": str}, parse_dates=["date"])
PX = {c: g.set_index("date")[["Open", "High", "Low", "Close", "Volume"]].sort_index()
      for c, g in px_all.groupby("code")}
TW = PX.pop("TWII")
TW["ma20"] = TW.Close.rolling(20).mean()
CAL = TW.index
ssf_rows = json.loads((HERE / "cache/ssf_contracts.json").read_text(encoding="utf-8"))
_c = defaultdict(list)
for r in ssf_rows:
    _c[str(r.get("StockCode", "")).strip()].append(r.get("Contract"))
SSF = set(_c)
SMALL = {k for k, v in _c.items() if len(v) > 1}

inst = []
for f, m in (("inst_twse.csv.gz", "上市"), ("inst_tpex.csv.gz", "上櫃")):
    d = pd.read_csv(HERE / "cache" / f, dtype={"code": str}, parse_dates=["date"])
    inst.append(d)
INST = pd.concat(inst).drop_duplicates(["date", "code"]).set_index(["code", "date"]).sort_index()


def inst_sum(code, days, col):
    try:
        s = INST.loc[code]
    except KeyError:
        return 0.0
    return float(s.reindex(days)[col].fillna(0).sum())


# 累犯:開始日前 60 個交易日內有出過關
ev = ev.sort_values(["code", "start"]).reset_index(drop=True)
prev_end = ev.groupby("code")["end"].shift(1)
ev["recid"] = [(pe is not pd.NaT and not pd.isna(pe) and ((CAL > pe) & (CAL < s)).sum() <= 60)
               for pe, s in zip(prev_end, ev.start)]
ev["ssf"] = ev.code.isin(SSF)
ev["small"] = ev.code.isin(SMALL)


def period(t):
    t = pd.Timestamp(t)
    if t >= NEW_FROM:
        return "NEW"
    return "OOS" if t.year >= 2026 else "IS"


# ---------------------------------------------------------------- 每段處置的K棒
def episode(ev_row):
    d = PX.get(ev_row.code)
    if d is None:
        return None
    idx = d.index
    s, e = ev_row.start, ev_row.end
    days = idx[(idx >= s) & (idx <= e)]
    after, before = idx[idx > e], idx[idx < s]
    if len(days) < 2 or len(after) < 4 or len(before) < 25:
        return None
    ma = d.Close.rolling(20).mean()
    return dict(d=d, idx=idx, ma=ma, days=days, pre=before[-1], rel=after[0], post=after[:4],
                first=days[0], last=days[-1])


EP = {}
for r in ev.itertuples():
    x = episode(r)
    if x:
        EP[r.Index] = x
print("可用處置段", len(EP))


def base_feat(r, x, t):
    """t 這天收盤時就知道的資訊"""
    d, ma, idx = x["d"], x["ma"], x["idx"]
    p = idx.get_loc(t)
    pre = x["pre"]
    up = r.upgrade_start
    w5 = idx[max(0, p - 4):p + 1]
    tw_ok = t in TW.index and TW.Close[t] > TW.ma20[t]
    return dict(
        second=(not pd.isna(up)) and up <= t,
        recid=r.recid, ssf=r.ssf, small=r.small, market=r.market, regime=period(t),
        bias_pre=d.Close[pre] / ma[pre] - 1,
        chg_in=d.Close[t] / d.Close[pre] - 1,
        vol_ratio=d.Volume.loc[x["first"]:t].mean() / d.Volume.iloc[max(0, idx.get_loc(pre) - 4):idx.get_loc(pre) + 1].mean(),
        tw_ok=tw_ok,
        inst5=inst_sum(r.code, w5, "tot"), it5=inst_sum(r.code, w5, "it"),
        dist_ma=d.Close[t] / ma[t] - 1,
    )


def mk(strat, r, x, side, t_in, p_in, t_out, p_out, reason, kind, feat):
    g = (p_out / p_in - 1) if side == "long" else (p_in / p_out - 1)
    cost = (COST_SPOT if kind == "spot" else (2 * FEE + 0.0015 if kind == "dt" else COST_SSF)) + SLIP
    return dict(strategy=strat, code=r.code, name=r.name, start=r.start, entry_date=t_in, exit_date=t_out,
                entry=p_in, exit=p_out, reason=reason, gross=g, net=g - cost,
                period=period(t_in), **feat)


# ---------------------------------------------------------------- 做法一(參數化)
def run_m1(tp=0.10, sl=0.95, pat="both", entry="next_open", exit_="rel_open", trail=None):
    out = []
    for i, x in EP.items():
        r = ev.loc[i]
        d, ma, idx = x["d"], x["ma"], x["idx"]
        O, H, L, C = d.Open, d.High, d.Low, d.Close
        pre, rel = x["pre"], x["rel"]
        if not C[pre] > ma[pre]:
            continue
        for t in x["days"]:
            p = idx.get_loc(t)
            if p < 5 or np.isnan(ma.iloc[p - 5]) or not ma.iloc[p] > ma.iloc[p - 5]:
                continue
            hi5 = C.iloc[p - 5:p].max()
            lz = L[t] <= ma[t] * 1.02 and C[t] >= ma[t] * 0.98
            sq = C[t] / hi5 - 1 <= -0.10 and C[t] >= ma[t] * 0.95
            ok = {"both": lz or sq, "langzi": lz, "squat": sq}[pat]
            if not ok:
                continue
            nxt = idx[p + 1]
            if nxt >= rel:
                break
            t_in, p_in = (nxt, O[nxt]) if entry == "next_open" else (t, C[t])
            tgt = p_in * (1 + tp) if tp else np.inf
            done, peak = None, p_in
            for u in idx[(idx >= t_in) & (idx <= rel)]:
                first_bar = (u == t_in)
                if u == rel:
                    if O[u] >= tgt:
                        done = (u, O[u], "出關開盤達標")
                    elif exit_ == "rel_open":
                        done = (u, O[u], "出關開盤")
                    else:
                        if H[u] >= tgt:
                            done = (u, tgt, "出關日盤中達標")
                        else:
                            done = (u, C[u], "出關收盤")
                    break
                if not first_bar and O[u] >= tgt:
                    done = (u, O[u], "跳空達標"); break
                if H[u] >= tgt and not (first_bar and entry == "close"):
                    done = (u, tgt, "盤中達標"); break
                if sl and C[u] < ma[u] * sl:
                    done = (u, C[u], "停損"); break
                peak = max(peak, C[u])
                if trail and C[u] < peak * (1 - trail):
                    done = (u, C[u], "移動停利"); break
            if done:
                f = base_feat(r, x, t)
                f["pattern"] = "浪子回頭" if lz else "深蹲蓄力"
                out.append(mk("M1", r, x, "long", t_in, p_in, done[0], done[1], done[2], "spot", f))
            break
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 做法二:出關前一天尾盤買
def run_m2(exit_="rel_open"):
    out = []
    for i, x in EP.items():
        r = ev.loc[i]
        d = x["d"]
        last, rel = x["last"], x["rel"]
        f = base_feat(r, x, last)
        p_out = d.Open[rel] if exit_ == "rel_open" else d.Close[rel]
        out.append(mk("M2", r, x, "long", last, d.Close[last], rel, p_out, exit_, "spot", f))
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 做法三:出關日開盤空(小型個股期)
def run_m3():
    out = []
    for i, x in EP.items():
        r = ev.loc[i]
        d = x["d"]
        last, rel = x["last"], x["rel"]
        f = base_feat(r, x, last)
        f["gap"] = d.Open[rel] / d.Close[last] - 1
        out.append(mk("M3", r, x, "short", rel, d.Open[rel], rel, d.Close[rel], "當天回補", "ssf", f))
    return pd.DataFrame(out)


# ---------------------------------------------------------------- 處置統計(小哥 App 的三大指標)
def disposal_stats():
    rows = []
    for i, x in EP.items():
        r = ev.loc[i]
        d = x["d"]
        seq = [("處置前一天", x["pre"])] + [(f"第{k+1}天", t) for k, t in enumerate(x["days"])] + \
              [("出關日", x["rel"])] + [(f"出關後第{k}天", t) for k, t in enumerate(x["post"][1:4], 1)]
        n = len(x["days"])
        for k, (lab, t) in enumerate(seq):
            p = d.index.get_loc(t)
            pc = d.Close.iloc[p - 1]
            lab2 = lab
            if lab.startswith("第") and lab.endswith("天") and "出關" not in lab:
                back = n - int(lab[1:-1])
                lab2 = "處置最後一天" if back == 0 else (f"出關前{back+1}天" if back <= 2 else lab)
            rows.append(dict(regime="新制(5天)" if r.start >= NEW_FROM else "舊制(10天)",
                             level="第二次(20分)" if str(r.level).startswith("第二次") else "第一次/其他",
                             recid="累犯" if r.recid else "初犯", ssf=r.ssf, market=r.market,
                             day=lab2, chg=d.Close[t] / pc - 1, red=d.Close[t] > d.Open[t],
                             oc=d.Close[t] / d.Open[t] - 1, gap=d.Open[t] / pc - 1))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 統計
def st(r):
    r = r.dropna()
    if len(r) == 0:
        return pd.Series({"n": 0})
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    return pd.Series({"n": len(r), "win%": round((r > 0).mean() * 100, 1), "avg%": round(r.mean() * 100, 2),
                      "med%": round(r.median() * 100, 2), "PF": round(w / l, 2) if l > 0 else np.nan,
                      "worst%": round(r.min() * 100, 1)})


def by_period(df, col="net"):
    if df is None or len(df) == 0:
        return pd.DataFrame()
    a = df.groupby("period")[col].apply(st).unstack()
    a.loc["ALL"] = st(df[col])
    return a.reindex(["IS", "OOS", "NEW", "ALL"])


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    m1 = run_m1()
    print(by_period(m1))
