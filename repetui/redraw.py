"""Re-send terminal modes after a terminal re-attach (dtach) without restarting the driver."""

from __future__ import annotations

from typing import Protocol

# Copied from Textual 7.5.0 (pinned exactly in pyproject.toml)
# linux_driver.start_application_mode(), _enable_mouse_support(),
# _enable_bracketed_paste() and _disable_line_wrap(), in that order.
TERMINAL_MODE_SEQUENCES = (
    "\x1b[?1049h"                                         # alternate screen
    "\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h"        # mouse reporting
    "\x1b[?25l"                                           # hide cursor
    "\x1b[?1004h"                                         # focus events
    "\x1b[>1u"                                            # kitty keyboard protocol
    "\x1b[?2004h"                                         # bracketed paste
    "\x1b[?7l"                                            # no line wrap
)


class _Writable(Protocol):
    def write(self, data: str) -> None: ...
    def flush(self) -> None: ...


def replay_terminal_modes(driver: _Writable) -> None:
    """Write the mode sequences once.

    Never call start/resume_application_mode here (they spawn threads).
    """
    driver.write(TERMINAL_MODE_SEQUENCES)
    driver.flush()
