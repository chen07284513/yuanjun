"""Probe Eastmoney endpoints for 主力 data: institutional holdings (quarterly) and main-force fund flow history."""
import json
import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                  "Referer": "https://data.eastmoney.com/"})
DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def dc(report, flt, sort="SECURITY_CODE"):
    try:
        j = S.get(DC, params=dict(reportName=report, columns="ALL", filter=flt, pageSize=5, pageNumber=1,
                                  sortColumns=sort, sortTypes="1", source="WEB", client="WEB"), timeout=15).json()
        res = j.get("result") or {}
        print(f"== {report} {flt}: count={res.get('count')} pages={res.get('pages')} msg={j.get('message')}")
        if res.get("data"):
            print(json.dumps(res["data"][0], ensure_ascii=False)[:1200])
    except Exception as e:  # noqa: BLE001
        print(f"== {report}: FAILED {e!r}")


for rep in ["RPT_MAINDATA_MAIN_POSITIONDETAILS", "RPT_MAIN_ORGHOLDDETAIL", "RPT_MAINDATA_MAIN_ORGHOLDDETAILS",
            "RPT_MAIN_ORGHOLD", "RPT_MAINDATA_MAIN_POSITION"]:
    dc(rep, "(ORG_TYPE=\"01\")(REPORT_DATE='2024-06-30')")
dc("RPT_F10_MAIN_ORGHOLDDETAILS", "(SECUCODE=\"600519.SH\")(REPORT_DATE='2024-06-30')", sort="ORG_TYPE")
dc("RPT_F10_MAIN_ORGHOLDDETAILS", "(REPORT_DATE='2024-06-30')", sort="SECUCODE")
dc("RPT_MAIN_ORGHOLD_TOTAL", "(REPORT_DATE='2024-06-30')")
dc("RPT_FREEHOLDERS_ANALYSIS", "(END_DATE='2024-06-30')")

# main-force fund flow history depth
try:
    j = S.get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get", params=dict(
        lmt=0, klt=101, secid="1.600519", fields1="f1,f2,f3,f7",
        fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        ut="b2884a393a59ad64002292a3e90d46a5"), timeout=15).json()
    kl = (j.get("data") or {}).get("klines") or []
    print("== fund flow daykline rows", len(kl), kl[:1], kl[-1:])
except Exception as e:  # noqa: BLE001
    print("fund flow FAILED", repr(e))
# today's main-force flow for the whole market in clist
try:
    j = S.get("https://push2.eastmoney.com/api/qt/clist/get", params=dict(
        pn=1, pz=5, po=1, np=1, fltt=2, invt=2, fid="f62", fs="m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        fields="f12,f14,f62,f184,f66,f69,f72,f75,f164,f165,f174,f175", ut="bd1d9ddb04089700cf9c27f6f7426281"), timeout=15).json()
    print("== clist fund flow", json.dumps((j.get("data") or {}).get("diff"), ensure_ascii=False)[:800])
except Exception as e:  # noqa: BLE001
    print("clist FAILED", repr(e))
