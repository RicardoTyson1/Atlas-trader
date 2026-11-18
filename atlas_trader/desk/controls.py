"""Desk controls, shared by the dashboard (writes) and the bot runner (reads).

The file `state/desk_controls.json` holds:
  disabled      strategy labels the bot must not open NEW trades for (open trades are still managed)
  paused        no new bot entries at all
  lockout_day   NY date on which a guardrail locked the desk; until that day ends, entries stay off and
                nothing can be switched back on from the page
  log           the last 100 control changes (who / when / what)

Guardrails (Ricardo, 2026-11-18: "do everything the way I trade, but not the way I spiral to a blown
account"). The page can always make the bot SAFER (switch a model off, pause, emergency stop). It can
never make it riskier while a guardrail is tripped, and it has no risk-size control at all:
  * Daily loss lockout: today's loss >= `daily_loss_lockout_pct` of the start-of-day balance -> new
    entries paused for the rest of the NY day, and re-enabling is blocked until the next day.
  * Loss streak lockout: `loss_streak_lockout` losing trades in a row today -> same.
  * Cool-down: switching a model back on (or un-pausing) is blocked for `cooldown_after_loss_min`
    minutes after a losing trade. This is the "instant re-entry after a loss" leak from his history.
The runner only READS this file; a missing or unreadable file means "no restrictions" so the desk can
never stop the bot from managing its trades.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

FILE = "desk_controls.json"
_lock = threading.Lock()


def path(state_dir: str | Path) -> Path:
    return Path(state_dir) / FILE


def load(state_dir: str | Path) -> dict:
    p = path(state_dir)
    try:
        d = json.loads(p.read_text())
        if not isinstance(d, dict):
            raise ValueError
    except (OSError, ValueError):
        d = {}
    d.setdefault("disabled", [])
    d.setdefault("paused", False)
    d.setdefault("lockout_day", None)
    d.setdefault("lockout_reason", None)
    d.setdefault("log", [])
    return d


def save(state_dir: str | Path, d: dict) -> None:
    p = path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    d["log"] = d.get("log", [])[-100:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, default=str))
    tmp.replace(p)


def entry_block(state_dir: str | Path, strategy_id: str, ny_day: str) -> str | None:
    """For the runner: a reason to skip a NEW entry, or None. Never raises."""
    try:
        d = load(state_dir)
    except Exception:  # noqa: BLE001 - the desk must never break the bot
        return None
    if d.get("lockout_day") == ny_day:
        return f"desk lockout today ({d.get('lockout_reason') or 'guardrail'})"
    if d.get("paused"):
        return "paused from the desk"
    if strategy_id in (d.get("disabled") or []):
        return "switched off from the desk"
    return None


def record(d: dict, who: str, what: str, now: datetime) -> None:
    d.setdefault("log", []).append({"time": now.isoformat(timespec="seconds"), "who": who, "what": what})


class Guardrails:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.daily_loss_pct = float(cfg.get("daily_loss_lockout_pct", 3.0))
        self.loss_streak = int(cfg.get("loss_streak_lockout", 3))
        self.cooldown_min = float(cfg.get("cooldown_after_loss_min", 30))

    def trip(self, sod_balance: float, equity: float, losses_in_row: int) -> str | None:
        """A reason to lock the desk for the day, or None."""
        if sod_balance > 0 and self.daily_loss_pct > 0:
            dd = (sod_balance - equity) / sod_balance * 100
            if dd >= self.daily_loss_pct:
                return f"down {dd:.1f}% today (limit {self.daily_loss_pct:g}%)"
        if self.loss_streak > 0 and losses_in_row >= self.loss_streak:
            return f"{losses_in_row} losses in a row (limit {self.loss_streak})"
        return None

    def can_loosen(self, d: dict, ny_day: str, minutes_since_loss: float | None) -> str | None:
        """None if the page may switch something back ON, else the reason it may not."""
        if d.get("lockout_day") == ny_day:
            return f"locked for today: {d.get('lockout_reason')}. Unlocks at the next session."
        if minutes_since_loss is not None and minutes_since_loss < self.cooldown_min:
            left = self.cooldown_min - minutes_since_loss
            return f"cool-down after a loss: {left:.0f} more minutes"
        return None
