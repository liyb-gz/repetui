import threading

from repetui.redraw import TERMINAL_MODE_SEQUENCES, replay_terminal_modes


class FakeDriver:
    def __init__(self) -> None:
        self.written: list[str] = []
        self.flushed = 0

    def write(self, data: str) -> None:
        self.written.append(data)

    def flush(self) -> None:
        self.flushed += 1


def test_replay_writes_exactly_the_mode_sequences_and_starts_no_threads() -> None:
    before = threading.active_count()
    driver = FakeDriver()
    replay_terminal_modes(driver)
    assert "".join(driver.written) == TERMINAL_MODE_SEQUENCES
    assert driver.flushed == 1 and threading.active_count() == before


def test_sequences_cover_alt_screen_mouse_paste_focus_and_cursor() -> None:
    for seq in ("\x1b[?1049h", "\x1b[?1000h", "\x1b[?1003h", "\x1b[?1015h", "\x1b[?1006h",
                "\x1b[?25l", "\x1b[?1004h", "\x1b[>1u", "\x1b[?2004h", "\x1b[?7l"):
        assert seq in TERMINAL_MODE_SEQUENCES
