"""Domain error hierarchy."""


class PitchprobError(Exception):
    """Base class for all pitchprob errors."""


class DataValidationError(PitchprobError):
    """A source row or file failed validation."""


class ModelNotFittedError(PitchprobError):
    """Prediction was requested from an unfitted model."""


class UnknownTeamError(PitchprobError):
    """A team name could not be resolved to a canonical team."""
