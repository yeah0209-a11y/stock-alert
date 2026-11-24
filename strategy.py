# -*- coding: utf-8 -*-
"""
處置股操作訊號(根據權證小哥處置股規則 + 2024~2026 回測)
=========================================================
v4(2026-11-24 回測調整,樣本外與新制分開驗證):
  做法一  出關前低接  : 上升月線 + 深蹲蓄力/浪子回頭 → 隔天開盤買,不設停利,收盤破月線×0.90 停損,出關日開盤賣
                        現股要「勝率分數」≥3(有個股期/法人買超/累犯/第二次處置/上市/處置中量縮,6 項)
  做法二  出關前一天買: 第二次處置,處置最後一天收盤前買,出關日開盤賣 → 新制(5天處置)回測沒有優勢,預設停用
  做法三  出關日開小高空: 只做小型個股期。處置期間有漲,出關日開盤比昨收高 0~5% 才空,
                        抱到「出關後第1天」收盤回補;收盤比放空價高 5% 就停損
  做法四  處置首日買  : 明天開始處置 + 法人近5日買超 + 大盤站上月線 → 處置第一天開盤買,出關前2天收盤賣

優先級(使用者指定:小型個股期最優先、一般個股期先不做、現股只做多):
  S  小型個股期:做法一、做法三、做法四
  A  現股:做法四
  B  現股:做法一(勝率分數 ≥3)
同一級內:勝率分數高的排前面 → 法人近5日買超 → 做法一離停損越遠越前面
"""
from datetime import date, timedelta
import math

import pandas as pd

TARGET = None          # 做法一不設停利(v3 是 10%;回測停利會砍掉大賺的單)
STOP_K = 0.90          # 收盤跌破 月線 × 0.90 停損(v3 是 0.95)
M1_MIN_SCORE = 3       # 現股做法一要的最低勝率分數
M3_STOP = 0.05         # 做法三:收盤比放空價高 5% 就回補
NEW_REGIME_FROM = date(2026, 8, 10)   # 處置新制(處置 5 天)
ENABLE_M2_NEW = False  # 新制下做法二回測沒有優勢 → 不出訊號
RECID_DAYS = 90        # 上次出關 90 天內又被處置 = 累犯
MA_N = 20
GAP_MAX = 0.05         # 做法三:出關日開盤最多比昨收高 5%

TIER_ORDER = {"S": 0, "A": 1, "B": 2}
STRAT_ORDER = {"做法一": 0, "做法三": 1, "做法四": 2, "做法二": 3}


def is_second(measure: str) -> bool:
    m = measure or ""
    return any(k in m for k in ("第二次", "再次", "二十分鐘", "20分鐘"))


