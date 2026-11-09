"""Probe Eastmoney 涨停池 / 昨日涨停池 / 炸板池 (JSONP form, as a browser would call it) and save fixtures."""
import datetime as dt
import json
import os
import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                                "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"})
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)
today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d")
EP = {
    "zt": ("https://push2ex.eastmoney.com/getTopicZTPool", dict(dpt="wz.ztzt", sort="fbt:asc")),
    "yzt": ("https://push2ex.eastmoney.com/getYesterdayZTPool", dict(dpt="wz.ztzt", sort="zs:desc")),
    "zb": ("https://push2ex.eastmoney.com/getTopicZBPool", dict(dpt="wz.ztzt", sort="fbt:asc")),
}
for day in [today, "20261106"]:
    for k, (url, extra) in EP.items():
        p = dict(ut="7eea3edcaed734bea9cbfc24409ed989", Pageindex=0, pagesize=500, date=day, cb="cbtest", **extra)
        try:
            r = S.get(url, params=p, timeout=15)
            txt = r.text
            print(f"== {k} {day}: http {r.status_code} len {len(txt)} head {txt[:60]!r}")
            body = txt[txt.index("(") + 1: txt.rindex(")")]
            j = json.loads(body)
            pool = (j.get("data") or {}).get("pool") or []
            print(f"   rc={j.get('rc')} pool={len(pool)} keys={list(pool[0].keys()) if pool else None}")
            if pool:
                print("   sample", json.dumps(pool[0], ensure_ascii=False)[:400])
            with open(os.path.join(OUT, f"fx_{k}_{day}.js"), "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception as e:  # noqa: BLE001
            print(f"== {k} {day}: FAILED {e!r}")
# ulist JSONP for main-force flow
r = S.get("https://push2.eastmoney.com/api/qt/ulist.np/get", params=dict(
    fltt=2, invt=2, secids="1.600519,0.000001", fields="f12,f13,f14,f2,f3,f62,f184",
    ut="bd1d9ddb04089700cf9c27f6f7426281", cb="cbu"), timeout=15)
print("== ulist", r.status_code, r.text[:300])
with open(os.path.join(OUT, "fx_ulist.js"), "w", encoding="utf-8") as f:
    f.write(r.text)
