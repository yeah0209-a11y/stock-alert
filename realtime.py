# -*- coding: utf-8 -*-
"""
盤中即時檢查(App 按「⚡ 即時價差」或排程 09:12、13:18 自動執行)
  1. 讀今晚的訊號(docs/data.json)和你的持股(data/holdings.json)
  2. 抓證交所即時五檔(整股 + 盤中零股)
  3. 算委買委賣價差,檢查做法三開盤價區間、做法一持股是否接近停損
  4. 存成 docs/realtime.json(只顯示在 App,不發 LINE)
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import market_extra as MX
from fetch_data import send_line

ROOT = Path(__file__).resolve().parent
TW = timezone(timedelta(hours=8))


def main():
    data = json.loads((ROOT / "docs" / "data.json").read_text(encoding="utf-8"))
    hp = ROOT / "data" / "holdings.json"
    holdings = json.loads(hp.read_text(encoding="utf-8")).get("holdings", []) if hp.exists() else []
    signals = data.get("signals", [])
    levels = data.get("levels", {})
    cm = {x["code"]: x["market"] for x in signals}
    for h in holdings:
        cm.setdefault(h["code"], h.get("market") or "上市")
    now = datetime.now(TW)
    q = MX.quotes(cm) if cm else {}

    lines = [f"⚡ 即時檢查 {now:%m/%d %H:%M}"]
    out = {"time": now.strftime("%Y-%m-%d %H:%M"), "quotes": {}, "notes": {}}
    for code in cm:
        out["quotes"][code] = q.get(code)
        out["notes"][code] = []

    if signals:
        lines += ["", "🟢 今天的訊號"]
    for x in signals:
        qq = q.get(x["code"]) or {}
        lot, odd = qq.get("lot") or {}, qq.get("odd") or {}
        notes = out["notes"][x["code"]]
        v = MX.spread_verdict(qq)
        if x["strategy"] == "做法三" and x.get("short_range") and lot.get("last"):
            lo, hi = x["short_range"]
            o = lot.get("last")
            ok = lo < o <= hi
            notes.append(f"現價 {o:g},可空區間 {lo:g}~{hi:g} → {'符合' if ok else '不符合,不做'}")
        lines.append(f"{x['rank']}. [{x['tier']}] {x['code']} {x['name']} {x['title']} 現價 {lot.get('last') or odd.get('last') or '-'}")
        if v:
            lines.append(f"   {v}")
        for n in notes:
            lines.append(f"   {n}")

    if holdings:
        lines += ["", "🟡 持股"]
    for h in holdings:
        qq = q.get(h["code"]) or {}
        lot = qq.get("lot") or {}
        px = lot.get("last") or (qq.get("odd") or {}).get("last")
        notes = out["notes"][h["code"]]
        lv = levels.get(h["code"], {})
        if h.get("strategy") == "做法一" and px:
            tgt = round(float(h["entry_price"]) * 1.10, 2)
            if px >= tgt:
                notes.append(f"已達停利價 {tgt},確認限價單是否成交")
            stop = lv.get("stop_next")
            if stop and px < stop:
                notes.append(f"🔴 現價 {px:g} 低於今天停損價 {stop:g},若收盤仍低於就要賣")
            elif stop and px < stop * 1.02:
                notes.append(f"現價 {px:g} 接近停損價 {stop:g}(差 2% 內),收盤前注意")
        lines.append(f"・{h['code']} {h.get('name', '')} 現價 {px or '-'}")
        v = MX.spread_verdict(qq)
        if v:
            lines.append(f"   {v}")
        for n in notes:
            lines.append(f"   {n}")

    if not cm:
        lines.append("今天沒有訊號,也沒有持股。")
    (ROOT / "docs" / "realtime.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    text = "\n".join(lines)[:4900]
    print(text)
    # 使用者只要每天 18:40 那一則推播,即時檢查只更新 App,不發 LINE
    if cm and os.environ.get("SEND_LINE_REALTIME"):
        send_line(text)


if __name__ == "__main__":
    main()
