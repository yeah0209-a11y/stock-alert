# -*- coding: utf-8 -*-
"""產生 App 用的 docs/stats.json:處置統計(平均漲跌幅/紅K機率/開收差幅)+ 各做法回測數字"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research import *  # noqa: F401,F403

ORDER = ["處置前一天", "第1天", "第2天", "第3天", "第4天", "第5天", "第6天", "第7天",
         "出關前3天", "出關前2天", "處置最後一天", "出關日", "出關後第1天", "出關後第2天", "出關後第3天"]


def day_table(s):
    g = s.groupby("day").agg(n=("chg", "size"), chg=("chg", "mean"), red=("red", "mean"), oc=("oc", "mean"))
    g = g.reindex([o for o in ORDER if o in g.index])
    g = g[g.n >= 15]
    return [{"day": k, "n": int(r.n), "chg": round(r.chg * 100, 2), "red": round(r.red * 100, 1),
             "oc": round(r.oc * 100, 2)} for k, r in g.iterrows()]


S = disposal_stats()
days = {}
for reg in ["新制(5天)", "舊制(10天)"]:
    s = S[S.regime == reg]
    days[reg] = {"全部": day_table(s), "初犯": day_table(s[s.recid == "初犯"]), "累犯": day_table(s[s.recid == "累犯"]),
                 "第一次": day_table(s[s.level == "第一次/其他"]), "第二次": day_table(s[s.level == "第二次(20分)"]),
                 "有個股期": day_table(s[s.ssf])}


def pstats(df):
    o = {}
    for p, lab in (("IS", "2024-25"), ("OOS", "2026舊制"), ("NEW", "新制"), (None, "全部")):
        t = df if p is None else df[df.period == p]
        if len(t):
            r = t.net
            w, l = r[r > 0].sum(), -r[r < 0].sum()
            o[lab] = {"n": int(len(r)), "win": round((r > 0).mean() * 100, 1), "avg": round(r.mean() * 100, 2),
                      "pf": round(w / l, 2) if l > 0 else None}
    return o


def score(m):
    return (m.ssf.astype(int) + (m.inst5 > 0).astype(int) + m.recid.astype(int) + m.second.astype(int)
            + (m.market == "上市").astype(int) + (m.vol_ratio < 0.5).astype(int))


m1 = run_m1(tp=None, sl=0.90)
m1["score"] = score(m1)
strat = {"做法一": {"全部": pstats(m1), "小型個股期": pstats(m1[m1.small]),
                  "分數": {**{str(k): pstats(m1[m1.score == k]) for k in range(0, 3)}, "3+": pstats(m1[m1.score >= 3])}}}
# 做法三:有漲 + 開小高 0~5%,空到出關後第1天收盤,收盤漲 5% 停損
rows = []
for i, x in EP.items():
    r = ev.loc[i]; d = x["d"]; rel = x["rel"]
    f = base_feat(r, x, x["last"]); gap = d.Open[rel] / d.Close[x["last"]] - 1
    if f["chg_in"] > 0 and 0 < gap <= 0.05:
        e, out = d.Open[rel], None
        for u in (rel, x["post"][1]):
            if d.Close[u] >= e * 1.05:
                out = d.Close[u]; break
        out = out or d.Close[x["post"][1]]
        rows.append(dict(period=period(rel), net=e / out - 1 - COST_SSF - SLIP, small=r.small))
m3 = pd.DataFrame(rows)
strat["做法三"] = {"全部": pstats(m3), "小型個股期": pstats(m3[m3.small])}
m2 = run_m2()
strat["做法二"] = {"全部": pstats(m2[m2.second])}
rows = []
for i, x in EP.items():
    r = ev.loc[i]; d = x["d"]; n = len(x["days"]); fp = base_feat(r, x, x["pre"])
    if fp["inst5"] > 0 and fp["tw_ok"] and n >= 3:
        g = d.Close[x["days"][n - 2]] / d.Open[x["first"]] - 1
        rows.append(dict(period=period(x["pre"]), net=g - COST_SPOT - SLIP))
strat["做法四"] = {"全部": pstats(pd.DataFrame(rows))}

out = {"generated": str(pd.Timestamp.today().date()), "sample": "2024-01 ~ 2026-11 上市櫃處置股",
       "days": days, "strategies": strat}
dst = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results" / "app_stats.json"
dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(strat, ensure_ascii=False, indent=1))
