# -*- coding: utf-8 -*-
"""
額外市場資料:委買委賣價差(整股/零股)、籌碼動向(法人、融資、集保大戶)、交易成本試算
資料來源都是免費公開資料:
  * 證交所 MIS 即時報價 getStockInfo(整股) / getOddInfo(盤中零股) — 上市上櫃都有,最佳五檔
  * 證交所 MI_MARGN / 櫃買 openapi 融資融券餘額
  * 集保結算所 1-5 集保戶股權分散表(每週更新)
"""
import json
import re
import time
from datetime import date
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (stock-alert personal use)"}
MIS_LOT = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_ODD = "https://mis.twse.com.tw/stock/api/getOddInfo.jsp"
TWSE_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_MARGIN = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
TDCC = "https://opendata.tdcc.com.tw/getOD.ashx"

SPREAD_WARN = 1.0      # 委買委賣價差超過 1% 就提醒「價差太大」


def _f(x):
    try:
        v = float(str(x).replace(",", ""))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# 委買委賣
# ----------------------------------------------------------------------
def _parse_book(m):
    asks = [_f(x) for x in str(m.get("a", "")).split("_") if x]
    bids = [_f(x) for x in str(m.get("b", "")).split("_") if x]
    aq = [int(x) for x in str(m.get("f", "")).split("_") if x.isdigit()]
    bq = [int(x) for x in str(m.get("g", "")).split("_") if x.isdigit()]
    a1 = next((x for x in asks if x), None)
    b1 = next((x for x in bids if x), None)
    spread = round((a1 - b1) / ((a1 + b1) / 2) * 100, 2) if a1 and b1 else None
    return {"bid": b1, "ask": a1, "spread_pct": spread,
            "bid_qty": bq[0] if bq else None, "ask_qty": aq[0] if aq else None,
            "last": _f(m.get("z")) or _f(m.get("pz")), "time": m.get("t") or m.get("%"),
            "volume": int(m.get("v")) if str(m.get("v", "")).isdigit() else None}


def quotes(codes_markets):
    """{代號: 市場} → {代號: {"lot": {...}, "odd": {...}}}(整股的量單位是張,零股是股)"""
    out = {}
    items = list(codes_markets.items())
    for i in range(0, len(items), 20):
        chunk = items[i:i + 20]
        ex = "|".join(f"{'tse' if m == '上市' else 'otc'}_{c}.tw" for c, m in chunk)
        for kind, url in (("lot", MIS_LOT), ("odd", MIS_ODD)):
            try:
                r = requests.get(url, params={"ex_ch": ex, "json": "1", "delay": "0"}, headers=UA, timeout=20)
                for m in r.json().get("msgArray", []):
                    out.setdefault(m.get("c"), {})[kind] = _parse_book(m)
            except Exception as e:  # noqa: BLE001
                print(f"[警告] MIS {kind} 抓取失敗:{e}")
            time.sleep(1)
    return out


def spread_verdict(q):
    """給卡片用的一句話"""
    if not q:
        return None
    parts = []
    for kind, name in (("odd", "零股"), ("lot", "整股")):
        b = q.get(kind) or {}
        if b.get("spread_pct") is None:
            continue
        flag = "⚠️太大" if b["spread_pct"] > SPREAD_WARN else "OK"
        parts.append(f"{name} 買{b['bid']:g}/賣{b['ask']:g} 價差{b['spread_pct']}% {flag}")
    return ";".join(parts) if parts else None


