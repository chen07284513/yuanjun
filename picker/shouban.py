"""首板 + 板块 strategy: features, score (推荐指数), sector analysis, morning auction check, paper trading.

Rule A (validated 2016-2026):  首板 (limit-up today, none in the previous 5 days, not 一字, listed > 60 days)
  + stock fell over the previous 20 days  + its industry fell >= 3% over 5 days  + the industry rose >= 2% today.
Next day: skip if it opens at the limit (cannot buy) or opens > +7%; among the rest buy the lowest opens first,
at most 5 per day; sell at the close of the 3rd trading day (entry day counts as day 1).
"""
import csv
import gzip
import json
import math
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_PER_DAY, MAX_GAP, HOLD_DAYS = 5, 0.07, 3
# 概念板块里不是“题材”的分类（指数成分、资金持仓、价格形态、财报标签等）不参与分析
CONCEPT_EXCL = re.compile(r"融资融券|股通|重仓|减仓|增仓|新进|持股|罗素|标准普尔|MSCI|创业板综|创业成份|中证|深成|上证|HS300|央视|深证|"
                          r"小盘|中盘|大盘|微盘|破净|破发|破增发|低价|昨日|最近多板|连续亏损|预增|预盈|预亏|预减|转债标的|权重股|茅指数|"
                          r"高振幅|高换手|AH股|B股|GDR|注册制|次新|ST|退市|风格|增持|首亏|扭亏|股权分散|股权集中|市盈率|市净率|外资背景|超跌|百元股|红利股|高成长|价值股|热股|微利股|周期股|趋势股|行业龙头|新高|摘帽|题材股|反转股|做市|股权转让|密集调研|金股|宁组合|北交所|举牌|养老金")
CONCEPT_MAX_SIZE = 600


def cost(d):
    return 0.00025 * 2 + (0.0005 if d >= "2023-08-28" else 0.001) + 0.002


def load_model():
    with open(os.path.join(HERE, "sb_model.json"), encoding="utf-8") as f:
        return json.load(f)


