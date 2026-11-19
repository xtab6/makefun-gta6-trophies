# makefun-gta6-trophies

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/xtab6/makefun-gta6-trophies/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/xtab6/makefun-gta6-trophies/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts each Grand Theft Auto VI trophy's Exophase earn rate at 2026-11-26T00:00:00Z, a week after launch, as five quantiles (5/25/50/75/95%) per trophy, scored by pinball loss on the logit scale. The forecaster splits every recorded read into holders and the tracked-player base, fits both in log-time since release, and extrapolates them to the target; when the history is flat (no refresh seen) it applies a refresh prior that expects Exophase's tracked base to grow several-fold and dilutes every rate but the one already at 100%. A story-chain funnel keeps chapter medians non-increasing (Ch1 → Ch2 → Ch3 → Ch4 → final mission → 100% completion → platinum).

## Run it

```sh
python3 main.py
```

Reads the case file at `/input/<id>.in` and writes `/output/<id>.out` with `{"trophies": [{"id": n, "q": [q05, q25, q50, q75, q95]}, ...]}`. Stdlib only, deterministic, no network. For local testing: `python3 main.py <case.in> <out.json>`.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
