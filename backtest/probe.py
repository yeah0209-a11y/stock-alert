# 測試期交所個股期清單格式(找出小型個股期)
import json, requests
UA = {"User-Agent": "Mozilla/5.0 (personal backtest)"}
out = {}
for name, url in [("ssf", "https://openapi.taifex.com.tw/v1/SSFLists"),
                  ("swagger", "https://openapi.taifex.com.tw/swagger.json")]:
    try:
        r = requests.get(url, headers=UA, timeout=40)
        js = r.json()
        if name == "ssf":
            out[name] = {"n": len(js), "first": js[:5], "keys": list(js[0].keys()) if js else []}
            out["ssf_small"] = [x for x in js if any("小型" in str(v) or "100" == str(v).strip() for v in x.values())][:20]
            out["ssf_all"] = js
        else:
            out[name] = [p for p in js.get("paths", {}) if "SSF" in p or "Stock" in p or "Future" in p][:80]
    except Exception as e:
        out[name] = {"error": str(e)}
open("backtest/results/probe.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
