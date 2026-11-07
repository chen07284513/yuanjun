"""援军战法 (Reinforcement / rebound-after-leader-pullback) — fixed-rule implementation.

Rules (signal evaluated at close of day t = "D2"):
  1. 龙头: within the last W trading days the stock closed limit-up on >= K consecutive days.
  2. 主升浪见顶: the highest high since that streak (peak) is at least 3 days before t.
  3. 回调: the stock kept making new lows; D0 = t-2 is the lowest low since the peak, and it
     was itself a new low (low[D0] < low[D0-1]).  撤军线 (stop line) = low[D0].
  4. 不创新低: D1 = t-1 and D2 = t both keep their low above the stop line.
  5. Pullback depth from peak to stop line between DD_MIN and DD_MAX.
  6. D2 closes as a 阳线 (close > open), not at limit-up (must be buyable), and the close is
     within NEAR of the stop line (撤军线附近), so the risk per trade is bounded.
  7. Liquidity: turnover on D2 >= MIN_AMOUNT.
Trade: buy at D2 close.  Exit: stop if price breaks the stop line; take profit at +TP;
otherwise sell at the close of the H-th trading day.  Limit-down locked days cannot sell.
"""
import numpy as np
from numba import njit


def limit_pct(codes, dates):
    """Daily price-limit fraction per (date, stock)."""
    T, N = len(dates), len(codes)
    lim = np.full((T, N), 0.10, dtype=np.float32)
    d = np.array(dates)
    cyb_switch = np.searchsorted(d, "2020-08-24")
    for j, c in enumerate(codes):
        num = c[2:]
        if num.startswith("688"):
            lim[:, j] = 0.20
        elif num.startswith("30"):
            lim[cyb_switch:, j] = 0.20
    return lim


@njit(cache=True)
def find_signals(o, h, l, c, chg, amt, lim, first_rel,
                 K, W, DD_MIN, DD_MAX, NEAR, MIN_AMT, MIN_AGE, t_start,
                 vol, mkt_ok, FIRST_HOLD, VOL_SHRINK, MAX_STREAK):
    T, N = c.shape
    out_t = []
    out_j = []
    out_stop = []
    out_streak = []
    out_dd = []
    for j in range(N):
        streak = 0
        last_ev = -10**6     # last day where streak >= K
        best_streak = 0      # streak height of that event
        for t in range(T):
            ct = c[t, j]
            if np.isnan(ct):
                streak = 0
                continue
            lu = (chg[t, j] >= lim[t, j] - 0.005) and (ct >= h[t, j] * 0.998)
            if lu:
                streak += 1
            else:
                streak = 0
            if streak >= K:
                if t - last_ev > W:
                    best_streak = streak
                else:
                    best_streak = max(best_streak, streak)
                last_ev = t
            if t < t_start or t < 5:
                continue
            if t - last_ev > W or t - last_ev < 3:
                continue
            if t - first_rel[j] < MIN_AGE:
                continue
            # peak since the start of the event streak window
            s = last_ev - best_streak + 1
            if s < 0:
                s = 0
            pk = -1.0
            pidx = -1
            for k in range(s, t + 1):
                hk = h[k, j]
                if not np.isnan(hk) and hk >= pk:
                    pk = hk
                    pidx = k
            if pidx > t - 3:
                continue
            d0 = t - 2
            d1 = t - 1
            if np.isnan(l[d0, j]) or np.isnan(l[d1, j]) or np.isnan(l[d0 - 1, j]):
                continue
            # D0 is the lowest low since peak and a fresh new low
            mn = 1e30
            for k in range(pidx, t + 1):
                lk = l[k, j]
                if not np.isnan(lk) and lk < mn:
                    mn = lk
            stop = l[d0, j]
            if stop > mn + 1e-9:
                continue
            if not (stop < l[d0 - 1, j]):
                continue
            if not (l[d1, j] > stop and l[t, j] > stop):
                continue
            dd = 1.0 - stop / pk
            if dd < DD_MIN or dd > DD_MAX:
                continue
            if not (ct > o[t, j]):
                continue
            if chg[t, j] >= lim[t, j] - 0.005:
                continue
            if ct > stop * (1.0 + NEAR):
                continue
            if amt[t, j] < MIN_AMT:
                continue
            if best_streak > MAX_STREAK:
                continue
            if mkt_ok[t] == 0:
                continue
            if FIRST_HOLD > 0:
                # 首板不破: stop line must stay above the open of the first board of the streak
                if stop < o[s, j]:
                    continue
            if VOL_SHRINK > 0:
                vs = 0.0
                ns = 0
                for k in range(s, s + best_streak):
                    if k < T and not np.isnan(vol[k, j]):
                        vs += vol[k, j]
                        ns += 1
                vp = (vol[d0, j] + vol[d1, j] + vol[t, j]) / 3.0
                if ns == 0 or vp > VOL_SHRINK * vs / ns:
                    continue
            out_t.append(t)
            out_j.append(j)
            out_stop.append(stop)
            out_streak.append(best_streak)
            out_dd.append(dd)
    return (np.array(out_t), np.array(out_j), np.array(out_stop),
            np.array(out_streak), np.array(out_dd))