def load_industry():
    m = {}
    with gzip.open(os.path.join(HERE, "industry_L2.csv.gz"), "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = ("sh" if str(r["market"]) == "1" else "sz") + str(r["code"]).zfill(6)
            m[code] = (r["board"], re.sub(r"(Ⅰ|Ⅱ|Ⅲ|III|II)$", "", r["board_name"]).strip())
    return m


def load_concepts():
    m = {}
    path = os.path.join(HERE, "concept.csv.gz")
    if not os.path.exists(path):
        return m
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if CONCEPT_EXCL.search(r["board_name"]):
                continue
            code = ("sh" if str(r["market"]) == "1" else "sz") + str(r["code"]).zfill(6)
            m.setdefault(code, []).append((r["board"], r["board_name"]))
    return m


class Panel:
    """Everything derived from the daily panel that the 首板 rules need."""

    def __init__(self, cal, codes, A, chg, lim, first_rel, idx_up, industry, concepts=None):
        self.cal, self.codes = cal, codes
        self.O, self.H, self.L, self.C, self.V, self.AMT = (A[k].astype(np.float64) for k in ["o", "h", "l", "c", "v", "a"])
        self.CHG = chg.astype(np.float64)
        self.LIM = lim.astype(np.float64)
        self.first_rel = first_rel
        self.idx_up = idx_up
        T, N = self.C.shape
        self.T, self.N = T, N
        prev = np.full_like(self.C, np.nan)
        last = np.full(N, np.nan)
        for t in range(T):
            prev[t] = last
            last = np.where(np.isnan(self.C[t]), last, self.C[t])
        self.PREV = prev
        with np.errstate(all="ignore"):
            self.LU = (self.CHG >= self.LIM - 0.005) & (self.C >= self.H * 0.998)
            touch = (self.H / prev - 1 >= self.LIM - 0.005)
            self.ZB = touch & ~self.LU
            self.LD = self.CHG <= -(self.LIM - 0.005)
        self.YIZI = self.LU & (self.H == self.L)
        recent = np.zeros((T, N), dtype=bool)
        for k in range(1, 6):
            recent[k:] |= self.LU[:-k]
        listed = (np.arange(T)[:, None] - first_rel[None, :]) > 60
        self.listed = listed
        self.SB = self.LU & ~recent & listed
        self.streak = np.zeros((T, N), dtype=np.int16)
        for t in range(1, T):
            self.streak[t] = np.where(self.LU[t], self.streak[t - 1] + 1, 0)
        self.streak[0] = self.LU[0].astype(np.int16)
        # sectors
        names, sid = [], {}
        self.sec_of = np.full(N, -1)
        for j, c in enumerate(codes):
            if c in industry:
                b, nm = industry[c]
                if b not in sid:
                    sid[b] = len(names)
                    names.append(nm)
                self.sec_of[j] = sid[b]
        self.sec_names = names
        S = len(names)
        M = np.zeros((N, S))
        ok = self.sec_of >= 0
        M[np.arange(N)[ok], self.sec_of[ok]] = 1
        valid = (~np.isnan(self.CHG)).astype(float)
        self.sec_cnt = valid @ M
        self.sec_ret = (np.nan_to_num(self.CHG) @ M) / np.maximum(self.sec_cnt, 1)
        self.sec_lu = self.LU.astype(float) @ M
        cum = np.cumsum(np.log1p(self.sec_ret), axis=0)
        self.sec_ret5 = np.full_like(self.sec_ret, np.nan)
        self.sec_ret5[5:] = np.expm1(cum[5:] - cum[:-5])
        order = np.argsort(-self.sec_ret, axis=1, kind="stable")
        self.sec_rank = np.empty_like(order)
        for t in range(T):  # 'min' ranking (ties share the best rank)
            vals = self.sec_ret[t]
            sv = -np.sort(-vals)
            self.sec_rank[t] = np.searchsorted(-sv, -vals, side="left") + 1

        # concepts
        self.con_names, cid = [], {}
        pairs = []
        for j, c in enumerate(codes):
            for b, nm in (concepts or {}).get(c, []):
                if b not in cid:
                    cid[b] = len(self.con_names)
                    self.con_names.append(nm)
                pairs.append((j, cid[b]))
        K = len(self.con_names)
        self.Mc = np.zeros((N, max(K, 1)))
        if pairs:
            jj, kk = zip(*pairs)
            self.Mc[list(jj), list(kk)] = 1
            size = self.Mc.sum(0)
            self.Mc[:, size > CONCEPT_MAX_SIZE] = 0
        ccnt = valid @ self.Mc
        self.con_ret = (np.nan_to_num(self.CHG) @ self.Mc) / np.maximum(ccnt, 1)
        self.con_lu = self.LU.astype(float) @ self.Mc
        ccum = np.cumsum(np.log1p(self.con_ret), axis=0)
        self.con_ret5 = np.full_like(self.con_ret, np.nan)
        self.con_ret5[5:] = np.expm1(ccum[5:] - ccum[:-5])
        self.con_valid = ccnt[-1] >= 3

    def concept_info(self, t, j, top=3):
        ks = np.where(self.Mc[j] > 0)[0]
        if len(ks) == 0:
            return dict(concepts=[], con_rebound=False, con_rebound_names=[])
        order = ks[np.lexsort((-self.con_ret[t, ks], -self.con_lu[t, ks]))]
        reb = [k for k in order if self.con_ret5[t, k] <= -0.03 and self.con_ret[t, k] >= 0.02]
        item = lambda k: dict(name=self.con_names[k], ret=r(self.con_ret[t, k]), ret5=r(self.con_ret5[t, k]),
                              lu=int(self.con_lu[t, k]))
        return dict(concepts=[item(k) for k in order[:top]], con_rebound=bool(reb),
                    con_rebound_names=[self.con_names[k] for k in reb[:3]])

    def concept_board(self, t, n=10):
        ks = np.where(self.con_valid)[0]
        hot = ks[np.lexsort((-self.con_ret[t, ks], -self.con_lu[t, ks]))][:n]
        rb = [k for k in ks if self.con_ret5[t, k] <= -0.03 and self.con_ret[t, k] >= 0.02]
        rb = sorted(rb, key=lambda k: (-self.con_lu[t, k], -self.con_ret[t, k]))[:n]
        item = lambda k: dict(name=self.con_names[k], ret=r(self.con_ret[t, k]), ret5=r(self.con_ret5[t, k]),
                              lu=int(self.con_lu[t, k]))
        return [item(k) for k in hot], [item(k) for k in rb], len([k for k in ks if self.con_ret5[t, k] <= -0.03 and self.con_ret[t, k] >= 0.02])

    def features(self, t, j):
        C, H, V = self.C, self.H, self.V
        s = self.sec_of[j]
        peers = np.where((self.sec_of == s) & self.LU[t])[0]
        n_prev = self.SB[t - 1].sum()
        with np.errstate(all="ignore"):
            hi60 = np.nanmax(H[max(0, t - 60):t, j])
            v5 = np.nanmean(V[max(0, t - 5):t, j])
        return dict(
            sec_ret5=float(self.sec_ret5[t, s]), sec_ret=float(self.sec_ret[t, s]),
            sec_rank=int(self.sec_rank[t, s]), sec_lu=int(self.sec_lu[t, s]), sec_lu_prev=int(self.sec_lu[t - 1, s]),
            ret20=float(C[t - 1, j] / C[t - 21, j] - 1) if t >= 21 and C[t - 21, j] > 0 else float("nan"),
            pos60=float(C[t, j] / hi60) if hi60 > 0 else float("nan"),
            amt_yi=float(self.AMT[t, j] / 1e5),
            gap0=float(self.O[t, j] / self.PREV[t, j] - 1),
            volr=float(V[t, j] / v5) if v5 > 0 else float("nan"),
            mkt_lu=int(self.LU[t].sum()),
            zb_rate=float(self.ZB[t].sum() / max(1, self.LU[t].sum() + self.ZB[t].sum())),
            prom_prev=float((self.SB[t - 1] & self.LU[t]).sum() / n_prev) if n_prev else float("nan"),
            leader=bool(self.AMT[t, j] >= np.nanmax(self.AMT[t, peers])) if len(peers) else False,
            idx_up=bool(self.idx_up[t]), cyb=bool(self.LIM[t, j] > 0.15),
        )

    def events(self, t, exclude=()):
        out = []
        for j in np.where(self.SB[t] & ~self.YIZI[t])[0]:
            c = self.codes[j]
            if self.sec_of[j] < 0 or c in exclude or np.isnan(self.C[t - 21, j]):
                continue
            f = self.features(t, j)
            f["ruleA"] = bool(f["sec_ret5"] <= -0.03 and f["ret20"] <= 0 and f["sec_ret"] >= 0.02)
            out.append((j, f))
        return out


def score(f, model):
    z = model["intercept"]
    for name, edges in model["bins"].items():
        v = f.get(name)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            v = model["medians"][name]
        k = int(np.digitize([v], edges[1:-1])[0])
        z += model["coef"].get(f"{name}#{k}", 0.0)
    for name in model["flags"]:
        z += model["coef"].get(name, 0.0) * float(bool(f.get(name)))
    p = 1 / (1 + math.exp(-z))
    idx = int(np.clip(np.searchsorted(model["pct"], p), 0, 100))
    tier = "低" if idx < model["tier_cut"][0] else ("中" if idx < model["tier_cut"][1] else "高")
    return p, idx, tier


def reasons(f):
    out = []
    if f["sec_ret5"] > -0.03:
        out.append(f"板块5日{f['sec_ret5']*100:+.1f}%（要求≤−3%）")
    if f["sec_ret"] < 0.02:
        out.append(f"板块今天{f['sec_ret']*100:+.1f}%（要求≥+2%）")
    if not (f["ret20"] <= 0):
        out.append(f"个股20日{f['ret20']*100:+.1f}%（要求≤0）")
    return out


def r(x, n=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), n)


