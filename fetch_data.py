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

# 最多同時持有幾檔(每檔用 1/10 資金)。持股滿了,B 級訊號會標示「已滿倉,建議略過」
MAX_POSITIONS = 10

# 是否計算操作訊號(做法一/二/三)。False = 只保留原本的處置股雷達
ENABLE_SIGNALS = True

# 你的券商手續費(國泰證券:電子下單 2.8 折、每筆最低 1 元)
FEE_DISCOUNT = 0.28
MIN_FEE = 1
# App 上試算交易成本用的金額(元)
COST_EXAMPLE_AMOUNT = 10000

# ★★★ 設定區結束 ★★★
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★


import json
import time
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
HOLDINGS_JSON = ROOT / "data" / "holdings.json"  # 你在 App 按「我有買」的持股
PAST_JSON = ROOT / "data" / "past_episodes.json"  # 2026/06 以來的處置紀錄(判斷累犯用)
INST_JSON = ROOT / "data" / "inst_cache.json"    # 最近幾天的三大法人買賣超
CHIP_JSON = ROOT / "data" / "chip_cache.json"    # 融資餘額、集保大戶持股(累積才看得到變化)

TW = timezone(timedelta(hours=8))

# ---- 資料來源網址 ----
TWSE_NOTICE = "https://openapi.twse.com.tw/v1/announcement/notice"        # 上市 今日注意股
TWSE_NOTETRANS = "https://openapi.twse.com.tw/v1/announcement/notetrans"  # 上市 注意累計次數異常(快進處置)
TWSE_PUNISH = "https://openapi.twse.com.tw/v1/announcement/punish"        # 上市 處置股
TWSE_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"  # 上市 每日收盤
TPEX_DAY_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"  # 上櫃 每日收盤
TWSE_HOLIDAY = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"      # 休市日
TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"                                 # 上市 三大法人
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"                     # 上櫃 三大法人
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


def clean_name(text):
    """櫃買中心的名稱會夾帶連結,例如 '聯亞(../../company-detail.html?code=3081)',這裡把它去掉"""
    text = re.sub(r"<.*?>", "", str(text or ""))
    text = re.sub(r"\((?:\.\./|/|http)[^)]*\)", "", text)
    return text.strip()


def to_float(x):
    try:
        return float(str(x).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


# ======================================================================
# 抓資料
# ======================================================================
def load_prices():
    """今天上市+上櫃的官方收盤資料 {代號: {close, chg_pct, open, high, low, volume}}"""
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
            "open": to_float(r.get("OpeningPrice")), "high": to_float(r.get("HighestPrice")),
            "low": to_float(r.get("LowestPrice")), "volume": to_float(r.get("TradeVolume")),
        }
    rows = get_json(TPEX_DAY_ALL, "tpex_day_all") or []
    for r in rows if isinstance(rows, list) else []:
        code = str(r.get("SecuritiesCompanyCode") or r.get("Code") or "").strip()
        close, chg = to_float(r.get("Close")), to_float(r.get("Change"))
        if not code or close is None or code in out:
            continue
        prev = close - (chg or 0)
        out[code] = {
            "close": close,
            "chg_pct": round((chg or 0) / prev * 100, 2) if prev else None,
            "open": to_float(r.get("Open")), "high": to_float(r.get("High")),
            "low": to_float(r.get("Low")), "volume": to_float(r.get("TradingShares")),
        }
    return out


def load_ssf_codes():
    return load_ssf_contracts()[0]


