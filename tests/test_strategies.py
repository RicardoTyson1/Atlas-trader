import numpy as np
import pytest

from atlas_trader.strategies import REGISTRY, build_strategy


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_lookahead(name, cfg, bars5):
    """Signals computed on truncated data must equal signals on full data."""
    strat = build_strategy(name, cfg.session)
    full = strat.generate(bars5)
    for cut in (300, 777, 1500, len(bars5) - 5):
        part = strat.generate(bars5.iloc[:cut])
        a = full.iloc[:cut]
        assert (part["signal"].values == a["signal"].values).all(), f"lookahead in {name} at {cut}"
        np.testing.assert_allclose(part["stop"].values, a["stop"].values, equal_nan=True)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_stops_on_correct_side(name, cfg, bars5):
    out = build_strategy(name, cfg.session).generate(bars5)
    c = bars5["close"]
    longs, shorts = out.signal == 1, out.signal == -1
    assert (out.stop[longs] < c[longs]).all()
    assert (out.stop[shorts] > c[shorts]).all()


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_signals_only_in_entry_window(name, cfg, bars5):
    out = build_strategy(name, cfg.session).generate(bars5)
    local = (out.index + __import__("pandas").Timedelta(minutes=5)).tz_convert(cfg.session.timezone)
    mins = local.hour * 60 + local.minute
    sig = out.signal.values != 0
    assert (mins[sig] >= 9 * 60 + 45).all() and (mins[sig] <= 15 * 60).all()


def test_orb_one_trade_per_day(cfg, bars5):
    out = build_strategy("orb", cfg.session).generate(bars5)
    day = out.index.tz_convert(cfg.session.timezone).date
    counts = (out.signal != 0).groupby(day).sum()
    assert counts.max() <= 1


def test_unknown_param_rejected(cfg):
    with pytest.raises(ValueError):
        build_strategy("orb", cfg.session, {"nope": 1})