@njit(cache=True)
def simulate_exit(o, h, l, c, chg, lim, t0, j, stop, TP, H, entry):
    """Return (exit_t, exit_price, reason) reason: 0=time,1=stop,2=tp,3=data end.
    entry: buy price on day t0 (the close in the backtest, the 14:4x price in the live app)."""
    T = c.shape[0]
    held = 0
    t = t0
    while True:
        t += 1
        if t >= T:
            # still open at data end: mark at last close
            k = T - 1
            while k > t0 and np.isnan(c[k, j]):
                k -= 1
            return k, c[k, j], 3
        if np.isnan(c[t, j]):
            continue  # suspended
        held += 1
        locked_down = (h[t, j] == l[t, j]) and (chg[t, j] <= -(lim[t, j] - 0.005))
        if locked_down:
            continue  # cannot sell
        if o[t, j] < stop:
            return t, o[t, j], 1
        if l[t, j] < stop:
            return t, stop * 0.999, 1
        if TP > 0:
            tgt = entry * (1.0 + TP)
            if o[t, j] >= tgt:
                return t, o[t, j], 2
            if h[t, j] >= tgt:
                return t, tgt, 2
        if held >= H:
            return t, c[t, j], 0


def cost_rate(date):
    stamp = 0.0005 if date >= "2023-08-28" else 0.001
    return 0.00025 * 2 + stamp + 0.001 * 2  # commission both sides + stamp + slippage both sides


def run(P, params, t_start):
    o, h, l, c = P["open"], P["high"], P["low"], P["close"]
    chg, amt, lim, first_rel = P["change"], P["amount"], P["lim"], P["first_rel"]
    mkt_ok = P["mkt_ok"][params.get("MKT", 0)]
    ts, js, stops, streaks, dds = find_signals(
        o, h, l, c, chg, amt, lim, first_rel,
        params["K"], params["W"], params["DD_MIN"], params["DD_MAX"], params["NEAR"],
        params["MIN_AMT"], 30, t_start, P["volume"], mkt_ok,
        params.get("FIRST_HOLD", 0), params.get("VOL_SHRINK", 0.0), params.get("MAX_STREAK", 99))
    dates = P["dates"]
    trades = []
    for t0, j, stop, stk, dd in zip(ts, js, stops, streaks, dds):
        te, px, why = simulate_exit(o, h, l, c, chg, lim, int(t0), int(j), float(stop),
                                    params["TP"], params["H"], float(c[t0, j]))
        entry = float(c[t0, j])
        gross = px / entry - 1.0
        net = gross - cost_rate(dates[te])
        trades.append((int(t0), int(j), int(te), entry, float(px), net, int(why),
                       int(stk), float(dd), float(stop), float(amt[t0, j])))
    return trades