def load_ssf_contracts():
    """回傳 (有個股期的代號 set, {代號: 小型個股期契約代號})。
    期交所清單裡同一檔股票出現兩個契約代號 = 有小型個股期(第二個是小型,例如南電 LYF / 小型南電 QSF)"""
    from collections import defaultdict
    contracts = defaultdict(list)
    rows = get_json(TAIFEX_SSF, "taifex_ssf")
    if not isinstance(rows, list) and not DEMO_DIR:   # 再試一次(換一般瀏覽器標頭)
        try:
            time.sleep(3)
            rows = requests.get(TAIFEX_SSF, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).json()
            errors[:] = [e for e in errors if not e.startswith("taifex_ssf")]
        except Exception:  # noqa: BLE001
            pass
    cache = ROOT / "data" / "ssf_cache.json"   # 期交所偶爾連不上 → 用上次成功的清單
    if isinstance(rows, list) and len(rows) > 50:
        if not DEMO_DIR:
            cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    elif cache.exists():
        rows = json.loads(cache.read_text(encoding="utf-8"))
        print("期交所個股期清單抓取失敗,改用上次的快取", flush=True)
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and re.fullmatch(r"\d{4}", str(r.get("StockCode", "")).strip()):
                contracts[str(r["StockCode"]).strip()].append(str(r.get("Contract", "")).strip())
    small = {k: v[1] for k, v in contracts.items() if len(v) > 1}
    codes = set(contracts) | set(EXTRA_SSF_CODES) | _old_ssf_codes(rows)
    return codes, small


def _old_ssf_codes(rows):
    codes = set()
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


# ======================================================================
# 操作訊號需要的資料:休市日、歷史股價、三大法人
# ======================================================================
def load_holidays():
    out = set()
    rows = get_json(TWSE_HOLIDAY, "twse_holiday") or []
    for r in rows if isinstance(rows, list) else []:
        name = str(r.get("Name") or r.get("名稱") or "")
        if "開始交易" in name or "最後交易" in name:
            continue
        d = roc_to_date(r.get("Date") or r.get("日期"))
        if d:
            out.add(d)
    return out


def load_history_prices(codes, today, official):
    """用 yfinance 抓近 8 個月日K,再用今天官方收盤資料補上/覆蓋今天這根"""
    import pandas as pd
    if DEMO_DIR or not codes:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        errors.append("沒有安裝 yfinance,無法計算操作訊號")
        return {}
    syms = {}
    for code, market in codes.items():
        syms[code] = "^TWII" if code == "TWII" else (f"{code}.TW" if market == "上市" else f"{code}.TWO")
    out = {}
    try:
        raw = yf.download(list(syms.values()), period="8mo", interval="1d", group_by="ticker",
                          auto_adjust=False, progress=False, threads=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"歷史股價抓取失敗:{type(e).__name__}")
        return {}
    for code, sym in syms.items():
        try:
            d = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            d = d[["Open", "High", "Low", "Close", "Volume"]].dropna()
            d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
            o = official.get(code)
            if o and o.get("close") and o.get("open") and o.get("high") and o.get("low"):
                ts = pd.Timestamp(today)
                d.loc[ts] = [o["open"], o["high"], o["low"], o["close"], o.get("volume") or 0]
                d = d.sort_index()
            if code != "TWII":
                d = d[d["Volume"] > 0]
            if len(d) > 30:
                out[code] = d
        except Exception:  # noqa: BLE001
            pass
    return out


def _num(x):
    try:
        return int(str(x).replace(",", "").strip() or 0)
    except ValueError:
        return 0


