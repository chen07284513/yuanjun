"""Weekly refresh of the industry (东方财富二级行业) membership used by the 首板 rules.

Writes picker/industry_L2.csv.gz. Every stock belongs to 3 nested industry boards; the middle one
(level 2, ~128 boards) is the one the backtest used.
"""
import csv
import gzip
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "research"))
sys.path.insert(0, HERE)
from fetch_boards import clist  # noqa: E402


def main():
    boards = clist("m:90+t:2", "f12,f14")
    rows = []
    for b in boards:
        for m in clist(f"b:{b['f12']}+f:!50", "f12,f13,f14"):
            rows.append((b["f12"], b["f14"], str(m["f12"]), str(m["f13"]), m["f14"]))
    size = {}
    for b, *_ in rows:
        size[b] = size.get(b, 0) + 1
    per = {}
    for i, (b, bn, c, mk, nm) in enumerate(rows):
        per.setdefault(c, []).append((-size[b], i, b, bn, mk, nm))
    out = []
    for c, lst in per.items():
        lst.sort()
        if len(lst) >= 2:
            _, _, b, bn, mk, nm = lst[1]
            out.append(dict(board=b, board_name=bn, code=c, market=mk, name=nm))
    n_boards = len({o["board"] for o in out})
    print(f"industry boards {len(boards)}, stocks {len(out)}, level-2 boards {n_boards}")
    if len(out) < 3000 or not (80 <= n_boards <= 200):
        print("result looks wrong - keeping the old file")
        sys.exit(1)
    with gzip.open(os.path.join(HERE, "industry_L2.csv.gz"), "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["board", "board_name", "code", "market", "name"])
        w.writeheader()
        w.writerows(out)
    # 概念板块（去掉指数成分、资金持仓、价格形态等非题材分类）
    from shouban import CONCEPT_EXCL, CONCEPT_MAX_SIZE
    con = []
    for b in clist("m:90+t:3", "f12,f14"):
        if CONCEPT_EXCL.search(b["f14"]):
            continue
        mem = clist(f"b:{b['f12']}+f:!50", "f12,f13,f14")
        if len(mem) > CONCEPT_MAX_SIZE:
            continue
        con += [dict(board=b["f12"], board_name=b["f14"], code=str(m["f12"]), market=str(m["f13"]), name=m["f14"]) for m in mem]
    n_con = len({c["board"] for c in con})
    print(f"concept boards kept {n_con}, rows {len(con)}")
    if n_con >= 200:
        with gzip.open(os.path.join(HERE, "concept.csv.gz"), "wt", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["board", "board_name", "code", "market", "name"])
            w.writeheader()
            w.writerows(con)


if __name__ == "__main__":
    main()
