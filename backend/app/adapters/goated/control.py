"""goated's control buttons: WolfBet's, for the verbs goatedctl has.

goated has no live IPC, so it has no pause, resume or reload; its benchmark times 0-amount
bets (goated has no read-only endpoint), takes no lane count and asks first; its export
writes one session, so it needs one; its backtest takes only the flags `goated.py
--backtest` reads; its update has no branch switch. Every other button, body and check is
WolfBet's.
"""

from dataclasses import replace
from typing import Literal

from pydantic import Field, model_validator

from app.adapters.base import ControlAction
from app.adapters.wolfbet.control import ACTIONS as WOLFBET_ACTIONS
from app.adapters.wolfbet.control import Args, Name, NoArgs

NOT_OFFERED = frozenset({"pause", "resume", "reload"})


class GoatedBenchmarkArgs(Args):
    count: int = Field(default=20, ge=3, le=200)


class GoatedBacktestArgs(Args):
    simulate: int | None = Field(default=None, ge=1, le=5_000_000)
    replay_session: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    preset: Name | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "GoatedBacktestArgs":
        if self.simulate is not None and self.replay_session is not None:
            raise ValueError("simulate and replay_session are exclusive")
        return self


class GoatedExportArgs(Args):
    """goated writes one session per file, its format from the file's extension."""

    session_id: int = Field(ge=1)
    format: Literal["csv", "json"] = "csv"


def _goated(name: str, action: ControlAction) -> ControlAction:
    if name == "benchmark":
        # Each call is a real 0-amount bet, which goated.py asks to confirm; the agent says
        # yes for it, so the owner confirms here.
        return replace(
            action,
            body=GoatedBenchmarkArgs,
            effect="Times {count} calls. Each is a 0-amount bet, so no balance is risked.",
            confirm_first=True,
        )
    if name == "export":
        return replace(action, body=GoatedExportArgs)
    if name == "backtest":
        return replace(action, body=GoatedBacktestArgs)
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
