"""Daily run: fetch A-share data, find 援军战法 signals, track the simulated trades, write JSON for the app.

Usage:  python picker/daily.py docs/data
"""
import datetime as dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from strategy import cost_rate, find_signals, simulate_exit  # noqa: E402
import shouban  # noqa: E402

LMT = 80          # bars fetched per stock
BACKFILL = 10     # on first run, also record signals from the last N trading days
TP, H = 0.08, 5   # exit rules shared by both groups (same as the backtest)
COMMON = dict(K=2, W=20, NEAR=0.10, MIN_AMT=1e5)
GROUPS = {
    "base": dict(COMMON, DD_MIN=0.15, DD_MAX=0.40, MKT=0, FIRST_HOLD=0, VOL_SHRINK=0.0),
    "strict": dict(COMMON, DD_MIN=0.20, DD_MAX=0.50, MKT=3, FIRST_HOLD=1, VOL_SHRINK=0.6),
}
ENVELOPE = dict(COMMON, DD_MIN=0.15, DD_MAX=0.50)
EXCLUDE = ("sh688", "sh689")  # 科创板不参与选股（北交所本来就不在股票池里）
UNIVERSE_DESC = "沪深主板+创业板（不含科创板、北交所、ST）"


def limit_of(code):
    n = code[2:]
    return 0.20 if n.startswith("688") or n.startswith("30") else 0.10


def build_panel(kl, lmt=LMT):
    """kl: {code: (bars, name)} -> arrays aligned on a common trading calendar."""
    count = {}
    for bars, _ in kl.values():
        for b in bars:
            count[b[0]] = count.get(b[0], 0) + 1
    need = max(1, int(0.3 * len(kl)))
    cal = sorted(d for d, n in count.items() if n >= need)[-lmt:]
    pos = {d: i for i, d in enumerate(cal)}
    codes = sorted(kl)
    T, N = len(cal), len(codes)
    A = {k: np.full((T, N), np.nan, dtype=np.float32) for k in ["o", "c", "h", "l", "v", "a"]}
    first_rel = np.full(N, -10**6, dtype=np.int32)
    for j, code in enumerate(codes):
        bars = kl[code][0]
        for b in bars:
            i = pos.get(b[0])
            if i is None:
                continue
            A["o"][i, j], A["c"][i, j], A["h"][i, j], A["l"][i, j], A["v"][i, j], A["a"][i, j] = b[1:7]
        if len(bars) < lmt and bars and bars[0][0] >= cal[0]:
            first_rel[j] = int(np.searchsorted(np.array(cal), bars[0][0]))
    chg = np.full((T, N), np.nan, dtype=np.float32)
    last = np.full(N, np.nan, dtype=np.float32)
    for t in range(T):
        ct = A["c"][t]
        chg[t] = ct / last - 1
        last = np.where(np.isnan(ct), last, ct)
    lim = np.tile(np.array([limit_of(c) for c in codes], dtype=np.float32), (T, 1))
    return cal, codes, A, chg, lim, first_rel


def market_state(cal, A, chg, lim, idx_bars):
    lu = ((chg >= lim - 0.005) & (A["c"] >= A["h"] * 0.998)).sum(1)
    f2 = (lu >= 60).astype(np.int8)
    f1 = np.zeros(len(cal), dtype=np.int8)
    idx_ok = False
    if idx_bars:
        closes = {b[0]: b[2] for b in idx_bars}
        ser = [closes.get(d) for d in sorted(closes)]
        days = sorted(closes)
        ma = {}
        for i in range(19, len(days)):
            ma[days[i]] = float(np.mean(ser[i - 19:i + 1]))
        for t, d in enumerate(cal):
            if d in closes and d in ma:
                f1[t] = int(closes[d] > ma[d])
                idx_ok = True
    ones = np.ones(len(cal), dtype=np.int8)
    return {0: ones, 1: f1, 2: f2, 3: f1 & f2}, lu, idx_ok


def signals(P, p, mkt, t_start):
    o, c, h, l, v, a = (P["A"][k] for k in ["o", "c", "h", "l", "v", "a"])
    ts, js, stops, streaks, dds = find_signals(
        o, h, l, c, P["chg"], a, P["lim"], P["first_rel"],
        p["K"], p["W"], p["DD_MIN"], p["DD_MAX"], p["NEAR"], p["MIN_AMT"], 30, t_start,
        v, mkt[p.get("MKT", 0)], p.get("FIRST_HOLD", 0), p.get("VOL_SHRINK", 0.0), 99)
    return {(int(t), int(j)): (float(s), int(k), float(d)) for t, j, s, k, d in zip(ts, js, stops, streaks, dds)}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def r(x, n=4):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), n)


