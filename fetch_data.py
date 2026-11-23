# -*- coding: utf-8 -*-
"""
處置股雷達 — 每日資料程式
=================================
做的事情:
  1. 從證交所(上市)、櫃買中心(上櫃)抓「注意股」「注意累計次數異常(快進處置)」「處置股」
  2. 算出四種機會清單:快進處置 / 處置中 / 快出關 / 剛出關
  3. 存成 docs/data.json(手機 App 讀這個檔)
  4. 用 LINE 推播一則摘要到你的手機

一般使用時你只需要修改下面 ★★★ 設定區 ★★★ 的內容。
LINE 的金鑰「不要」寫在這個檔案裡,要放在 GitHub 的 Secrets(教學裡有步驟)。
"""

# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# ★★★ 設定區:只要改這裡 ★★★

# 你特別關注的股票代號(例如你有在做的個股期標的),出現時會加上 ⭐ 並排在最前面
WATCHLIST = ["2330", "2454", "3008"]

# True = 只看一般股票(4 位數代號),把權證、牛熊證等過濾掉
ONLY_STOCKS = True

# 處置結束前幾個交易日算「快出關」
RELEASE_SOON_DAYS = 2

# 出關後幾個交易日內算「剛出關」
JUST_RELEASED_DAYS = 3

# 手機 App 的網址(GitHub Pages 開好之後填進來,LINE 訊息會附上這個連結)
APP_URL = "https://yeah0209-a11y.github.io/stock-alert/"

# 是否發 LINE 推播(True = 發,False = 不發)
SEND_LINE = True

# 就算今天沒有任何符合條件的股票,也要發一則「今日無訊號」?
SEND_WHEN_EMPTY = False

# 你額外知道有個股期的股票代號(自動抓期交所名單失敗時會用到)
EXTRA_SSF_CODES = []

# ★★★ 設定區結束 ★★★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★


import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
except ImportError:  # 測試模式不需要 requests
    requests = None

ROOT = Path(__file__).resolve().parent
DATA_JSON = ROOT / "docs" / "data.json"       # 給手機 App 讀
HISTORY_JSON = ROOT / "data" / "history.json"  # 累積的處置紀錄(用來算剛出關)

TW = timezone(timedelta(hours=8))

# ---- 資料來源網址 ----
TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"        # 上市 今日注意股
TWSE_NOTETRANS = "https://openapi.twse.com.tw/v1/announcement/notetrans"  # 上市 注意累計次數異常(快進處置)
TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"        # 上市 處置股
TWSE_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"  # 上市 每日收盤
TPEX_DISPOSAL = "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal"     # 上櫃 處置股(帶日期查詢)
TAIFEX_SSF = "https://openapi.taifex.com.tw/v1/SSFLists"                  # 期交所 個股期標的

# 測試模式:設定環境變數 DEMO_DIR,就改從資料夾裡的 json 檔讀資料,不連網路
DEMO_DIR = os.environ.get("DEMO_DIR")
DEMO_TODAY = os.environ.get("DEMO_TODAY")  # 例如 2026-11-23

errors = []  # 收集抓資料時的錯誤,顯示在 App 上


# ======================================================================
# 小工具
# ======================================================================
def today_tw() -> date:
    if DEMO_TODAY:
        return date.fromisoformat(DEMO_TODAY)
    return datetime.now(TW).date()


