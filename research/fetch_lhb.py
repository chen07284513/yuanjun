"""Download 龙虎榜 history from Eastmoney's data center (runs on GitHub Actions).

python research/fetch_lhb.py test 2024-01-01 2024-01-10   -> prints field names / sample rows
python research/fetch_lhb.py full 2017-01-01 2026-12-31   -> writes research/data/*.csv.gz per year
"""
import csv
import datetime as dt
import gzip
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                  "Referer": "https://data.eastmoney.com/stock/lhb.html"})
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def get(report, flt, sort="SECURITY_CODE", page_size=500, columns="ALL"):
    rows, page = [], 1
    while True:
        params = dict(sortColumns=sort, sortTypes="1", pageSize=page_size, pageNumber=page,
                      reportName=report, columns=columns, source="WEB", client="WEB", filter=flt)
        for attempt in range(4):
            try:
                r = S.get(URL, params=params, timeout=15)
                j = r.json()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        res = j.get("result") or {}
        data = res.get("data") or []
        rows += data
        pages = int(res.get("pages") or 1)
        if page >= pages or not data:
            return rows, j.get("message")
        page += 1


def months(a, b):
    d = dt.date.fromisoformat(a).replace(day=1)
    end = dt.date.fromisoformat(b)
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield d.isoformat(), (nxt - dt.timedelta(days=1)).isoformat()
        d = nxt


def save(rows, path):
    if not rows:
        return
    keys = sorted({k for r in rows for k in r})
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def day_seats(day):
    out = {}
    for side, rep in (("buy", "RPT_BILLBOARD_DAILYDETAILSBUY"), ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL")):
        try:
            rows, _ = get(rep, f"(TRADE_DATE='{day}')", sort="SECURITY_CODE")
        except Exception as e:  # noqa: BLE001
            print("seat fetch failed", day, side, repr(e), flush=True)
            rows = None
        out[side] = rows
    return day, out


def main():
    mode, a, b = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(OUT, exist_ok=True)
    if mode == "test":
        for rep, flt in [("RPT_DAILYBILLBOARD_DETAILSNEW", f"(TRADE_DATE>='{a}')(TRADE_DATE<='{b}')"),
                         ("RPT_ORGANIZATION_TRADE_DETAILS", f"(TRADE_DATE>='{a}')(TRADE_DATE<='{b}')"),
                         ("RPT_BILLBOARD_DAILYDETAILSBUY", f"(TRADE_DATE='{a}')"),
                         ("RPT_BILLBOARD_DAILYDETAILSSELL", f"(TRADE_DATE='{a}')")]:
            try:
                rows, msg = get(rep, flt)
                print(f"== {rep}: {len(rows)} rows msg={msg}", flush=True)
                if rows:
                    print(json.dumps(rows[0], ensure_ascii=False)[:1500], flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"== {rep}: FAILED {e!r}", flush=True)
        return

    t0 = time.time()
    by_year = {}
    org_year = {}
    for m0, m1 in months(a, b):
        flt = f"(TRADE_DATE>='{m0}')(TRADE_DATE<='{m1}')"
        rows, _ = get("RPT_DAILYBILLBOARD_DETAILSNEW", flt)
        by_year.setdefault(m0[:4], []).extend(rows)
        try:
            org, _ = get("RPT_ORGANIZATION_TRADE_DETAILS", flt)
            org_year.setdefault(m0[:4], []).extend(org)
        except Exception as e:  # noqa: BLE001
            print("org failed", m0, repr(e), flush=True)
        print(f"summary {m0}: {len(rows)} rows ({time.time() - t0:.0f}s)", flush=True)
    for y, rows in by_year.items():
        save(rows, os.path.join(OUT, f"lhb_summary_{y}.csv.gz"))
    for y, rows in org_year.items():
        save(rows, os.path.join(OUT, f"lhb_org_{y}.csv.gz"))

    days = sorted({str(r["TRADE_DATE"])[:10] for rows in by_year.values() for r in rows})
    print(f"trading days with LHB: {len(days)}", flush=True)
    seats = {}
    failed = []
    with ThreadPoolExecutor(4) as ex:
        for i, (day, out) in enumerate(ex.map(day_seats, days)):
            for side in ("buy", "sell"):
                if out[side] is None:
                    failed.append(day)
                    continue
                for r in out[side]:
                    r["SIDE"] = side
                seats.setdefault(day[:4], []).extend(out[side])
            if i % 50 == 0:
                print(f"seats {day} ({i + 1}/{len(days)}) {time.time() - t0:.0f}s", flush=True)
    for y, rows in seats.items():
        save(rows, os.path.join(OUT, f"lhb_seats_{y}.csv.gz"))
    print(f"done in {time.time() - t0:.0f}s, failed seat days: {sorted(set(failed))[:20]} ({len(set(failed))})",
          flush=True)


if __name__ == "__main__":
    main()
