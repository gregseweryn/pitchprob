"""Line-latency primitives — hand-computed series, no data required.

Every case here is a claim about *how the measurement can lie*: censoring
counted as speed, a margin change counted as a move, an opposite move
counted as a reaction. The arithmetic is small enough to check by eye.
"""

from datetime import UTC, datetime, timedelta

import pytest

from pitchprob.evaluation.latency import (
    Observation,
    classify_response,
    detect_moves,
    latency_summary,
    median_gap,
)

_T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _series(*pairs: tuple[int, float]) -> list[Observation]:
    """(hours after T0, probability) -> observations."""
    return [Observation(at=_T0 + timedelta(hours=h), value=v) for h, v in pairs]


class TestDetectMoves:
    def test_only_changes_at_or_above_the_threshold_count(self) -> None:
        series = _series((0, 0.500), (1, 0.505), (2, 0.530), (3, 0.529))
        moves = detect_moves(series, min_move=0.02)
        assert len(moves) == 1
        move = moves[0]
        assert move.at == _T0 + timedelta(hours=2)
        assert move.before == pytest.approx(0.505)
        assert move.after == pytest.approx(0.530)
        assert move.delta == pytest.approx(0.025)

    def test_downward_moves_are_detected_and_signed(self) -> None:
        moves = detect_moves(_series((0, 0.60), (1, 0.55)), min_move=0.02)
        assert moves[0].delta == pytest.approx(-0.05)

    def test_a_flat_series_has_no_moves(self) -> None:
        assert detect_moves(_series((0, 0.5), (1, 0.5), (2, 0.5)), min_move=0.01) == []

    def test_fewer_than_two_observations_cannot_move(self) -> None:
        assert detect_moves(_series((0, 0.5)), min_move=0.01) == []

    def test_observations_must_be_ordered(self) -> None:
        out_of_order = [
            Observation(at=_T0 + timedelta(hours=1), value=0.5),
            Observation(at=_T0, value=0.6),
        ]
        with pytest.raises(ValueError, match="chronological"):
            detect_moves(out_of_order, min_move=0.01)


class TestClassifyResponse:
    """A move at T0+2h of +0.025; the follower's baseline is its last value
    at or before that instant."""

    MOVE = detect_moves(
        _series((0, 0.500), (2, 0.530)), min_move=0.02
    )[0]

    def test_a_same_direction_move_reports_its_delay(self) -> None:
        follower = _series((1, 0.49), (3, 0.50), (4, 0.52))
        # baseline 0.49 (last at-or-before T0+2h); +0.03 first reached at
        # T0+4h, so the delay is 2h measured from the reference move.
        response = classify_response(self.MOVE, follower, threshold=0.02)
        assert response.direction == "with"
        assert response.latency == timedelta(hours=2)

    def test_a_move_below_the_threshold_is_not_a_reaction(self) -> None:
        follower = _series((1, 0.49), (5, 0.50))
        response = classify_response(self.MOVE, follower, threshold=0.02)
        assert response.direction == "none"
        assert response.latency is None

    def test_never_reacting_is_censored_not_zero(self) -> None:
        follower = _series((1, 0.49), (3, 0.49), (6, 0.49))
        response = classify_response(self.MOVE, follower, threshold=0.02)
        assert response.direction == "none"
        assert response.latency is None

    def test_an_opposite_move_is_recorded_as_against(self) -> None:
        follower = _series((1, 0.49), (3, 0.45))
        response = classify_response(self.MOVE, follower, threshold=0.02)
        assert response.direction == "against"
        assert response.latency == timedelta(hours=1)

    def test_observations_after_the_deadline_are_ignored(self) -> None:
        """Kickoff ends the measurement: a book that "reacts" in-play has
        not reacted to anything we could have bet on."""
        follower = _series((1, 0.49), (10, 0.55))
        response = classify_response(
            self.MOVE, follower, threshold=0.02,
            deadline=_T0 + timedelta(hours=5),
        )
        assert response.direction == "none"

    def test_a_follower_with_no_prior_observation_has_no_baseline(self) -> None:
        """Without a price from before the reference moved, there is nothing
        to measure a reaction against — that is missing data, not speed."""
        follower = _series((3, 0.55), (4, 0.60))
        response = classify_response(self.MOVE, follower, threshold=0.02)
        assert response.direction == "unobserved"
        assert response.latency is None


class TestLatencySummary:
    def _responses(self):
        return [
            ("betclic", "1x2", classify_response(
                TestClassifyResponse.MOVE, _series((1, 0.49), (3, 0.52)),
                threshold=0.02)),
            ("betclic", "1x2", classify_response(
                TestClassifyResponse.MOVE, _series((1, 0.49), (7, 0.52)),
                threshold=0.02)),
            # never reacts: censored
            ("betclic", "1x2", classify_response(
                TestClassifyResponse.MOVE, _series((1, 0.49), (9, 0.49)),
                threshold=0.02)),
            ("sts", "1x2", classify_response(
                TestClassifyResponse.MOVE, _series((1, 0.49), (3, 0.45)),
                threshold=0.02)),
        ]

    def test_median_is_taken_over_reactions_only_and_censoring_is_reported(
        self,
    ) -> None:
        stats = {s.bookmaker: s for s in latency_summary(self._responses())}
        betclic = stats["betclic"]
        assert betclic.observed == 3
        assert betclic.reacted == 2
        assert betclic.reacted_frac == pytest.approx(2 / 3)
        # 1h and 5h -> median 3h. The censored third case must NOT enter the
        # median; including it as, say, 0 would make this book look fast.
        assert betclic.median_latency == timedelta(hours=3)

    def test_a_book_that_only_moved_against_has_no_median(self) -> None:
        stats = {s.bookmaker: s for s in latency_summary(self._responses())}
        sts = stats["sts"]
        assert sts.against == 1
        assert sts.reacted == 0
        assert sts.median_latency is None

    def test_unobserved_cases_are_excluded_from_the_denominator(self) -> None:
        responses = [
            ("betclic", "1x2", classify_response(
                TestClassifyResponse.MOVE, _series((3, 0.55)), threshold=0.02)),
        ]
        stats = latency_summary(responses)
        assert stats[0].observed == 0
        assert stats[0].unobserved == 1
        assert stats[0].reacted_frac is None


class TestMedianGap:
    def test_gap_is_the_measurement_floor(self) -> None:
        """A daily tape cannot resolve a two-hour reaction; the report leans
        on this number to say so instead of publishing a latency."""
        assert median_gap(_series((0, 0.5), (24, 0.5), (48, 0.5))) == timedelta(
            hours=24
        )

    def test_a_single_observation_has_no_gap(self) -> None:
        assert median_gap(_series((0, 0.5))) is None