def market_mode(now):
    """'morning' before 09:50 (竞价检查), 'intraday' 09:50-15:00 on weekdays, else 'close'."""
    hm = now.hour * 100 + now.minute
    if now.weekday() < 5 and 900 <= hm < 950:
        return "morning"
    return "intraday" if now.weekday() < 5 and 950 <= hm < 1500 else "close"


def adj_factor(tr, A, t0, j, tpos):
    """Prices are 前复权: if an ex-dividend happened after the signal, old prices get rescaled.
    Return k so that (price stored at signal time) * k = price on today's scale."""
    d0 = tpos.get(tr.get("d0"))
    if d0 is not None and tr.get("stop"):
        v = float(A["l"][d0, j])
        if v > 0:
            return v / tr["stop"]
    return float(A["c"][t0, j]) / tr["entry"]  # older records: entry was that day's close


def run(fetcher, out_dir, log=print, now=None, mode=None):
    now = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    mode = mode or market_mode(now)
    os.makedirs(out_dir, exist_ok=True)
    tpath = os.path.join(out_dir, "trades.json")
    book = load_json(tpath, {"meta": {}, "trades": {}})
    names = load_json(os.path.join(HERE, "names.json"), {})

    uni, usrc = fetcher.universe()
    if uni:
        names.update({c: n for c, n in uni})
        codes = [c for c, _ in uni]
    else:
        codes = load_json(os.path.join(HERE, "codes.json"), [])
        usrc = "cached list"
    codes = [c for c in codes if not c.startswith(EXCLUDE)]
    log(f"universe {len(codes)} from {usrc}")

    kl = fetcher.klines(codes)
    for c, (_, n) in kl.items():
        if n:
            names[c] = n
    coverage = len(kl) / max(1, len(codes))
    # ST / delisting names stay in the panel (sector and market statistics, as in the backtest)
    # but are never picked
    st_codes = {c for c in kl if "ST" in names.get(c, "") or "退" in names.get(c, "")}
    try:
        idx_bars = fetcher.index_kline()
    except Exception as e:  # noqa: BLE001
        log(f"index failed {e!r}")
        idx_bars = []

    if len(kl) < 50:
        latest = dict(asof=book["meta"].get("last_date"), generated=now.strftime("%Y-%m-%d %H:%M"),
                      status="fail", coverage=r(coverage, 3), stocks=len(kl),
                      notes=["今天没取到行情数据（数据源连不上），模拟盘沿用上次结果。"],
                      sources=getattr(fetcher, "used", {}), market={}, today=[])
        old = load_json(os.path.join(out_dir, "latest.json"), {})
        if old.get("asof"):
            latest.update(market=old.get("market", {}), today=old.get("today", []), asof=old["asof"])
        with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, separators=(",", ":"))
        log("too little data - wrote failure status")
        return latest, book

    cal, pcodes, A, chg, lim, first_rel = build_panel(kl)
    P = dict(A=A, chg=chg, lim=lim, first_rel=first_rel)
    T = len(cal)
    asof = cal[-1]
    mkt, lu, idx_ok = market_state(cal, A, chg, lim, idx_bars)
    jpos = {c: j for j, c in enumerate(pcodes)}
    tpos = {d: i for i, d in enumerate(cal)}

    status = "ok"
    notes = []
    if coverage < 0.8:
        status = "partial"
        notes.append(f"只取到 {coverage:.0%} 的股票数据，今天不生成新信号，只更新模拟盘。")
    if not idx_ok:
        notes.append("指数数据缺失，严格版的“大盘在20日线上”条件今天无法判断，严格版暂停。")

    last_date = book["meta"].get("last_date")
    t_start = T - BACKFILL
    if last_date and last_date in tpos:
        t_start = max(t_start, tpos[last_date] + 1)
    new_keys = []
    if status == "ok" and t_start < T:
        base = signals(P, GROUPS["base"], mkt, t_start)
        strict = signals(P, GROUPS["strict"], mkt, t_start) if idx_ok else {}
        env_first = signals(P, dict(ENVELOPE, FIRST_HOLD=1), mkt, t_start)
        env_vol = signals(P, dict(ENVELOPE, VOL_SHRINK=0.6), mkt, t_start)
        for key in sorted(set(base) | set(strict)):
            t, j = key
            stop, streak, dd = (base.get(key) or strict.get(key))
            code = pcodes[j]
            k = f"{code}|{cal[t]}"
            if k in book["trades"] or code in st_codes:
                continue
            entry = float(A["c"][t, j])  # intraday: the latest price at run time; after close: the close
            backfill = t < T - 1 and not last_date
            src = "补录" if backfill else ("盘中" if mode == "intraday" and t == T - 1 else "收盘")
            book["trades"][k] = dict(
                code=code, name=names.get(code, ""), date=cal[t], entry=r(entry, 3), stop=r(stop, 3),
                d0=cal[t - 2], src=src, entry_time=now.strftime("%H:%M") if src == "盘中" else None,
                tp=r(entry * (1 + TP), 3), streak=streak, dd=r(dd),
                amount_yi=r(A["a"][t, j] / 1e5, 2),
                groups=[g for g, s in (("base", base), ("strict", strict)) if key in s],
                badges=dict(emotion=bool(mkt[3][t]), first_hold=key in env_first, vol_shrink=key in env_vol),
                backfill=backfill, status="open")
            new_keys.append(k)
        book["meta"]["last_date"] = asof
    log(f"new signals {len(new_keys)}")

    # ---- track every unfinished trade with today's bars
    for k, tr in book["trades"].items():
        if tr["status"] == "closed":
            continue
        t0, j = tpos.get(tr["date"]), jpos.get(tr["code"])
        if t0 is None or j is None:
            tr["stale"] = True
            continue
        k_adj = adj_factor(tr, A, t0, j, tpos)
        entry_now = tr["entry"] * k_adj
        stop_now = tr["stop"] * k_adj
        te, px, why = simulate_exit(A["o"], A["h"], A["l"], A["c"], chg, lim, t0, j, stop_now, TP, H, entry_now)
        held = int(np.sum(~np.isnan(A["c"][t0 + 1:te + 1, j]))) if te > t0 else 0
        tr["held"] = held
        tr.pop("stale", None)
        if why == 3:
            tr.update(status="open", last=r(px / k_adj, 3), last_date=cal[te],
                      ret=r(px / entry_now - 1))
        else:
            tr.update(status="closed", exit_date=cal[te], exit=r(px / k_adj, 3),
                      reason={0: "到期", 1: "止损", 2: "止盈"}[why],
                      ret=r(px / entry_now - 1 - cost_rate(cal[te])))

    with open(tpath, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, separators=(",", ":"))
    if mode == "close" and status == "ok":
        try:
            P2 = shouban.Panel(cal, pcodes, A, chg, lim, first_rel, mkt[1], shouban.load_industry())
            shouban.run_close(P2, names, st_codes, out_dir, now, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"首板 failed: {e!r}")
            if os.environ.get("STRICT"):
                raise
    with open(os.path.join(HERE, "names.json"), "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, separators=(",", ":"))
    latest = dict(
        asof=asof, generated=now.strftime("%Y-%m-%d %H:%M"), mode=mode, status=status, notes=notes,
        coverage=r(coverage, 3), stocks=len(kl), universe_source=usrc, universe=UNIVERSE_DESC,
        sources=getattr(fetcher, "used", {}),
        market=dict(limit_ups=int(lu[-1]), index_above_ma20=bool(mkt[1][-1]) if idx_ok else None,
                    emotion_ok=bool(mkt[3][-1]) if idx_ok else None),
        today=[k for k, tr in book["trades"].items() if tr["date"] == asof],
        rules=dict(groups=GROUPS, TP=TP, H=H))
    with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, separators=(",", ":"))
    log(f"asof {asof} status {status} today {len(latest['today'])} trades {len(book['trades'])}")
    return latest, book


if __name__ == "__main__":
    from fetch import LiveFetcher
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "docs", "data")
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    if market_mode(now) == "morning":
        from fetch import quotes
        shouban.run_morning(out, quotes, now)
        sys.exit(0)
    # 盘中那次必须在 15:00 前出结果：最多抓 7 分钟数据
    fetcher = LiveFetcher(deadline=420 if market_mode(now) == "intraday" else 1200)
    latest, _ = run(fetcher, out, now=now)
    if latest["status"] != "ok" and latest["coverage"] < 0.3:
        sys.exit(1)
