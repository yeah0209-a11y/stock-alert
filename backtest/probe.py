# 測試三大法人資料格式(只抓一天)
import json, requests
UA = {"User-Agent": "Mozilla/5.0 (personal backtest)"}
out = {}
def try_(name, url, params):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=40)
        out[name] = {"status": r.status_code, "text": r.text[:3000]}
    except Exception as e:
        out[name] = {"error": str(e)}
try_("twse_t86", "https://www.twse.com.tw/rwd/zh/fund/T86", {"date": "20261120", "selectType": "ALLBUT0999", "response": "json"})
try_("twse_t86_2024", "https://www.twse.com.tw/rwd/zh/fund/T86", {"date": "20240102", "selectType": "ALLBUT0999", "response": "json"})
try_("tpex_new", "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade", {"type": "Daily", "sect": "EW", "date": "2026/11/20", "response": "json"})
try_("tpex_new_2024", "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade", {"type": "Daily", "sect": "EW", "date": "2024/01/02", "response": "json"})
try_("tpex_old", "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php", {"l": "zh-tw", "se": "EW", "t": "D", "d": "115/11/20"})
open("backtest/results/probe.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
print(json.dumps(out, ensure_ascii=False)[:5000])
