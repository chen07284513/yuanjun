"""机构持仓 snapshot (专业机构 = 基金/QFII/社保/券商/保险/信托) from Eastmoney RPT_MAIN_ORGHOLD.

Uses the latest quarter that has been fully published (same lag as the backtest):
Q1 from 05-01, Q2 from 09-01, Q3 from 11-01, Q4 from 05-01 of the next year.
python picker/holdings.py  -> picker/holdings.csv.gz
"""
import csv
import datetime as dt
import gzip
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PRO = {"01", "02", "03", "04", "05", "06"}
COLS = "SECURITY_CODE,REPORT_DATE,ORG_TYPE,HOULD_NUM,FREESHARES_RATIO,HOLDCHA,HOLDCHA_RATIO"


def avail(q):
    y, md = q[:4], q[5:]
    return {"03-31": f"{y}-05-01", "06-30": f"{y}-09-01", "09-30": f"{y}-11-01", "12-31": f"{int(y) + 1}-05-01"}[md]


def usable_quarters(today):
    qs = []
    for y in range(today.year - 2, today.year + 1):
        for md in ("03-31", "06-30", "09-30", "12-31"):
            q = f"{y}-{md}"
            if avail(q) <= today.isoformat():
                qs.append(q)
    return sorted(qs)[-2:]  # latest usable + the one before (for 增减)


def aggregate(rows):
    """rows of RPT_MAIN_ORGHOLD for ONE quarter -> {code6: dict}"""
    out = {}
    for r in rows:
        c = str(r["SECURITY_CODE"]).zfill(6)
        d = out.setdefault(c, dict(pro_n=0, pro_ratio=0.0, fund_n=0, fund_ratio=0.0, fund_cha=""))
        t = str(r["ORG_TYPE"]).zfill(2)
        if t in PRO:
            d["pro_n"] += int(float(r.get("HOULD_NUM") or 0))
            d["pro_ratio"] += float(r.get("FREESHARES_RATIO") or 0)
        if t == "01":
            d["fund_n"] = int(float(r.get("HOULD_NUM") or 0))
            d["fund_ratio"] = float(r.get("FREESHARES_RATIO") or 0)
            d["fund_cha"] = r.get("HOLDCHA") or ""
    return out


def fetch_quarter(q):
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Referer": "https://data.eastmoney.com/"})
    rows, pn, pages = [], 1, 1
    while pn <= pages:
        for a in range(4):
            try:
                j = s.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params=dict(
                    reportName="RPT_MAIN_ORGHOLD", columns=COLS, filter=f"(REPORT_DATE='{q}')", pageSize=500, pageNumber=pn,
                    sortColumns="SECURITY_CODE,ORG_TYPE", sortTypes="1,1", source="WEB", client="WEB"), timeout=20).json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(2 * (a + 1))
        res = j.get("result") or {}
        rows += res.get("data") or []
        pages = int(res.get("pages") or 0)
        pn += 1
    return rows


def write(cur, prev, q, path=os.path.join(HERE, "holdings.csv.gz")):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "quarter", "pro_n", "pro_ratio", "pro_chg", "fund_n", "fund_ratio", "fund_cha"])
        for c, d in sorted(cur.items()):
            p = prev.get(c)
            chg = round(d["pro_ratio"] - p["pro_ratio"], 3) if p else ""
            w.writerow([c, q, d["pro_n"], round(d["pro_ratio"], 3), chg, d["fund_n"], round(d["fund_ratio"], 3), d["fund_cha"]])


def load(path=os.path.join(HERE, "holdings.csv.gz")):
    out = {}
    if not os.path.exists(path):
        return out
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["code"]] = r
    return out


if __name__ == "__main__":
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    qp, qc = usable_quarters(today)
    cur, prev = aggregate(fetch_quarter(qc)), aggregate(fetch_quarter(qp))
    print("holdings", qc, len(cur), "prev", qp, len(prev))
    if len(cur) < 3000:
        raise SystemExit("too few rows - keep old file")
    write(cur, prev, qc)
