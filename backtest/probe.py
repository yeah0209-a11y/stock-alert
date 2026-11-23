# 測試:委買委賣(整股/零股)、融資融券、集保股權分散 的資料格式
import json, requests
UA = {"User-Agent": "Mozilla/5.0 (personal use)"}
out = {}
def try_(name, url, params=None, raw=False):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=40)
        out[name] = {"status": r.status_code, "text": r.text[:2500]}
    except Exception as e:
        out[name] = {"error": str(e)}
D = "20261120"
try_("twse_mi_index", "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", {"date": D, "type": "ALLBUT0999", "response": "json"})
try_("twse_odd_TWT53U", "https://www.twse.com.tw/rwd/zh/afterTrading/TWT53U", {"date": D, "response": "json"})
try_("tpex_close_openapi", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes")
try_("tpex_odd", "https://www.tpex.org.tw/www/zh-tw/afterTrading/oddLotIntraday", {"date": "2026/11/20", "response": "json"})
try_("tpex_odd_openapi", "https://www.tpex.org.tw/openapi/v1/tpex_odd_lot_trading")
try_("twse_margin", "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN", {"date": D, "selectType": "STOCK", "response": "json"})
try_("tpex_margin", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance")
try_("tdcc_1_5", "https://opendata.tdcc.com.tw/getOD.ashx", {"id": "1-5"})
try_("tdcc_openapi", "https://openapi.tdcc.com.tw/v1/opendata/1-5")
try_("mis_realtime", "https://mis.twse.com.tw/stock/api/getStockInfo.jsp", {"ex_ch": "tse_2330.tw|otc_6140.tw", "json": "1", "delay": "0"})
try_("mis_odd", "https://mis.twse.com.tw/stock/api/getOddInfo.jsp", {"ex_ch": "tse_2330.tw", "json": "1"})
open("backtest/results/probe.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
