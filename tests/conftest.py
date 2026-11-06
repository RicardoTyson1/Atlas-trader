import pytest

from atlas_trader.config import config_from_dict
from atlas_trader.data import resample, synthetic_intraday


BASE = {
    "account": {"starting_balance": 50000},
    "prop_rules": {"profit_target_pct": 8, "daily_loss_pct": 4, "max_drawdown_pct": 8,
                   "drawdown_type": "trailing", "safety_buffer": 0.7},
    "risk": {"risk_per_trade_pct": 0.5, "max_trades_per_day": 3, "max_consecutive_losses": 3},
    "instruments": {"NAS100": {"value_per_point": 1.0, "min_lot": 0.1, "lot_step": 0.1,
                               "spread_points": 1.0, "slippage_points": 0.5}},
    "strategies": [{"name": "momentum_rsi", "instruments": ["NAS100"], "timeframe": "5m"}],
}


@pytest.fixture
def cfg(tmp_path):
    raw = {**BASE, "paths": {"state_dir": str(tmp_path / "state"), "kill_switch_file": str(tmp_path / "state" / "KILL")}}
    return config_from_dict(raw)


@pytest.fixture(scope="session")
def bars5():
    return resample(synthetic_intraday(days=30, seed=3), "5m")
