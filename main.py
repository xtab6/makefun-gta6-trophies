#!/usr/bin/env python3
"""GTA VI trophy-rarity forecaster (make.fun gta6-trophy-rarity-forecast-01).

Reads one case file (/input/<id>.in) and writes /output/<id>.out:
{"trophies": [{"id": n, "q": [q05, q25, q50, q75, q95]}, ...]}
with five quantiles (percent) of each trophy's Exophase earn rate at the
case target, scored by pinball loss on the logit scale.

Model (stdlib only, deterministic, no network):
  * Split every read into its two moving parts: holders H = rate * tracked / 100
    and the tracked-player base P. Both are fit in log(1 + hours since
    release) by least squares and extrapolated to the target, anchored on the
    last read, so a dilution jump in P (new players syncing) does not drag the
    holder trend with it.
  * When the whole history is flat (no refresh seen: P constant), no trend is
    estimable, so a refresh prior applies instead: Exophase refreshes a game's
    counts every few hours and a launch-week refresh grows the tracked base
    several-fold, which dilutes every rate but the one already at 100%.
    The median shifts down by (1 - exp(-horizon/3h)) * ln(5) in logit space
    and the spread widens to 0.8 to cover both branches (refresh / no refresh).
  * Story-chain funnel: chapter trophies must be non-increasing along
    Ch1 -> Ch2 -> Ch3 -> Ch4 -> final mission -> 100% completion -> platinum,
    because a later chapter cannot be completed before the earlier ones.
    Medians are adjusted (cumulative min) to respect that order.
  * The 100% trophy (every tracked player holds it, and a fresh sync wave
    held it too) stays pinned near 100 with a tight top-heavy spread.
  * Spread otherwise: residual std of the logit(rate) fit combined with a
    0.25 * sqrt(horizon_days) random-walk term, floored at 0.35, capped at 1.2.

Usage: python3 main.py [case.in [out.json]]  (defaults: /input/*.in -> /output/<id>.out)
"""
import glob
import json
import math
import os
import sys
from datetime import datetime, timezone

Z = (-1.645, -0.674, 0.0, 0.674, 1.645)
LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)

# Refresh prior (flat-history branch): mean gap between Exophase refreshes
# and expected tracked-base growth factor on a launch-week refresh.
REFRESH_MEAN_GAP_H = 3.0
REFRESH_GROWTH = 5.0
FLAT_SPREAD = 0.8

# Story chain (trophy ids) that must be non-increasing in median earn rate.
CHAIN = (3, 4, 5, 6, 7, 14, 1)

TIER_PRIOR = {"platinum": 0.3, "gold": 0.8, "silver": 1.5, "bronze": 2.5}


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def logit(p):
    p = min(max(p, 0.01), 99.99) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(x):
    if x > 20:
        return 99.9999
    if x < -20:
        return 0.0001
    return 100.0 / (1.0 + math.exp(-x))


