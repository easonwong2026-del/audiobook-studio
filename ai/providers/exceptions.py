"""Safe, actionable exceptions raised while parsing remote model output."""


class ProviderOutputTruncatedError(RuntimeError):
    """The provider stopped before producing one complete batch JSON object."""


class ProviderOutputInvalidJsonError(RuntimeError):
    """The provider returned a complete response that is not valid batch JSON."""
