"""Cloudflare bypass utilities."""


class BypassCancelledError(Exception):
    """Raised when a bypass operation is cancelled."""


class ChallengeNotSolvedError(Exception):
    """Raised when a bypasser ran but the site still answered with a challenge.

    Distinct from a bypasser that is broken or unreachable, which is what every
    "the bypass failed" message used to say. A solver can do its job perfectly and
    still be handed something it cannot clear - DDoS-Guard's manual CAPTCHA page is
    the case from #1292 - and telling the user to go check that FlareSolverr is
    reachable sends them to fix a service that is working.
    """