def ols_slope(xs, ys):
    """Least-squares slope of y on x; 0.0 when it cannot be estimated."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def forecast(case):
    trophies = case["trophies"]
    history = case.get("history") or []
    release = parse_ts(case["game"]["release_date"] + "T00:00:00Z")
    at = parse_ts(case["at"])
    target = parse_ts(case["target"])
    horizon_h = max(0.0, (target - at).total_seconds() / 3600.0)
    horizon_d = horizon_h / 24.0

    # Time series: hours since release for each read.
    reads = []
    for h in history:
        try:
            t = (parse_ts(h["at"]) - release).total_seconds() / 3600.0
        except (KeyError, ValueError):
            continue
        reads.append((t, h))
    reads.sort(key=lambda r: r[0])

    # Tracked-base series; flat when no refresh was ever observed.
    p_series = [(t, h["players_tracked"]) for t, h in reads
                if h.get("players_tracked")]
    if p_series:
        p_min = min(p for _, p in p_series)
        p_max = max(p for _, p in p_series)
        flat = p_max <= p_min * 1.05
        p_last = p_series[-1][1]
    else:
        flat = True
        p_last = None

    t_last = (at - release).total_seconds() / 3600.0
    t_target = (target - release).total_seconds() / 3600.0
    log_ratio = math.log((1.0 + t_target) / (1.0 + t_last)) if t_target > t_last else 0.0

    # Base growth slope from log P on log(1 + t).
    if len(p_series) >= 2 and p_last:
        xs = [math.log1p(t) for t, _ in p_series]
        ys = [math.log(p) for _, p in p_series]
        b_p = clamp(ols_slope(xs, ys), 0.0, 1.5)
    else:
        b_p = 0.0
    p_target = p_last * math.exp(b_p * log_ratio) if p_last else None

    # Refresh prior (only when the history is flat: no trend is estimable).
    p_refresh = 1.0 - math.exp(-horizon_h / REFRESH_MEAN_GAP_H) if flat else 0.0
    prior_shift = -p_refresh * math.log(REFRESH_GROWTH)

    medians = {}
    spreads = {}
    for idx, tr in enumerate(trophies):
        tid = tr["id"]
        # Last observed rate and holder series for this trophy.
        series = []
        last_rate = None
        for t, h in reads:
            rates = h.get("rates") or []
            if idx < len(rates) and rates[idx] is not None:
                series.append((t, rates[idx]))
                last_rate = rates[idx]
        if last_rate is None:
            medians[tid] = TIER_PRIOR.get(tr.get("tier"), 2.0)
            spreads[tid] = 1.2
            continue
        if last_rate >= 99.99:
            # Pinned at 100%: handled with fixed quantiles below.
            medians[tid] = 99.95
            spreads[tid] = 0.0
            continue
        if last_rate >= 99.0:
            # Saturated near 100% (a trophy every tracked player holds):
            # holders track the base, so the rate stays pinned up there.
            medians[tid] = min(99.95, max(last_rate, 99.5))
            spreads[tid] = -1.0  # marker: near-100 quantiles below
            continue
        if max(r for _, r in series) >= 99.99 and last_rate >= 90.0:
            # Was 100% at some read and still >= 90%: the entry-point trophy.
            # Its holders are new players onboarding, not progression, so the
            # rate recovers toward 100 as they play their first hour; do not
            # extrapolate a dilution dip as a trend.
            medians[tid] = clamp(last_rate, 95.0, 99.95)
            spreads[tid] = -1.0  # marker: near-100 quantiles below
            continue

        if flat or p_target is None:
            med = inv_logit(logit(last_rate) + prior_shift)
            medians[tid] = clamp(med, 0.02, 99.9)
            spreads[tid] = FLAT_SPREAD
            continue

        # Holder growth slope from log H on log(1 + t).
        h_pts = []
        for t, h in reads:
            rates = h.get("rates") or []
            p = h.get("players_tracked")
            if idx < len(rates) and rates[idx] is not None and p:
                h_pts.append((t, rates[idx] * p / 100.0))
        if len(h_pts) >= 2:
            xs = [math.log1p(t) for t, _ in h_pts]
            ys = [math.log(max(hv, 1e-9)) for _, hv in h_pts]
            b_h = clamp(ols_slope(xs, ys), 0.0, 2.0)
        else:
            b_h = 0.0
        h_last = last_rate * p_last / 100.0
        h_target = h_last * math.exp(b_h * log_ratio)
        # Holders cannot exceed the tracked base.
        h_target = min(h_target, 0.999 * p_target)
        med = 100.0 * h_target / p_target
        medians[tid] = clamp(med, 0.02, 99.9)

        # Spread: residual std of the logit(rate) fit + random-walk term.
        resid = 0.0
        if len(series) >= 3:
            xs = [math.log1p(t) for t, _ in series]
            ys = [logit(r) for _, r in series]
            b_r = ols_slope(xs, ys)
            a_r = sum(ys) / len(ys) - b_r * (sum(xs) / len(xs))
            resid = math.sqrt(sum((y - (a_r + b_r * x)) ** 2
                                  for x, y in zip(xs, ys)) / len(xs))
        s = math.sqrt(resid * resid + (0.25 * math.sqrt(horizon_d)) ** 2)
        spreads[tid] = clamp(s, 0.35, 1.2)

    # Story-chain funnel: medians non-increasing along CHAIN.
    for i in range(1, len(CHAIN)):
        prev, cur = CHAIN[i - 1], CHAIN[i]
        if prev in medians and cur in medians and medians[cur] > medians[prev]:
            medians[cur] = medians[prev]

    out = {"trophies": []}
    for tr in trophies:
        tid = tr["id"]
        if spreads.get(tid, 1.0) == 0.0:
            qs = [99.5, 99.8, 99.95, 99.99, 99.99]
        elif spreads.get(tid, 1.0) == -1.0:
            m = medians[tid]
            qs = [round(m - 1.0, 4), round(m - 0.3, 4), round(m, 4),
                  99.99, 99.99]
            qs = [clamp(q, 0.01, 99.99) for q in qs]
            for i in range(1, 5):
                if qs[i] < qs[i - 1]:
                    qs[i] = qs[i - 1]
        else:
            m = medians[tid]
            s = spreads[tid]
            qs = [round(inv_logit(logit(m) + z * s), 4) for z in Z]
            qs = [clamp(q, 0.01, 99.99) for q in qs]
            for i in range(1, 5):
                if qs[i] < qs[i - 1]:
                    qs[i] = qs[i - 1]
        out["trophies"].append({"id": tid, "q": qs})
    return out


def main(argv):
    if len(argv) >= 3:
        in_path, out_path = argv[1], argv[2]
    elif len(argv) == 2:
        in_path = argv[1]
        out_path = None
    else:
        ins = sorted(glob.glob("/input/*.in"))
        if not ins:
            raise SystemExit("no case file found in /input")
        in_path = ins[0]
        out_path = None
    with open(in_path) as f:
        case = json.load(f)
    result = forecast(case)
    if out_path is None:
        out_path = "/output/%s.out" % case["id"]
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
