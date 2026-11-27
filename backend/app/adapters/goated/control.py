"""goated's control buttons: WolfBet's, for the verbs goatedctl has.

goated has no live IPC, so it has no pause, resume or reload; its benchmark times 0-amount
bets (goated has no read-only endpoint) and takes no lane count; its backtest takes only
the flags `goated.py --backtest` reads; its update has no branch switch. Every other
button, body and check is WolfBet's.
"""

from dataclasses import replace

from pydantic import Field, model_validator

from app.adapters.base import ControlAction
from app.adapters.wolfbet.control import ACTIONS as WOLFBET_ACTIONS
from app.adapters.wolfbet.control import Args, Name, NoArgs

NOT_OFFERED = frozenset({"pause", "resume", "reload"})


class BenchmarkArgs(Args):
    count: int = Field(default=20, ge=3, le=200)


class BacktestArgs(Args):
    simulate: int | None = Field(default=None, ge=1, le=5_000_000)
    replay_session: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    preset: Name | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "BacktestArgs":
        if self.simulate is not None and self.replay_session is not None:
            raise ValueError("simulate and replay_session are exclusive")
        return self


def _goated(name: str, action: ControlAction) -> ControlAction:
    if name == "benchmark":
        return replace(
            action,
            body=BenchmarkArgs,
            effect="Times {count} calls. Each is a 0-amount bet, so no balance is risked.",
        )
    if name == "backtest":
        return replace(action, body=BacktestArgs)
    if name == "update":
        return replace(
            action,
            body=NoArgs,
            effect="The bot pulls the latest code and reinstalls. Each lane restarts on it when "
            "its session ends, so betting goes on. Use Restart under Service to move every lane "
            "now.",
        )
    if name == "version":
        return replace(action, effect="Shows the bot and goatedctl versions. Changes nothing.")
    return action


ACTIONS: dict[str, ControlAction] = {
    name: _goated(name, action)
    for name, action in WOLFBET_ACTIONS.items()
    if name not in NOT_OFFERED
}