# ----------------------------------------------------------------------
# 交易日曆
# ----------------------------------------------------------------------
class Calendar:
    def __init__(self, holidays):
        self.holidays = set(holidays)

    def is_trading(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def next(self, d: date) -> date:
        d += timedelta(days=1)
        while not self.is_trading(d):
            d += timedelta(days=1)
        return d

    def prev(self, d: date) -> date:
        d -= timedelta(days=1)
        while not self.is_trading(d):
            d -= timedelta(days=1)
        return d

    def count(self, a: date, b: date) -> int:
        """a ~ b(含頭尾)有幾個交易日"""
        n, d = 0, a
        while d <= b:
            n += self.is_trading(d)
            d += timedelta(days=1)
        return n


# ----------------------------------------------------------------------
# 把重疊的處置公告合併成一段(第一次還沒結束就加碼成第二次的情況)
# ----------------------------------------------------------------------
def merge_episodes(disposals, cal: Calendar, past=()):
    rows = sorted(disposals, key=lambda d: (d["code"], d["start"]))
    eps = []
    for d in rows:
        s, e = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
        second = is_second(d.get("measure"))
        if eps and eps[-1]["code"] == d["code"] and s <= eps[-1]["end"]:
            ep = eps[-1]
            ep["end"] = max(ep["end"], e)
            if second and ep["second_from"] is None:
                ep["second_from"] = s
        else:
            eps.append({"code": d["code"], "name": d["name"], "market": d["market"],
                        "start": s, "end": e, "second_from": s if second else None})
    ends = {}
    for p in list(past) + [{"code": e["code"], "start": str(e["start"]), "end": str(e["end"])} for e in eps]:
        ends.setdefault(p["code"], set()).add(date.fromisoformat(str(p["end"])[:10]))
    for ep in eps:
        ep["release"] = cal.next(ep["end"])
        ep["key"] = f"{ep['code']}_{ep['start']}"
        ep["total_days"] = cal.count(ep["start"], ep["end"])
        ep["new_regime"] = ep["start"] >= NEW_REGIME_FROM
        prev = [e for e in ends.get(ep["code"], ()) if e < ep["start"] and (ep["start"] - e).days <= RECID_DAYS]
        ep["recid"] = bool(prev)
    return eps


def day_label(ep, d: date, cal: Calendar) -> str:
    """d 是這段處置的第幾天(對應 App 處置統計表的列名)"""
    if d < ep["start"]:
        return "處置前一天"
    if d >= ep["release"]:
        k = cal.count(ep["release"], d) - 1
        return "出關日" if k == 0 else f"出關後第{k}天"
    n, k = ep["total_days"], cal.count(ep["start"], d)
    back = n - k
    if back == 0:
        return "處置最後一天"
    if back <= 2:
        return f"出關前{back + 1}天"
    return f"第{k}天"


def score_factors(ep, is_ssf, tot5, second, vol_ratio):
    """勝率分數:回測裡 2024-25、2026 舊制、新制三段都比較好的條件,各 1 分"""
    f = {"有個股期": bool(is_ssf), "法人5日買超": bool(tot5 and tot5 > 0), "累犯": bool(ep.get("recid")),
         "第二次處置": bool(second), "上市": ep.get("market") == "上市",
         "處置中量縮": vol_ratio is not None and vol_ratio < 0.5}
    return sum(f.values()), [k for k, v in f.items() if v]


# ----------------------------------------------------------------------
# 法人:進場前 5 個交易日的買賣超
# ----------------------------------------------------------------------
def inst_window(inst, code, days):
    """inst = {date_str: {code: [外資, 投信, 三大法人]}};回傳 (外資+投信, 投信, 三大法人) 或 None(資料不足)"""
    have = [d for d in days if d.isoformat() in inst]
    if len(have) < max(1, int(len(days) * 0.8)):
        return None
    fi = it = tot = 0
    for d in have:
        v = inst[d.isoformat()].get(code)
        if v:
            fi += v[0]; it += v[1]; tot += v[2]
    return fi + it, it, tot


# ----------------------------------------------------------------------
# 個股技術數值
# ----------------------------------------------------------------------
def levels_for(px: pd.DataFrame, today: date):
    """回傳今天的收盤、月線、月線是否上升、明天收盤的停損價(低於這個價格收盤就要賣)"""
    px = px[px.index.date <= today]
    if len(px) < MA_N + 6:
        return None
    c = px["Close"]
    ma = c.rolling(MA_N).mean()
    s19 = c.iloc[-(MA_N - 1):].sum()
    # 明天收盤價 P 要滿足 P < 0.95 × (s19 + P) / 20 才算破線 → P < 0.95·s19 / (20 − 0.95)
    stop_next = STOP_K * s19 / (MA_N - STOP_K)
    return {
        "date": px.index[-1].date(), "close": float(c.iloc[-1]),
        "high": float(px["High"].iloc[-1]), "low": float(px["Low"].iloc[-1]),
        "ma20": float(ma.iloc[-1]), "ma_up": bool(ma.iloc[-1] > ma.iloc[-6]),
        "stop_today": float(ma.iloc[-1] * STOP_K), "stop_next": float(stop_next),
    }


def r2(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 2)


# ----------------------------------------------------------------------
# 產生「明天」的進場訊號
# ----------------------------------------------------------------------
def build_signals(today, cal, episodes, prices, inst, small, signaled, ssf=(), twii=None):
    """
    today    : 今天(收盤後執行)
    episodes : merge_episodes() 的結果
    prices   : {code: DataFrame(Open,High,Low,Close,Volume)},含今天
    inst     : 法人資料
    small    : {code: 小型個股期契約代號}
    signaled : {episode_key: True} 做法一每段處置只做一次
    """
    out, lv_map = [], {}
    if not cal.is_trading(today):
        return out, lv_map
    n1 = cal.next(today)
    tw_ok = None
    if twii is not None and len(twii) > 25:
        tw_c = twii["Close"][twii.index.date <= today]
        tw_ok = bool(tw_c.iloc[-1] > tw_c.rolling(20).mean().iloc[-1])
    for ep in episodes:
        if not (ep["start"] <= today <= ep["end"] or ep["start"] == n1):
            continue
        px = prices.get(ep["code"])
        if px is None or px.empty:
            continue
        lv = levels_for(px, today)
        if lv is None or lv["date"] != today:   # 今天沒有股價(停牌等)
            continue
        lv["release"] = ep["release"]
        lv_map[ep["code"]] = lv
        hist = px[px.index.date <= today]
        before = hist[hist.index.date < ep["start"]]
        if len(before) < MA_N:
            continue
        during = hist[hist.index.date >= ep["start"]]
        pre_vol = float(before["Volume"].iloc[-5:].mean()) if "Volume" in before else 0
        vol_ratio = float(during["Volume"].mean()) / pre_vol if len(during) and pre_vol > 0 else None
        ma_all = hist["Close"].rolling(MA_N).mean()
        pre_close = float(before["Close"].iloc[-1])
        pre_ma = float(ma_all.loc[before.index[-1]])
        days5 = [d.date() for d in hist.index[-5:]]
        w = inst_window(inst, ep["code"], days5)
        tot5 = w[2] if w else None
        it5 = w[1] if w else None
        is_small = ep["code"] in small
        second = ep["second_from"] is not None and ep["second_from"] <= n1
        score, factors = score_factors(ep, ep["code"] in ssf or is_small, tot5, second, vol_ratio)
        base = {"code": ep["code"], "name": ep["name"], "market": ep["market"],
                "instrument": "小型個股期" if is_small else "現股",
                "small_contract": small.get(ep["code"]), "second": second,
                "margin_x": (2.0 if second else 1.5) if is_small else None,
                "close": r2(lv["close"]), "ma20": r2(lv["ma20"]),
                "start": str(ep["start"]), "end": str(ep["end"]), "release": str(ep["release"]),
                "inst5": tot5, "it5": it5, "episode": ep["key"], "entry_day": str(n1),
                "score": score, "factors": factors, "recid": ep.get("recid"), "new_regime": ep.get("new_regime")}

        # ---------- 做法四:明天開始處置(今天公告)→ 處置第一天開盤買,出關前2天收盤賣
        if n1 == ep["start"]:
            if ep["total_days"] >= 3 and tot5 is not None and tot5 > 0 and tw_ok:
                exit_day = cal.prev(ep["end"])
                out.append({**base, "strategy": "做法四", "side": "long", "tier": "S" if is_small else "A",
                            "title": "處置首日買",
                            "entry": f"{n1:%m/%d}(處置第1天)9:00 開盤買進",
                            "exit": f"{exit_day:%m/%d}(出關前2天)13:25~13:30 收盤前賣出",
                            "stop": "不設停損,每檔最多用 1/10 資金(回測最差約 −36%)", "target": None,
                            "exit_day": str(exit_day)})
            continue

        # ---------- 做法二:明天是處置最後一天
        if n1 == ep["end"] and second and (ENABLE_M2_NEW or not ep.get("new_regime")):
            ok = True if is_small else (it5 is not None and it5 != 0)
            if ok:
                out.append({**base, "strategy": "做法二", "side": "long", "tier": "S" if is_small else "A",
                            "title": "出關前一天買",
                            "entry": f"{n1:%m/%d}(處置最後一天)13:25~13:30 收盤前買進",
                            "exit": f"{ep['release']:%m/%d} 出關日 9:00 開盤賣出",
                            "stop": "只持有一晚,不設停損(回測最差約 −10%)", "target": None})

        # ---------- 做法三:明天出關(小型個股期當沖空)
        if is_small and today == ep["end"] and lv["close"] > pre_close:
            lo, hi = lv["close"], lv["close"] * (1 + GAP_MAX)
            n2 = cal.next(n1)
            out.append({**base, "strategy": "做法三", "side": "short", "tier": "S",
                        "title": "出關日開小高放空",
                        "entry": f"{n1:%m/%d} 開盤價在 {lo:.2f} 以上、{hi:.2f} 以下才放空(開平盤、開低或開太高都不做)",
                        "exit": f"{n2:%m/%d}(出關後第1天)13:30~13:45 收盤前回補",
                        "stop": f"{n1:%m/%d} 收盤比放空價高 {M3_STOP:.0%} 以上就在收盤前回補(回測最差約 −13%)",
                        "target": None, "short_range": [r2(lo), r2(hi)], "exit_day": str(n2)})

        # ---------- 做法一:出關前低接(隔天開盤買,隔天不能是出關日)
        if n1 < ep["release"] and not signaled.get(ep["key"]) and pre_close > pre_ma and lv["ma_up"]:
            prev5 = hist["Close"].iloc[-6:-1]
            hi5 = float(prev5.max()) if len(prev5) else lv["close"]
            langzi = lv["low"] <= lv["ma20"] * 1.02 and lv["close"] >= lv["ma20"] * 0.98
            squat = lv["close"] / hi5 - 1 <= -0.10 and lv["close"] >= lv["ma20"] * 0.95
            if langzi or squat:
                ok = True if is_small else score >= M1_MIN_SCORE
                signaled[ep["key"]] = True     # 不論有沒有通過法人條件,這段處置的第一個訊號就用掉了(同回測)
                if ok:
                    est = lv["close"]
                    out.append({**base, "strategy": "做法一", "side": "long", "tier": "S" if is_small else "B",
                                "title": "出關前低接(" + ("深蹲蓄力" if squat else "浪子回頭") + ")",
                                "entry": f"{n1:%m/%d} 9:00 開盤買進",
                                "exit": f"{ep['release']:%m/%d} 出關日 9:00 開盤賣出(不設停利,讓獲利跑)",
                                "target": None,
                                "stop": f"收盤跌破月線×{STOP_K:.2f} 就賣;明天的停損收盤價約 {lv['stop_next']:.2f}(每天更新)",
                                "stop_next": r2(lv["stop_next"]),
                                "room": (lv["close"] - lv["stop_next"]) / lv["close"]})

    def key(x):
        return (TIER_ORDER[x["tier"]], -(x.get("score") or 0), STRAT_ORDER[x["strategy"]],
                0 if (x.get("inst5") or 0) > 0 else 1, -(x.get("room") or 0))
    out.sort(key=key)
    for i, x in enumerate(out, 1):
        x["rank"] = i
        x.pop("room", None)
    return out, lv_map


# ----------------------------------------------------------------------
# 持股狀態(使用者在 App 按「我有買」的部位)
# ----------------------------------------------------------------------
def holding_status(h, today, cal, episodes, prices):
    """回傳 dict:level(red/yellow)、訊息、停利價、明天停損價、出關日"""
    code = h["code"]
    eps = [e for e in episodes if e["code"] == code and e["start"] <= date.fromisoformat(h["entry_date"]) <= e["release"]]
    ep = eps[-1] if eps else None
    release = ep["release"] if ep else None
    px = prices.get(code)
    lv = levels_for(px, today) if px is not None and not px.empty else None
    n1 = cal.next(today) if cal.is_trading(today) else cal.next(today - timedelta(days=1))
    entry = float(h.get("entry_price") or 0)
    st = {"id": h["id"], "code": code, "name": h.get("name", ""), "strategy": h.get("strategy"),
          "instrument": h.get("instrument"), "side": h.get("side", "long"), "entry_price": entry,
          "entry_date": h["entry_date"], "qty": h.get("qty"), "release": str(release) if release else None,
          "target": r2(entry * (1 + TARGET)) if TARGET and h.get("strategy") == "做法一" and entry else None,
          "stop_next": r2(lv["stop_next"]) if lv and h.get("strategy") == "做法一" else None,
          "close": r2(lv["close"]) if lv else None, "alerts": [], "level": "yellow"}
    if st["close"] and entry:
        st["pnl_pct"] = round(((st["close"] / entry - 1) if st["side"] == "long" else (entry / st["close"] - 1)) * 100, 2)

    def red(msg):
        st["alerts"].append(msg)
        st["level"] = "red"

    s = h.get("strategy")
    traded_today = lv is not None and lv["date"] == today
    ed = date.fromisoformat(h["entry_date"])
    if s == "做法三":
        exit_day = date.fromisoformat(h["exit_day"]) if h.get("exit_day") else cal.next(ed)
        st["exit_day"] = str(exit_day)
        if h.get("daytrade"):      # v3 的當沖單
            if ed < today or (ed == today and traded_today):
                red("當沖單應該已經回補,若已回補請按「已回補」")
        elif today >= exit_day:
            red(f"{exit_day:%m/%d} 收盤前應該已經回補,若已回補請按「已回補」")
        elif ed == today and lv and traded_today and entry and lv["close"] >= entry * (1 + M3_STOP):
            red(f"今天收盤 {lv['close']:.2f} 比放空價高 {M3_STOP:.0%} 以上 → 應該已在收盤前停損回補")
        elif cal.next(today) == exit_day:
            red(f"明天 {exit_day:%m/%d} 13:30~13:45 收盤前回補")
    elif s == "做法四":
        exit_day = date.fromisoformat(h["exit_day"]) if h.get("exit_day") else (cal.prev(ep["end"]) if ep else None)
        st["exit_day"] = str(exit_day) if exit_day else None
        if exit_day and today >= exit_day:
            red(f"{exit_day:%m/%d}(出關前2天)收盤前應該已經賣出,若已賣出請按「已賣出」")
        elif exit_day and cal.next(today) == exit_day:
            red(f"明天 {exit_day:%m/%d}(出關前2天)13:25~13:30 收盤前賣出")
        elif exit_day:
            st["alerts"].append(f"{exit_day:%m/%d}(出關前2天)收盤前賣出")
    elif s in ("做法一", "做法二"):
        if s == "做法一" and lv and traded_today:
            if st["target"] and lv["high"] >= st["target"] and date.fromisoformat(h["entry_date"]) <= today:
                red(f"今天最高 {lv['high']:.2f} 已碰到停利價 {st['target']:.2f},若已成交請按「已賣出」")
            if lv["close"] < lv["stop_today"]:
                red(f"今天收盤 {lv['close']:.2f} 已跌破停損線 {lv['stop_today']:.2f} → 明天 9:00 開盤賣出")
        if release and release == n1:
            red(f"明天 {release:%m/%d} 出關 → 9:00 開盤賣出")
        elif release and release <= today:
            red(f"已經出關({release:%m/%d}),請盡快賣出")
        if s == "做法一" and st["stop_next"] and not any("停損" in a for a in st["alerts"]):
            st["alerts"].append(f"明天收盤價若低於 {st['stop_next']:.2f} 就賣(小型個股期可在 13:30~13:45 收盤確認後賣)")
        if ep and h.get("release_at_buy") and release and str(release) != h["release_at_buy"]:
            st["alerts"].append(f"⚠️ 處置延長,出關日從 {h['release_at_buy']} 改成 {release}")
    return st
