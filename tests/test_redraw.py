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


class InBandDriver(FakeDriver):
    """Textual 7.5.0 LinuxDriver state after the terminal confirmed mode 2048 (in-band resize)."""

    def __init__(self, in_band: bool, pixels: bool) -> None:
        super().__init__()
        self._in_band_window_resize = in_band
        self._mouse_pixels = pixels


def test_replay_reenables_in_band_resize_and_pixel_mouse_when_the_driver_uses_them() -> None:
    # A re-attached terminal (new herdr pane) starts with 2048/1016 off: without them Textual
    # ignores SIGWINCH (never re-lays out) and divides cell mouse coordinates by the pixel ratio.
    driver = InBandDriver(in_band=True, pixels=True)
    replay_terminal_modes(driver)
    written = "".join(driver.written)
    assert written.startswith(TERMINAL_MODE_SEQUENCES)
    assert written.endswith("\x1b[?2048h\x1b[?1016h")
    assert driver.flushed == 1


def test_replay_adds_only_the_modes_the_driver_enabled() -> None:
    driver = InBandDriver(in_band=True, pixels=False)
    replay_terminal_modes(driver)
    assert "".join(driver.written) == TERMINAL_MODE_SEQUENCES + "\x1b[?2048h"
    driver = InBandDriver(in_band=False, pixels=False)
    replay_terminal_modes(driver)
    assert "".join(driver.written) == TERMINAL_MODE_SEQUENCES
