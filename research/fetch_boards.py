"""Snapshot of Eastmoney 行业 (industry) and 概念 (concept) board membership.

python research/fetch_boards.py   -> research/data/boards_industry.csv.gz, boards_concept.csv.gz
"""
import csv
import gzip
import json
import os
import time

import requests

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                  "Referer": "https://quote.eastmoney.com/center/boardlist.html"})
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def clist(fs, fields):
    rows, pn = [], 1
    while True:
        for attempt in range(4):
            try:
                r = S.get("https://push2.eastmoney.com/api/qt/clist/get", params=dict(
                    pn=pn, pz=100, po=1, np=1, fltt=2, invt=2, fid="f12", fs=fs, fields=fields,
                    ut="bd1d9ddb04089700cf9c27f6f7426281"), timeout=15)
                d = (r.json().get("data") or {})
                break
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        diff = d.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        rows += diff
        if not diff or len(rows) >= int(d.get("total") or 0):
            return rows
        pn += 1
        time.sleep(0.1)


def main():
    os.makedirs(OUT, exist_ok=True)
    for kind, fs in (("industry", "m:90+t:2"), ("concept", "m:90+t:3")):
        boards = clist(fs, "f12,f14")
        print(kind, "boards", len(boards), flush=True)
        out = []
        for i, b in enumerate(boards):
            try:
                mem = clist(f"b:{b['f12']}+f:!50", "f12,f13,f14")
            except Exception as e:  # noqa: BLE001
                print("  failed", b, repr(e), flush=True)
                continue
            for m in mem:
                out.append(dict(board=b["f12"], board_name=b["f14"], code=m["f12"], market=m["f13"], name=m["f14"]))
            if i % 50 == 0:
                print(f"  {i}/{len(boards)} rows={len(out)}", flush=True)
        with gzip.open(os.path.join(OUT, f"boards_{kind}.csv.gz"), "wt", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["board", "board_name", "code", "market", "name"])
            w.writeheader()
            w.writerows(out)
        print(kind, "members", len(out), flush=True)


if __name__ == "__main__":
    main()
