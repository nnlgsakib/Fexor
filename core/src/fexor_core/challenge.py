"""Active liveness challenge: random actions verified from face-mesh metrics.

Pure logic (no models) so it is fully unit-testable with synthetic metric streams.
Thresholds are relative to the person's own neutral baseline, so they work across
face shapes and camera distances.
"""

from __future__ import annotations

import secrets
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

from .liveness import MeshMetrics


class Action(StrEnum):
    BLINK = "blink"
    TURN_LEFT = "turn_left"  # subject's own left
    TURN_RIGHT = "turn_right"
    SMILE = "smile"
    LOOK_UP = "look_up"


class Status(StrEnum):
    BASELINE = "baseline"
    ACTIVE = "active"
    PASSED = "passed"
    FAILED = "failed"


@dataclass
class Thresholds:
    blink_closed: float = 0.6  # ear below base * this = eyes closed
    blink_open: float = 0.85  # ear back above base * this = reopened
    turn: float = 0.16  # yaw delta (interocular units)
    look_up: float = 0.8  # pitch below base * this
    smile: float = 1.12  # mouth width above base * this
    neutral_yaw: float = 0.12  # baseline must be roughly frontal
    min_presence: float = 0.5


def random_actions(n: int = 2, pool: tuple[Action, ...] = tuple(Action)) -> list[Action]:
    """n distinct actions in unpredictable order (cryptographic RNG)."""
    remaining = list(pool)
    out = []
    for _ in range(min(n, len(remaining))):
        out.append(remaining.pop(secrets.randbelow(len(remaining))))
    return out


@dataclass
class Baseline:
    ear: float
    yaw: float
    pitch: float
    smile: float


@dataclass
class Challenge:
    actions: list[Action]
    step_timeout: float = 6.0
    baseline_frames: int = 5
    th: Thresholds = field(default_factory=Thresholds)

    status: Status = Status.BASELINE
    step: int = 0
    reason: str | None = None
    baseline: Baseline | None = None
    _samples: list[MeshMetrics] = field(default_factory=list)
    _step_started: float = 0.0
    _eyes_closed: bool = False

    @property
    def current(self) -> Action | None:
        if self.status is Status.ACTIVE and self.step < len(self.actions):
            return self.actions[self.step]
        return None

    def _fail(self, reason: str) -> Status:
        self.status, self.reason = Status.FAILED, reason
        return self.status

    def feed(self, m: MeshMetrics, now: float) -> Status:
        if self.status in (Status.PASSED, Status.FAILED):
            return self.status
        if m.presence < self.th.min_presence:
            return self.status  # skip bad frame, timeout still applies below

        if self.status is Status.BASELINE:
            if abs(m.yaw) > self.th.neutral_yaw:
                self._samples.clear()  # need a frontal neutral face to start
                return self.status
            self._samples.append(m)
            if len(self._samples) >= self.baseline_frames:
                s = self._samples
                self.baseline = Baseline(
                    ear=statistics.median(x.ear for x in s),
                    yaw=statistics.median(x.yaw for x in s),
                    pitch=statistics.median(x.pitch for x in s),
                    smile=statistics.median(x.smile for x in s),
                )
                self.status, self._step_started = Status.ACTIVE, now
            return self.status

        if now - self._step_started > self.step_timeout:
            return self._fail(f"timeout:{self.current}")

        done = self._detected(m)
        wrong = self._wrong_action(m)
        if done:
            self.step += 1
            self._eyes_closed = False
            self._step_started = now
            if self.step >= len(self.actions):
                self.status = Status.PASSED
        elif wrong:
            return self._fail(f"wrong_action:{wrong}")
        return self.status

    def _flags(self, m: MeshMetrics) -> set[Action]:
        b, th = self.baseline, self.th
        assert b is not None
        f = set()
        dyaw = m.yaw - b.yaw
        # Camera image is not mirrored: subject's left turn moves the nose to image right.
        if dyaw > th.turn:
            f.add(Action.TURN_LEFT)
        if dyaw < -th.turn:
            f.add(Action.TURN_RIGHT)
        if m.pitch < b.pitch * th.look_up:
            f.add(Action.LOOK_UP)
        if m.smile > b.smile * th.smile:
            f.add(Action.SMILE)
        return f

    def _detected(self, m: MeshMetrics) -> bool:
        assert self.baseline is not None
        want = self.current
        if want is Action.BLINK:
            if m.ear < self.baseline.ear * self.th.blink_closed:
                self._eyes_closed = True
            elif self._eyes_closed and m.ear > self.baseline.ear * self.th.blink_open:
                return True
            return False
        return want in self._flags(m)

    def _wrong_action(self, m: MeshMetrics) -> Action | None:
        """A different deliberate pose (blinks are natural and never count as wrong)."""
        others = self._flags(m) - {self.current}
        # Looking up while turning, or smiling, can come along with the requested move;
        # only an opposite turn is a clear sign of a replayed / scripted sequence.
        for a in (Action.TURN_LEFT, Action.TURN_RIGHT):
            if a in others and self.current in (Action.TURN_LEFT, Action.TURN_RIGHT):
                return a
        return None
