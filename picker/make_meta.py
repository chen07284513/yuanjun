"""docs/data/meta.json: stock -> 题材概念 (for the live 连板 view to rank concepts by today's limit-ups)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shouban  # noqa: E402


def main(out=os.path.join(HERE, "..", "docs", "data", "meta.json")):
    con = shouban.load_concepts()
    names, idx, m = [], {}, {}
    for code, lst in con.items():
        for b, nm in lst:
            if b not in idx:
                idx[b] = len(names)
                names.append(nm)
            m.setdefault(code[2:], []).append(idx[b])
    # drop oversized concepts (same rule as the backtest)
    size = [0] * len(names)
    for v in m.values():
        for k in v:
            size[k] += 1
    m = {c: [k for k in v if size[k] <= shouban.CONCEPT_MAX_SIZE] for c, v in m.items()}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(c=names, m=m), f, ensure_ascii=False, separators=(",", ":"))
    print("meta", len(names), "concepts", len(m), "stocks", os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
