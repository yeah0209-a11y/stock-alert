import json, requests
UA = {"User-Agent": "Mozilla/5.0 (personal use)", "Origin": "https://yeah0209-a11y.github.io"}
out = {}
def try_(name, url, params=None):
    try:
        r = requests.get(url, params=params, headers=UA, timeout=40)
        out[name] = {"status": r.status_code, "cors": r.headers.get("Access-Control-Allow-Origin"), "text": r.text.strip()[:1800]}
    except Exception as e:
        out[name] = {"error": str(e)}
try_("mis_odd_otc", "https://mis.twse.com.tw/stock/api/getOddInfo.jsp", {"ex_ch": "otc_6140.tw|tse_2305.tw", "json": "1"})
try_("mis_stock", "https://mis.twse.com.tw/stock/api/getStockInfo.jsp", {"ex_ch": "otc_6140.tw", "json": "1"})
r = requests.get("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", params={"date": "20261120", "type": "ALLBUT0999", "response": "json"}, headers=UA, timeout=60).json()
out["mi_index_tables"] = [(i, t.get("title"), t.get("fields")) for i, t in enumerate(r.get("tables", []))]
out["mi_index_row"] = [t.get("data", [[]])[:2] for t in r.get("tables", []) if t.get("fields") and "最後揭示買價" in t.get("fields")]
open("backtest/results/probe.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