# ----------------------------------------------------------------------
# 籌碼:融資、集保大戶(用快取累積,才看得到變化)
# ----------------------------------------------------------------------
def update_chip_cache(path: Path, today: date, get_json):
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cache.setdefault("margin", {})
    cache.setdefault("tdcc", {})
    k = today.isoformat()
    # 融資餘額(張)
    if k not in cache["margin"]:
        day = {}
        js = get_json(TWSE_MARGIN, "twse_margin", params={"date": f"{today:%Y%m%d}", "selectType": "STOCK", "response": "json"})
        if isinstance(js, dict) and js.get("stat") == "OK":
            for t in js.get("tables") or []:
                f = t.get("fields") or []
                if "今日餘額" in f:
                    i_bal = f.index("今日餘額")
                    for r in t.get("data") or []:
                        c = str(r[0]).strip()
                        if re.fullmatch(r"\d{4}", c):
                            day[c] = int(str(r[i_bal]).replace(",", "") or 0)
        rows = get_json(TPEX_MARGIN, "tpex_margin")
        for r in rows if isinstance(rows, list) else []:
            c = str(r.get("SecuritiesCompanyCode", "")).strip()
            if re.fullmatch(r"\d{4}", c) and str(r.get("Date", "")).strip():
                try:
                    day[c] = int(float(r.get("MarginPurchaseBalance") or 0))
                except ValueError:
                    pass
        if day:
            cache["margin"][k] = day
    # 集保大戶(每週五資料,週末才更新;只在資料日期是新的時候存)
    try:
        r = requests.get(TDCC, params={"id": "1-5"}, headers=UA, timeout=60)
        r.encoding = "utf-8-sig"
        lines = r.text.splitlines()[1:]
        week = {}
        d0 = None
        for ln in lines:
            p = ln.split(",")
            if len(p) < 6:
                continue
            d0 = p[0]
            c, lvl = p[1].strip(), int(p[2])
            if re.fullmatch(r"\d{4}", c) and 12 <= lvl <= 15:   # 400 張以上
                week[c] = round(week.get(c, 0) + float(p[5]), 2)
        if d0 and week and d0 not in cache["tdcc"]:
            cache["tdcc"][d0] = week
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 集保資料抓取失敗:{e}")
    cache["margin"] = {x: cache["margin"][x] for x in sorted(cache["margin"])[-12:]}
    cache["tdcc"] = {x: cache["tdcc"][x] for x in sorted(cache["tdcc"])[-6:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def chips_for(code, inst, chip_cache):
    """回傳一檔股票的籌碼摘要(張)"""
    out = {}
    days = sorted(inst)[-5:]
    if days:
        fi = sum(inst[d].get(code, [0, 0, 0])[0] for d in days)
        it = sum(inst[d].get(code, [0, 0, 0])[1] for d in days)
        tot = sum(inst[d].get(code, [0, 0, 0])[2] for d in days)
        out.update({"fi5": round(fi / 1000), "it5": round(it / 1000), "dealer5": round((tot - fi - it) / 1000),
                    "inst5": round(tot / 1000)})
    m = chip_cache.get("margin", {})
    md = [d for d in sorted(m) if code in m[d]]
    if md:
        out["margin"] = m[md[-1]][code]
        if len(md) >= 2:
            base = m[md[max(0, len(md) - 6)]][code]
            out["margin_chg"] = m[md[-1]][code] - base
            out["margin_days"] = min(5, len(md) - 1)
    t = chip_cache.get("tdcc", {})
    td = [d for d in sorted(t) if code in t[d]]
    if td:
        out["big400"] = t[td[-1]][code]
        if len(td) >= 2:
            out["big400_chg"] = round(t[td[-1]][code] - t[td[-2]][code], 2)
    score = 0
    if out.get("inst5") is not None:
        score += 1 if out["inst5"] > 0 else (-1 if out["inst5"] < 0 else 0)
    if out.get("big400_chg") is not None:
        score += 1 if out["big400_chg"] > 0 else (-1 if out["big400_chg"] < 0 else 0)
    if out.get("margin_chg") is not None and out.get("inst5") is not None:
        if out["margin_chg"] > 0 and out["inst5"] < 0:   # 法人賣、散戶融資接
            score -= 1
    out["light"] = "🟢" if score >= 1 else ("🔴" if score <= -1 else "🟡")
    return out


def chips_text(c):
    if not c:
        return None
    p = []
    if "inst5" in c:
        p.append(f"法人5日 外資{c['fi5']:+d}/投信{c['it5']:+d}/自營{c['dealer5']:+d} 張")
    if "margin" in c:
        p.append(f"融資 {c['margin']:,} 張" + (f"({c['margin_chg']:+,d},{c['margin_days']}日)" if "margin_chg" in c else ""))
    if "big400" in c:
        p.append(f"400張大戶 {c['big400']}%" + (f"(週{c['big400_chg']:+.2f})" if "big400_chg" in c else ""))
    return c["light"] + " " + ";".join(p)


# ----------------------------------------------------------------------
# 交易成本(國泰證券:電子下單 2.8 折、每筆最低 1 元;證交稅賣出 0.3%)
# ----------------------------------------------------------------------
def cost_example(amount, fee_discount, min_fee, tax=0.003):
    fee = max(min_fee, int(amount * 0.001425 * fee_discount))     # 手續費無條件捨去
    total = fee * 2 + int(amount * tax)
    return {"amount": amount, "fee_each": fee, "tax": int(amount * tax), "total": total,
            "pct": round(total / amount * 100, 2)}
