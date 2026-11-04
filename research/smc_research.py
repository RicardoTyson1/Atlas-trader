"""Pre-registered test of the trader's own model: liquidity sweep -> FVG retrace.

Same protocol as gold_research.py (DEV halves for selection, one-shot HOLDOUT).
Caveat: the holdout period was already used for gold_research.py's families, so it
is no longer perfectly "unseen" by the researcher; this family was never run on it.

Run:  python research/smc_research.py data/raw/XAUUSD.R_5m.csv
"""
import sys

import gold_research as g

g.FAMILIES = {
    "liquidity_fvg": dict(
        strategy="liquidity_fvg", tf="5m", overnight=False,
        configs=[dict(reward_risk=rr, target=tgt, **x) for x in g.grid(
            bias=["discount", "trend", "none"], liquidity=["pdhl", "asian", "both"],
            killzones=["london", "ny", "both"])
            for tgt, rr in (("rr", 2.0), ("rr", 3.0), ("liquidity", 2.0))],
    ),
}

if __name__ == "__main__":
    g.main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/XAUUSD.R_5m.csv")
