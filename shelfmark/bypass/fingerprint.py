"""Browser fingerprint profile management for bypass stealth."""

import random

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

COMMON_RESOLUTIONS = [
    (1920, 1080, 0.35),
    (1366, 768, 0.18),
    (1536, 864, 0.10),
    (1440, 900, 0.08),
    (1280, 720, 0.07),
    (1600, 900, 0.06),
    (1280, 800, 0.05),
    (2560, 1440, 0.04),
    (1680, 1050, 0.04),
    (1920, 1200, 0.03),
]

# Current screen size (module-level singleton)
_current_screen_size: tuple[int, int] | None = None


def get_screen_size() -> tuple[int, int]:
    global _current_screen_size
    if _current_screen_size is None:
        _current_screen_size = _generate_screen_size()
        logger.debug(
            "Generated initial screen size: %sx%s",
            _current_screen_size[0],
            _current_screen_size[1],
        )
    return _current_screen_size


def rotate_screen_size() -> tuple[int, int]:
    global _current_screen_size
    old_size = _current_screen_size
    _current_screen_size = _generate_screen_size()
    width, height = _current_screen_size

    if old_size:
        logger.info(
            "Rotated screen size: %sx%s -> %sx%s",
            old_size[0],
            old_size[1],
            width,
            height,
        )
    else:
        logger.info("Generated screen size: %sx%s", width, height)

    return _current_screen_size


def clear_screen_size() -> None:
    global _current_screen_size
    _current_screen_size = None


def _generate_screen_size() -> tuple[int, int]:
    resolutions = [(w, h) for w, h, _ in COMMON_RESOLUTIONS]
    weights = [weight for _, _, weight in COMMON_RESOLUTIONS]
    return random.choices(resolutions, weights=weights)[0]