# --------------------------------------------------------------------------- close-of-day run
def run_close(P, names, st_codes, out_dir, now, log=print):
    model = load_model()
    T = P.T
    t = T - 1
    asof = P.cal[t]
    tpath = os.path.join(out_dir, "shouban_trades.json")
    try:
        book = json.load(open(tpath, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        book = {"meta": {}, "trades": {}}
    tpos = {d: i for i, d in enumerate(P.cal)}
    cpos = {c: j for j, c in enumerate(P.codes)}

    # 1) settle pending signals whose entry day has arrived: buy at the open, lowest opens first, max 5
    pend = {}
    for k, tr in book["trades"].items():
        if tr["status"] == "pending" and tr["date"] in tpos and tpos[tr["date"]] < t:
            pend.setdefault(tr["date"], []).append(k)
    for d, keys in pend.items():
        t0 = tpos[d]
        e = t0 + 1
        cand = []
        for k in keys:
            tr = book["trades"][k]
            j = cpos.get(tr["code"])
            if j is None or np.isnan(P.O[e, j]):
                tr.update(status="skipped", note="停牌/无数据")
                continue
            gap = P.O[e, j] / P.C[t0, j] - 1
            tr["gap1"] = r(gap)
            if gap >= P.LIM[e, j] - 0.005:
                tr.update(status="skipped", note="开盘涨停，买不进")
            elif gap > MAX_GAP:
                tr.update(status="skipped", note="高开超过7%，放弃")
            else:
                cand.append((gap, k, j))
        for n, (gap, k, j) in enumerate(sorted(cand)):
            tr = book["trades"][k]
            if n < MAX_PER_DAY:
                tr.update(status="open", entry_date=P.cal[e], entry=r(P.O[e, j], 3), ref_close=r(P.C[t0, j], 3))
            else:
                tr.update(status="skipped", note="当天已买满5只")

    # 2) track open positions: sell at the close of the 3rd trading day (skip locked limit-down days)
    for k, tr in book["trades"].items():
        if tr["status"] != "open":
            continue
        j, t0 = cpos.get(tr["code"]), tpos.get(tr["date"])
        if j is None or t0 is None:
            tr["stale"] = True
            continue
        kadj = P.C[t0, j] / tr["ref_close"] if tr.get("ref_close") else 1.0
        entry = tr["entry"] * kadj
        x, held = t0 + 1, 0
        exit_t = None
        while x < T:
            if not np.isnan(P.C[x, j]):
                held += 1
                if held >= HOLD_DAYS and not (P.LD[x, j] and P.H[x, j] == P.L[x, j]):
                    exit_t = x
                    break
            x += 1
        if exit_t is None:
            last = P.C[T - 1, j] if not np.isnan(P.C[T - 1, j]) else entry
            tr.update(held=held, last=r(last / kadj, 3), ret=r(last / entry - 1))
        else:
            px = P.C[exit_t, j]
            tr.update(status="closed", held=held, exit_date=P.cal[exit_t], exit=r(px / kadj, 3),
                      ret=r(px / entry - 1 - cost(P.cal[exit_t])))

    # 3) today's 首板 + sector analysis
    evs = P.events(t, exclude=st_codes)
    picks, others = [], []
    for j, f in evs:
        p, idx, tier = score(f, model)
        c = P.codes[j]
        item = dict(code=c, name=names.get(c, ""), sector=P.sec_names[P.sec_of[j]], close=r(P.C[t, j], 3),
                    p=r(p, 3), index=idx, tier=tier, **{k: r(v) if isinstance(v, float) else v for k, v in f.items()})
        item.update(P.concept_info(t, j))
        if f["ruleA"]:
            picks.append(item)
            key = f"{c}|{asof}"
            if key not in book["trades"]:
                book["trades"][key] = dict(code=c, name=item["name"], sector=item["sector"], date=asof,
                                           index=idx, tier=tier, close=item["close"], status="pending",
                                           con_rebound=item["con_rebound"])
        else:
            item["why_not"] = reasons(f)
            others.append(item)
    picks.sort(key=lambda x: -x["index"])
    others.sort(key=lambda x: -x["index"])
    rebound = []
    for s in range(len(P.sec_names)):
        if P.sec_ret5[t, s] <= -0.03 and P.sec_ret[t, s] >= 0.02:
            rebound.append(dict(name=P.sec_names[s], ret=r(P.sec_ret[t, s]), ret5=r(P.sec_ret5[t, s]),
                                lu=int(P.sec_lu[t, s]), rank=int(P.sec_rank[t, s])))
    rebound.sort(key=lambda x: x["rank"])
    hot = [dict(name=P.sec_names[s], ret=r(P.sec_ret[t, s]), ret5=r(P.sec_ret5[t, s]), lu=int(P.sec_lu[t, s]))
           for s in np.argsort(-P.sec_ret[t])[:8]]
    hot_con, reb_con, n_reb_con = P.concept_board(t)
    ladder, levels, broken = ladder_info(P, t, names, st_codes)
    book["meta"]["last_date"] = asof
    with open(tpath, "w", encoding="utf-8") as fh:
        json.dump(book, fh, ensure_ascii=False, separators=(",", ":"))
    out = dict(asof=asof, generated=now.strftime("%Y-%m-%d %H:%M"), mode="close",
               market=dict(limit_ups=int(P.LU[t].sum()), zb_rate=r(P.ZB[t].sum() / max(1, P.LU[t].sum() + P.ZB[t].sum())),
                           idx_up=bool(P.idx_up[t]), shouban=int((P.SB[t] & ~P.YIZI[t]).sum())),
               rebound=rebound, hot=hot, picks=picks, others=others[:10], others_count=len(others),
               hot_concepts=hot_con, rebound_concepts=reb_con, rebound_concepts_count=n_reb_con,
               ladder=ladder, levels=levels, broken=broken[:10], broken_count=len(broken),
               height=max([x["k"] for x in ladder], default=1),
               morning=None,
               model=dict(tiers=model["tiers"], gaps=model["gaps"], bt=model["bt"], tier_cut=model["tier_cut"],
                          concept_stats=model.get("concept_stats"), ladder=model.get("ladder")))
    with open(os.path.join(out_dir, "shouban.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"首板 {asof}: 首板 {len(evs)} 只，符合规则 {len(picks)} 只，反弹板块 {len(rebound)} 个")
    return out


def ladder_info(P, t, names, st_codes):
    """连板梯队 (>=2 boards), today's promotion rate per level and today's broken streaks."""
    ok = P.listed[t] & np.array([c not in st_codes for c in P.codes])
    lad = []
    for j in np.where((P.streak[t] >= 2) & ok)[0]:
        c, k = P.codes[j], int(P.streak[t, j])
        ci = P.concept_info(t, j, top=1)
        lad.append(dict(code=c, name=names.get(c, ""), k=k, yizi=bool(P.YIZI[t, j]),
                        sector=P.sec_names[P.sec_of[j]] if P.sec_of[j] >= 0 else "",
                        concept=ci["concepts"][0]["name"] if ci["concepts"] else "",
                        amt_yi=r(P.AMT[t, j] / 1e5, 2), sec_ret=r(P.sec_ret[t, P.sec_of[j]]) if P.sec_of[j] >= 0 else None,
                        start=P.cal[t - k + 1] if t - k + 1 >= 0 else None))
    lad.sort(key=lambda x: (-x["k"], -(x["amt_yi"] or 0)))
    levels = []
    okp = P.listed[t - 1] & np.array([c not in st_codes for c in P.codes])
    mx = int(P.streak[t - 1][okp].max()) if okp.any() else 0
    for k in range(1, max(mx, 1) + 1):
        prev = (P.streak[t - 1] == k) & okp
        n = int(prev.sum())
        if n:
            levels.append(dict(k=k, n=n, up=int((prev & P.LU[t]).sum()), rate=r((prev & P.LU[t]).sum() / n, 3)))
    brk = [dict(code=P.codes[j], name=names.get(P.codes[j], ""), k=int(P.streak[t - 1, j]), chg=r(P.CHG[t, j]))
           for j in np.where((P.streak[t - 1] >= 2) & ~P.LU[t] & okp)[0]]
    brk.sort(key=lambda x: -x["k"])
    return lad, levels, brk


# --------------------------------------------------------------------------- 9:26 auction check
def run_morning(out_dir, quotes, now, log=print):
    """quotes(codes) -> {code: (open_or_auction_price, prev_close)}"""
    path = os.path.join(out_dir, "shouban.json")
    try:
        sb = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        log("no shouban.json yet")
        return None
    picks = sb.get("picks") or []
    if not picks:
        sb["morning"] = dict(time=now.strftime("%H:%M"), note="昨天没有符合规则的首板")
    else:
        q = quotes([p["code"] for p in picks])
        cand = []
        for p in picks:
            o, pc = q.get(p["code"], (None, None))
            if not o or not pc:
                p.update(gap1=None, verdict="暂无竞价数据")
                continue
            lim = 0.20 if p["code"][2:].startswith("30") else 0.10
            gap = o / pc - 1
            p.update(gap1=r(gap), open=r(o, 3))
            if gap >= lim - 0.005:
                p["verdict"] = "开盘涨停，买不进"
            elif gap > MAX_GAP:
                p["verdict"] = "高开超过7%，放弃"
            else:
                cand.append((gap, p))
        for n, (gap, p) in enumerate(sorted(cand, key=lambda x: x[0])):
            p["verdict"] = "买入" if n < MAX_PER_DAY else "备选（已满5只）"
            p["order"] = n + 1
        sb["morning"] = dict(time=now.strftime("%H:%M"), note=f"{min(len(cand), MAX_PER_DAY)} 只可按规则买入")
    sb["generated_morning"] = now.strftime("%Y-%m-%d %H:%M")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sb, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"morning update: {sb['morning']}")
    return sb