def load_inst(cal, today):
    """最近 12 個交易日的三大法人買賣超(有快取,每天只補抓缺的)。{日期: {代號: [外資, 投信, 三大法人]}}"""
    import time as _t
    cache = json.loads(INST_JSON.read_text(encoding="utf-8")) if INST_JSON.exists() else {}
    if DEMO_DIR:
        return cache
    days, d = [], today
    while len(days) < 12:
        if cal.is_trading(d):
            days.append(d)
        d -= timedelta(days=1)
    for d in sorted(days):
        k = d.isoformat()
        if k in cache:
            continue
        day = {}
        js = get_json(TWSE_T86, "twse_t86", params={"date": f"{d:%Y%m%d}", "selectType": "ALLBUT0999", "response": "json"})
        tp = get_json(TPEX_INST, "tpex_inst", params={"type": "Daily", "sect": "EW", "date": f"{d:%Y/%m/%d}", "response": "json"})
        ok = False
        if isinstance(js, dict) and js.get("stat") == "OK":
            f = js.get("fields") or []
            try:
                i_fi = next(i for i, x in enumerate(f) if "外陸資買賣超" in x)
                i_it = next(i for i, x in enumerate(f) if x.startswith("投信買賣超"))
                i_tot = next(i for i, x in enumerate(f) if "三大法人" in x)
                for r in js.get("data") or []:
                    c = str(r[0]).strip()
                    if re.fullmatch(r"\d{4}", c):
                        day[c] = [_num(r[i_fi]), _num(r[i_it]), _num(r[i_tot])]
                ok = True
            except StopIteration:
                pass
        if isinstance(tp, dict):
            for r in ((tp.get("tables") or [{}])[0].get("data") or []):
                c = str(r[0]).strip()
                if re.fullmatch(r"\d{4}", c) and len(r) > 23:
                    day[c] = [_num(r[4]), _num(r[13]), _num(r[23])]
                    ok = True
        if ok and day:
            cache[k] = day
        _t.sleep(2)
    keep = sorted(cache)[-15:]
    cache = {k: cache[k] for k in keep}
    INST_JSON.parent.mkdir(parents=True, exist_ok=True)
    INST_JSON.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def load_holdings():
    if HOLDINGS_JSON.exists():
        try:
            return json.loads(HOLDINGS_JSON.read_text(encoding="utf-8")).get("holdings", [])
        except Exception:  # noqa: BLE001
            errors.append("holdings.json 格式錯誤")
    return []


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
                "code": code, "name": clean_name(cell(i_name)), "market": "上櫃",
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
    ssf, small = load_ssf_contracts()
    hist = load_history()

    # 把今天抓到的處置紀錄存進歷史(證交所只列最近公告,存起來才知道誰還在關、誰剛出關)
    for d in load_twse_disposal() + load_tpex_disposal(today):
        hist["disposals"][f"{d['code']}_{d['start']}"] = d
    # 刪掉 60 天前就結束的舊紀錄,避免檔案越來越大
    cutoff = today - timedelta(days=120)   # 留 120 天,用來判斷「累犯」
    hist["disposals"] = {k: v for k, v in hist["disposals"].items()
                         if date.fromisoformat(v["end"]) >= cutoff}

    def enrich(item):
        p = prices.get(item["code"], {})
        item["close"] = p.get("close")
        item["chg_pct"] = p.get("chg_pct")
        item["watch"] = item["code"] in WATCHLIST
        item["ssf"] = item["code"] in ssf
        item["small"] = small.get(item["code"])
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

    # ---------------- 操作訊號 ----------------
    signals, holdings_st, levels = [], [], {}
    if ENABLE_SIGNALS:
        import strategy as S
        cal = S.Calendar(load_holidays())
        past = json.loads(PAST_JSON.read_text(encoding="utf-8")) if PAST_JSON.exists() else []
        episodes = S.merge_episodes(list(hist["disposals"].values()), cal, past)
        holdings = load_holdings()
        need = {e["code"]: e["market"] for e in episodes if e["end"] >= today - timedelta(days=10)}
        for h in holdings:
            need.setdefault(h["code"], h.get("market") or "上市")
        need["TWII"] = "上市"
        hp = load_history_prices(need, today, prices)
        inst = load_inst(cal, today)
        signaled = hist.setdefault("b_signaled", {})
        signals, lv = S.build_signals(today, cal, episodes, hp, inst, small, signaled, ssf, hp.get("TWII"))
        # 處置中/快出關卡片:第幾天、初犯/累犯、明天在處置統計表的哪一列
        epmap = {e["code"]: e for e in episodes if e["start"] <= cal.next(today) and today <= e["release"]}
        n1 = cal.next(today)
        for lst in (in_disposal, releasing):
            for x in lst:
                e = epmap.get(x["code"])
                if e:
                    x["recid"] = e["recid"]
                    x["total_days"] = e["total_days"]
                    x["day_no"] = cal.count(e["start"], today) if today >= e["start"] else 0
                    x["tomorrow_label"] = S.day_label(e, n1, cal)
                    x["regime"] = "新制(5天)" if e["new_regime"] else "舊制(10天)"
        holdings_st = [S.holding_status(h, today, cal, episodes, hp) for h in holdings]
        holdings_st.sort(key=lambda x: (x["level"] != "red", x["code"]))
        held = {h["code"] for h in holdings}
        for x in signals:
            x["held"] = x["code"] in held
            if len(holdings) >= MAX_POSITIONS and x["tier"] == "B":
                x["full"] = True
        levels = {c: {"close": v["close"], "ma20": round(v["ma20"], 2), "stop_next": round(v["stop_next"], 2),
                      "release": str(v["release"])} for c, v in lv.items()}
        for h in holdings_st:
            if h["code"] not in levels and h.get("stop_next"):
                levels[h["code"]] = {"stop_next": h["stop_next"], "release": h["release"], "close": h["close"]}
        # ---- 委買委賣價差 + 籌碼
        import market_extra as MX
        cm = {x["code"]: x["market"] for x in signals}
        for h in holdings:
            cm.setdefault(h["code"], h.get("market") or "上市")
        q = MX.quotes(cm) if (cm and not DEMO_DIR) else {}
        chip_cache = MX.update_chip_cache(CHIP_JSON, today, get_json) if not DEMO_DIR else {}
        for x in signals + holdings_st:
            x["quote"] = q.get(x["code"])
            x["spread_text"] = MX.spread_verdict(q.get(x["code"]))
            x["chips"] = MX.chips_for(x["code"], inst, chip_cache)
            x["chips_text"] = MX.chips_text(x["chips"])
        hist["b_signaled"] = {k: v for k, v in signaled.items()
                              if date.fromisoformat(k.split("_")[1]) >= today - timedelta(days=90)}

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
        "signals": signals,
        "holdings": holdings_st,
        "levels": levels,
        "max_positions": MAX_POSITIONS,
        "cost": __import__("market_extra").cost_example(COST_EXAMPLE_AMOUNT, FEE_DISCOUNT, MIN_FEE),
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
    if os.environ.get("LINE_PREFIX"):
        lines.insert(0, os.environ["LINE_PREFIX"])

    reds = [h for h in data.get("holdings", []) if h["level"] == "red"]
    if reds:
        lines += ["", f"🔴 持股要處理({len(reds)})"]
        for h in reds:
            lines.append(f"・{h['code']} {h['name']}({h.get('strategy')} {h.get('instrument')})")
            for a in h["alerts"]:
                lines.append(f"   → {a}")
    sig = data.get("signals", [])
    if sig:
        lines += ["", f"🟢 明天可進場(依優先順序,{len(sig)})"]
        for x in sig[:8]:
            side = "空" if x["side"] == "short" else "買"
            flag = "(已持有)" if x.get("held") else ("(已滿倉,建議略過)" if x.get("full") else "")
            lines.append(f"{x['rank']}. [{x['tier']}] {x['code']} {x['name']} {x['title']}・{x['instrument']}{side}{flag}")
            lines.append(f"   進場:{x['entry']}")
            if x.get("target"):
                lines.append(f"   停利:{x['target']}")
            lines.append(f"   停損:{x['stop']}")
            lines.append(f"   出場:{x['exit']}")
            if x.get("spread_text"):
                lines.append(f"   收盤價差:{x['spread_text']}")
            if x.get("chips_text"):
                lines.append(f"   籌碼:{x['chips_text']}")
        if len(sig) > 8:
            lines.append(f"…另有 {len(sig) - 8} 檔,請開 App")
    yel = [h for h in data.get("holdings", []) if h["level"] != "red"]
    if yel:
        lines += ["", f"🟡 持有中({len(yel)})"]
        for h in yel:
            extra = []
            if h.get("target"):
                extra.append(f"停利 {h['target']}")
            if h.get("stop_next"):
                extra.append(f"明日停損收盤 {h['stop_next']}")
            if h.get("release"):
                extra.append(f"出關 {h['release'][5:]}")
            lines.append(f"・{h['code']} {h['name']} " + "、".join(extra))

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
    lines.append("📱 建議從主畫面的「處置雷達」圖示開 App(金鑰存在那裡)")
    lines.append(APP_URL + "?openExternalBrowser=1")
    return "\n".join(lines)[:4900], has_signal


def send_line(text):
    token = (os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip()
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
    if SEND_LINE and not DEMO_DIR and not os.environ.get("NO_LINE") and (has_signal or SEND_WHEN_EMPTY or data["errors"]):
        send_line(text)


if __name__ == "__main__":
    main()
