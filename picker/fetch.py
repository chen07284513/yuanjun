"""A-share data sources: Eastmoney (primary) with Tencent as fallback.

Every kline is returned as a list of bars: (date 'YYYY-MM-DD', open, close, high, low, volume, amount_k)
with forward-adjusted (前复权) prices and turnover in thousand yuan.
"""
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Mobile Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA})


def _get(url, params=None, referer=None, tries=2, timeout=6):
    last = None
    for i in range(tries):
        try:
            h = {"Referer": referer} if referer else None
            r = S.get(url, params=params, headers=h, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1) + random.random() * 0.4)
    raise last


# ---------------------------------------------------------------- universe
def em_universe():
    """[(code like 'sh600519', name)] for SH main/STAR and SZ main/ChiNext."""
    out, pn = [], 1
    while True:
        txt = _get("https://push2.eastmoney.com/api/qt/clist/get", dict(
            pn=pn, pz=100, po=1, np=1, fltt=2, invt=2, fid="f12",
            fs="m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", fields="f12,f13,f14",
            ut="bd1d9ddb04089700cf9c27f6f7426281"), referer="https://quote.eastmoney.com/")
        d = json.loads(txt).get("data") or {}
        rows = d.get("diff") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        for r in rows:
            mk = "sh" if int(r["f13"]) == 1 else "sz"
            out.append((mk + str(r["f12"]), str(r["f14"])))
        total = int(d.get("total") or 0)
        if not rows or len(out) >= total:
            break
        pn += 1
        time.sleep(0.15)
    return out


# ---------------------------------------------------------------- klines
def em_kline(code, lmt=80, secid=None):
    secid = secid or (("1." if code.startswith("sh") else "0.") + code[2:])
    txt = _get("https://push2his.eastmoney.com/api/qt/stock/kline/get", dict(
        secid=secid, fields1="f1,f2,f3,f4,f5,f6",
        fields2="f51,f52,f53,f54,f55,f56,f57", klt=101, fqt=1, end="20500101", lmt=lmt,
        ut="7eea3edcaed734bea9cbfc24409ed989"), referer="https://quote.eastmoney.com/")
    d = json.loads(txt).get("data") or {}
    bars = []
    for line in d.get("klines") or []:
        f = line.split(",")
        bars.append((f[0], float(f[1]), float(f[2]), float(f[3]), float(f[4]),
                     float(f[5]), float(f[6]) / 1000.0))
    return bars, d.get("name")


def tx_kline(code, lmt=80):
    txt = _get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
               dict(param=f"{code},day,,,{lmt},qfq"))
    d = (json.loads(txt).get("data") or {}).get(code) or {}
    rows = d.get("qfqday") or d.get("day") or []
    name = None
    qt = d.get("qt") or {}
    if isinstance(qt.get(code), list) and len(qt[code]) > 1:
        name = qt[code][1]
    bars = []
    for f in rows:
        o, c, h, l, v = (float(x) for x in f[1:6])
        bars.append((f[0], o, c, h, l, v, v * 100 * (o + c + h + l) / 4 / 1000.0))
    return bars, name


class LiveFetcher:
    """Fetch everything the daily run needs. Records which sources worked."""

    def __init__(self, workers=8, lmt=80, log=print, deadline=600):
        self.workers, self.lmt, self.log, self.deadline = workers, lmt, log, deadline
        self.primary = "em"
        self.used = {"em": 0, "tx": 0, "fail": 0}

    def universe(self):
        try:
            u = em_universe()
            if len(u) > 4000:
                return u, "eastmoney"
            self.log(f"universe too small from eastmoney: {len(u)}")
        except Exception as e:  # noqa: BLE001
            self.log(f"eastmoney universe failed: {e!r}")
        return None, None

    def _one(self, code):
        order = ["em", "tx"] if self.primary == "em" else ["tx", "em"]
        for n, src in enumerate(order):
            try:
                bars, name = (em_kline if src == "em" else tx_kline)(code, self.lmt)
                if bars:
                    self.used[src] += 1
                    if n:
                        self.chunk_fallback += 1
                    return code, bars, name
            except Exception:  # noqa: BLE001
                pass
        self.used["fail"] += 1
        return code, None, None

    def klines(self, codes):
        # probe which source works from this runner before the big fan-out
        probe = codes[:: max(1, len(codes) // 20)][:20]
        ok_em = 0
        for c in probe:
            try:
                ok_em += bool(em_kline(c, 5)[0])
            except Exception:  # noqa: BLE001
                pass
        if ok_em < len(probe) // 2:
            self.primary = "tx"
        self.log(f"probe eastmoney {ok_em}/{len(probe)} -> primary {self.primary}")
        out = {}
        t0 = time.time()
        with ThreadPoolExecutor(self.workers) as ex:
            for i in range(0, len(codes), 200):
                self.chunk_fallback = 0
                got = 0
                for code, bars, name in ex.map(self._one, codes[i:i + 200]):
                    if bars:
                        out[code] = (bars, name)
                        got += 1
                el = time.time() - t0
                self.log(f"  {min(i + 200, len(codes))}/{len(codes)} ok={len(out)} {el:.0f}s primary={self.primary} {self.used}")
                if i == 0 and not out:
                    self.log("first 200 stocks all failed on both sources - giving up")
                    break
                # primary source struggling but the backup works -> switch for the rest of the run
                if got and self.chunk_fallback > got // 2:
                    self.primary = "tx" if self.primary == "em" else "em"
                    self.log(f"  switching primary source to {self.primary}")
                if el > self.deadline:
                    self.log(f"deadline {self.deadline}s reached - stopping with what we have")
                    break
        self.log(f"klines fetched {len(out)}/{len(codes)} sources {self.used}")
        return out

    def index_kline(self):
        try:
            bars, _ = em_kline("sh000985", self.lmt, secid="1.000985")
            if bars:
                return bars
        except Exception as e:  # noqa: BLE001
            self.log(f"eastmoney index failed: {e!r}")
        bars, _ = tx_kline("sh000985", self.lmt)
        return bars
