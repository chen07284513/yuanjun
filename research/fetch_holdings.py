"""主力 data for research:
  1) quarterly institutional holdings per stock and holder type (RPT_MAIN_ORGHOLD), 2015Q4..latest
  2) daily main-force fund flow, last ~120 trading days, every A-share (push2his fflow)
Writes research/data/holdings_*.csv.gz and fflow.csv.gz
"""
import csv
import gzip
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                  "Referer": "https://data.eastmoney.com/"})
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COLS = "SECURITY_CODE,REPORT_DATE,ORG_TYPE,HOULD_NUM,FREESHARES_RATIO,HOLDCHA,HOLDCHA_RATIO,FREE_MARKET_CAP"


def page(q, pn):
    for a in range(5):
        try:
            j = S.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params=dict(
                reportName="RPT_MAIN_ORGHOLD", columns=COLS, filter=f"(REPORT_DATE='{q}')", pageSize=500,
                pageNumber=pn, sortColumns="SECURITY_CODE,ORG_TYPE", sortTypes="1,1", source="WEB", client="WEB"),
                timeout=20).json()
            res = j.get("result") or {}
            return res.get("data") or [], int(res.get("pages") or 0)
        except Exception:  # noqa: BLE001
            time.sleep(2 * (a + 1))
    return [], 0


def quarters():
    out = []
    for y in range(2015, 2027):
        for md in ("03-31", "06-30", "09-30", "12-31"):
            d = f"{y}-{md}"
            if "2015-12-31" <= d <= "2026-09-30":
                out.append(d)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    by_year = {}
    for q in quarters():
        first, pages = page(q, 1)
        rows = list(first)
        with ThreadPoolExecutor(4) as ex:
            for data, _ in ex.map(lambda p: page(q, p), range(2, pages + 1)):
                rows += data
        by_year.setdefault(q[:4], []).extend(rows)
        print(f"holdings {q}: {len(rows)} rows, {pages} pages ({time.time() - t0:.0f}s)", flush=True)
    for y, rows in by_year.items():
        with gzip.open(os.path.join(OUT, f"holdings_{y}.csv.gz"), "wt", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS.split(","), extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    # main-force fund flow history (~120 days) for every stock
    uni, pn = [], 1
    while True:
        j = S.get("https://push2.eastmoney.com/api/qt/clist/get", params=dict(
            pn=pn, pz=100, po=1, np=1, fltt=2, invt=2, fid="f12", fs="m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            fields="f12,f13", ut="bd1d9ddb04089700cf9c27f6f7426281"), timeout=15).json()
        d = j.get("data") or {}
        diff = d.get("diff") or []
        uni += [(x["f12"], x["f13"]) for x in diff]
        if not diff or len(uni) >= int(d.get("total") or 0):
            break
        pn += 1

    def ff(item):
        code, mk = item
        for a in range(3):
            try:
                j = S.get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get", params=dict(
                    lmt=0, klt=101, secid=f"{mk}.{code}", fields1="f1,f2,f3,f7",
                    fields2="f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    ut="b2884a393a59ad64002292a3e90d46a5"), timeout=15).json()
                return code, mk, (j.get("data") or {}).get("klines") or []
            except Exception:  # noqa: BLE001
                time.sleep(1 + a)
        return code, mk, []
    n = 0
    with gzip.open(os.path.join(OUT, "fflow.csv.gz"), "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "market", "date", "main_net", "small_net", "mid_net", "large_net", "xlarge_net",
                    "main_pct", "small_pct", "mid_pct", "large_pct", "xlarge_pct"])
        with ThreadPoolExecutor(8) as ex:
            for code, mk, kl in ex.map(ff, uni):
                for line in kl:
                    p = line.split(",")
                    w.writerow([code, mk] + p[:11])
                n += 1
                if n % 1000 == 0:
                    print(f"fflow {n}/{len(uni)} ({time.time() - t0:.0f}s)", flush=True)
    print("done", time.time() - t0, flush=True)


if __name__ == "__main__":
    main()