def get_json(url, name, params=None):
    """抓一個網址的 JSON。失敗不會讓整個程式停下來,只會記錄錯誤。"""
    if DEMO_DIR:
        f = Path(DEMO_DIR) / f"{name}.json"
        if not f.exists():
            errors.append(f"[測試] 找不到 {f.name}")
            return None
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        r = requests.get(url, params=params, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (stock-alert personal use)"})
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001
        errors.append(f"{name} 抓取失敗:{type(e).__name__}")
        print(f"[警告] {name} 抓取失敗:{e}", file=sys.stderr)
        return None


ROC_DATE = re.compile(r"(\d{2,3})\s*[/年.-]\s*(\d{1,2})\s*[/月.-]\s*(\d{1,2})")


def roc_to_date(text):
    """民國日期 → 西元 date。支援 115/11/23、115年11月23日、1151123。"""
    if not text:
        return None
    text = str(text).strip()
    if re.fullmatch(r"\d{7}", text):
        return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))
    m = ROC_DATE.search(text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    return date(y + 1911, mo, d)


def period_to_dates(text):
    """'115/11/23～115/11/27' → (date, date)"""
    found = ROC_DATE.findall(str(text or ""))
    if len(found) < 2:
        return None, None
    a, b = found[0], found[1]
    return (date(int(a[0]) + 1911, int(a[1]), int(a[2])),
            date(int(b[0]) + 1911, int(b[1]), int(b[2])))


def trading_days_between(a: date, b: date) -> int:
    """a 之後到 b(含 b)有幾個交易日。只排除週末,國定假日沒有排除(近似值)。"""
    if b <= a:
        return -trading_days_between(b, a) if b < a else 0
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def keep_code(code: str) -> bool:
    code = (code or "").strip()
    if not code:
        return False
    if ONLY_STOCKS:
        return bool(re.fullmatch(r"\d{4}", code))
    return True


def to_float(x):
    try:
        return float(str(x).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


# ======================================================================
# 抓資料
# ======================================================================
def load_prices():
    """上市股票收盤價與漲跌幅 {代號: {close, chg_pct}}"""
    out = {}
    rows = get_json(TWSE_DAY_ALL, "twse_day_all") or []
    for r in rows:
        close, chg = to_float(r.get("ClosingPrice")), to_float(r.get("Change"))
        if close is None:
            continue
        prev = close - (chg or 0)
        out[r.get("Code", "").strip()] = {
            "close": close,
            "chg_pct": round((chg or 0) / prev * 100, 2) if prev else None,
        }
    return out


def load_ssf_codes():
    """有個股期的股票代號(自動從期交所抓,抓不到就只用 EXTRA_SSF_CODES)"""
    codes = set(EXTRA_SSF_CODES)
    rows = get_json(TAIFEX_SSF, "taifex_ssf")
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            # 期交所欄位名稱可能變動,所以找「看起來像股票代號」的欄位
            for k, v in r.items():
                if any(t in k for t in ("證券代號", "StockCode", "UnderlyingCode", "標的證券代號")):
                    v = str(v).strip()
                    if re.fullmatch(r"\d{4,6}", v):
                        codes.add(v)
    return codes


def load_twse_near_disposal():
    """上市:注意累計次數異常 = 證交所官方公布『再異常就可能進處置』的名單"""
    out = []
    for r in get_json(TWSE_NOTETRANS, "twse_notetrans") or []:
        code = (r.get("Code") or "").strip()
        if not keep_code(code):
            continue
        out.append({
            "code": code, "name": (r.get("Name") or "").strip(), "market": "上市",
            "criteria": (r.get("RecentlyMetAttentionSecuritiesCriteria") or "").strip(),
        })
    return out


def load_twse_attention():
    """上市:今日注意股"""
    out = []
    for r in get_json(TWSE_NOTICE, "twse_notice") or []:
        code = (r.get("Code") or "").strip()
        if not keep_code(code):
            continue
        out.append({
            "code": code, "name": (r.get("Name") or "").strip(), "market": "上市",
            "count": r.get("NumberOfAnnouncement"),
            "info": (r.get("TradingInfoForAttention") or "").strip(),
            "date": str(roc_to_date(r.get("Date")) or ""),
        })
    return out


def load_twse_disposal():
    out = []
    for r in get_json(TWSE_PUNISH, "twse_punish") or []:
        code = (r.get("Code") or "").strip()
        if not keep_code(code):
            continue
        start, end = period_to_dates(r.get("DispositionPeriod"))
        if not start:
            continue
        out.append({
            "code": code, "name": (r.get("Name") or "").strip(), "market": "上市",
            "announced": str(roc_to_date(r.get("Date")) or ""),
            "start": str(start), "end": str(end),
            "reason": (r.get("ReasonsOfDisposition") or "").strip(),
            "measure": (r.get("DispositionMeasures") or "").strip(),
        })
    return out


def load_tpex_disposal(today: date):
    """上櫃:處置股(查最近 30 天的公告)。櫃買中心的格式比較常變,所以用欄位名稱去找。"""
    start = today - timedelta(days=30)
    params = {"startDate": f"{start:%Y/%m/%d}", "endDate": f"{today:%Y/%m/%d}", "response": "json"}
    js = get_json(TPEX_DISPOSAL, "tpex_disposal", params=params)
    out = []
    if not isinstance(js, dict):
        return out
    for table in js.get("tables") or []:
        fields = table.get("fields") or []

        def idx(*keys):
            for i, f in enumerate(fields):
                if any(k in str(f) for k in keys):
                    return i
            return None

        i_code, i_name = idx("證券代號", "代號"), idx("證券名稱", "名稱")
        i_period, i_date = idx("處置起訖", "處置起迄", "期間"), idx("公布日期")
        i_reason, i_measure = idx("處置條件"), idx("處置措施")
        if i_code is None or i_period is None:
            continue
        for row in table.get("data") or []:
            def cell(i):
                return str(row[i]).strip() if i is not None and i < len(row) else ""
            code = re.sub(r"<.*?>", "", cell(i_code))
            if not keep_code(code):
                continue
            s, e = period_to_dates(cell(i_period))
            if not s:
                continue
            out.append({
                "code": code, "name": re.sub(r"<.*?>", "", cell(i_name)), "market": "上櫃",
                "announced": str(roc_to_date(cell(i_date)) or ""),
                "start": str(s), "end": str(e),
                "reason": cell(i_reason), "measure": cell(i_measure),
            })
    return out


# ======================================================================
# 計算
# ======================================================================
def load_history():
    if HISTORY_JSON.exists():
        return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    return {"disposals": {}}


def save_history(hist):
    HISTORY_JSON.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_JSON.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")


def build(today: date):
    prices = load_prices()
    ssf = load_ssf_codes()
    hist = load_history()

    # 把今天抓到的處置紀錄存進歷史(證交所只列最近公告,存起來才知道誰還在關、誰剛出關)
    for d in load_twse_disposal() + load_tpex_disposal(today):
        hist["disposals"][f"{d['code']}_{d['start']}"] = d
    # 刪掉 60 天前就結束的舊紀錄,避免檔案越來越大
    cutoff = today - timedelta(days=60)
    hist["disposals"] = {k: v for k, v in hist["disposals"].items()
                         if date.fromisoformat(v["end"]) >= cutoff}

    def enrich(item):
        p = prices.get(item["code"], {})
        item["close"] = p.get("close")
        item["chg_pct"] = p.get("chg_pct")
        item["watch"] = item["code"] in WATCHLIST
        item["ssf"] = item["code"] in ssf
        return item

    in_disposal, releasing, just_released = [], [], []
    for d in hist["disposals"].values():
        s, e = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
        item = enrich(dict(d))
        if s <= today <= e or (today < s):  # 處置中,或已公告明天開始
            item["days_left"] = trading_days_between(today, e) + (1 if today >= s else 0)
            item["status"] = "明日開始" if today < s else "處置中"
            in_disposal.append(item)
            if today >= s and item["days_left"] <= RELEASE_SOON_DAYS:
                releasing.append(item)
        elif e < today:
            since = trading_days_between(e, today)
            if since <= JUST_RELEASED_DAYS:
                item["days_since"] = since
                just_released.append(item)

    # 同一檔股票若處置中,就不要再出現在剛出關
    active = {x["code"] for x in in_disposal}
    just_released = [x for x in just_released if x["code"] not in active]

    near = [enrich(x) for x in load_twse_near_disposal() if x["code"] not in active]
    attention = [enrich(x) for x in load_twse_attention()]

    def sort_key(x):
        return (not x.get("watch"), not x.get("ssf"), x["code"])

    for lst in (near, in_disposal, releasing, just_released, attention):
        lst.sort(key=sort_key)
    releasing.sort(key=lambda x: (x["days_left"], sort_key(x)))

    data = {
        "updated": datetime.now(TW).strftime("%Y-%m-%d %H:%M") if not DEMO_TODAY else f"{today} 18:30",
        "date": str(today),
        "near_disposal": near,
        "in_disposal": in_disposal,
        "releasing_soon": releasing,
        "just_released": just_released,
        "attention": attention,
        "errors": errors,
        "settings": {"release_soon_days": RELEASE_SOON_DAYS, "just_released_days": JUST_RELEASED_DAYS},
    }
    return data, hist


# ======================================================================
# LINE 推播
# ======================================================================
def fmt(x):
    tag = "⭐" if x.get("watch") else ""
    tag += "🅵" if x.get("ssf") else ""
    price = ""
    if x.get("close") is not None:
        price = f" {x['close']:g}"
        if x.get("chg_pct") is not None:
            price += f"({x['chg_pct']:+.1f}%)"
    return f"{tag}{x['code']} {x['name']}{price}"


def line_message(data):
    lines = [f"📡 處置股雷達 {data['date']}"]

    def section(title, items, extra=lambda x: "", limit=10):
        if not items:
            return
        lines.append("")
        lines.append(f"{title}({len(items)})")
        for x in items[:limit]:
            lines.append("・" + fmt(x) + extra(x))
        if len(items) > limit:
            lines.append(f"…另有 {len(items) - limit} 檔,請開 App 查看")

    section("🔥 快進處置", data["near_disposal"])
    section("🚪 快出關", data["releasing_soon"], lambda x: f" 剩{x['days_left']}天")
    section("🆕 剛出關", data["just_released"], lambda x: f" 出關{x['days_since']}天")
    new_today = [x for x in data["in_disposal"] if x["status"] == "明日開始"]
    section("🔒 明日開始處置", new_today)

    has_signal = len(lines) > 1
    if data["errors"]:
        lines.append("")
        lines.append("⚠️ 部分資料抓取失敗:" + ";".join(data["errors"][:3]))
    if not has_signal:
        lines.append("")
        lines.append("今天沒有符合條件的股票。")
    lines.append("")
    lines.append("⭐=觀察名單 🅵=有個股期")
    lines.append(APP_URL)
    return "\n".join(lines)[:4900], has_signal


def send_line(text):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token:
        print("[提醒] 沒有設定 LINE_CHANNEL_ACCESS_TOKEN,略過推播")
        return
    # broadcast = 發給這個官方帳號的所有好友;個人用的話好友只有你自己
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"messages": [{"type": "text", "text": text}]},
        timeout=30,
    )
    print("LINE 推播結果:", r.status_code, r.text[:200])


# ======================================================================
def main():
    today = today_tw()
    data, hist = build(today)
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    save_history(hist)

    text, has_signal = line_message(data)
    print(text)
    if SEND_LINE and not DEMO_DIR and (has_signal or SEND_WHEN_EMPTY or data["errors"]):
        send_line(text)


if __name__ == "__main__":
    main()
