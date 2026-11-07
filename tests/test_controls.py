import pytest

from repetui.controls import BindingConflict, ReviewAction, ReviewControls


def test_review_controls_start_with_terminal_defaults() -> None:
    controls = ReviewControls.defaults()

    assert controls.binding(ReviewAction.REVEAL_GOOD) == "enter"
    assert controls.binding(ReviewAction.AGAIN) == "1"
    assert controls.binding(ReviewAction.HARD) == "2"
    assert controls.binding(ReviewAction.GOOD) == "3"
    assert controls.binding(ReviewAction.EASY) == "4"
    assert controls.binding(ReviewAction.UNDO) == "u"
    assert controls.binding(ReviewAction.BURY) == "b"
    assert controls.binding(ReviewAction.SUSPEND) == "x"
    assert controls.binding(ReviewAction.FLAG) == "f"
    assert controls.binding(ReviewAction.READINGS) == "r"
    assert controls.binding(ReviewAction.REPLAY_AUDIO) == "p"
    assert controls.binding(ReviewAction.VIEW_IMAGE) == "v"
    assert controls.binding(ReviewAction.SYNC) == "s"


def test_existing_r_binding_is_preserved_when_readings_action_is_added() -> None:
    controls = ReviewControls.from_saved({"again": "r", "sync": "s"})

    assert controls.binding(ReviewAction.AGAIN) == "r"
    assert controls.binding(ReviewAction.READINGS) is None


def test_existing_p_binding_is_preserved_when_replay_action_is_added() -> None:
    controls = ReviewControls.from_saved({"again": "p"})

    assert controls.binding(ReviewAction.AGAIN) == "p"
    assert controls.binding(ReviewAction.REPLAY_AUDIO) is None


def test_existing_v_binding_is_preserved_when_view_action_is_added() -> None:
    controls = ReviewControls.from_saved({"again": "v"})

    assert controls.binding(ReviewAction.AGAIN) == "v"
    assert controls.binding(ReviewAction.VIEW_IMAGE) is None


def test_binding_conflict_requires_confirmation_and_unbinds_displaced_action() -> None:
    controls = ReviewControls.defaults()

    with pytest.raises(BindingConflict) as conflict:
        controls.with_binding(ReviewAction.UNDO, "b")

    assert conflict.value.action is ReviewAction.BURY
    assert controls.binding(ReviewAction.UNDO) == "u"
    assert controls.binding(ReviewAction.BURY) == "b"

    replaced = controls.with_binding(ReviewAction.UNDO, "b", replace=True)

    assert replaced.binding(ReviewAction.UNDO) == "b"
    assert replaced.binding(ReviewAction.BURY) is None


def test_selected_action_can_restore_its_default_binding() -> None:
    customized = ReviewControls.defaults().with_binding(ReviewAction.UNDO, "w")

    restored = customized.with_default(ReviewAction.UNDO)

    assert restored.binding(ReviewAction.UNDO) == "u"


def test_new_actions_have_defaults() -> None:
    controls = ReviewControls.defaults()
    assert controls.binding(ReviewAction.TOGGLE_AUDIO) == "m"
    assert controls.binding(ReviewAction.TOGGLE_IMAGES) == "i"
    assert controls.binding(ReviewAction.MEDIA_PROGRESS) == "S"


@pytest.mark.parametrize(
    ("taken", "action"),
    [
        ("m", ReviewAction.TOGGLE_AUDIO),
        ("i", ReviewAction.TOGGLE_IMAGES),
        ("S", ReviewAction.MEDIA_PROGRESS),
    ],
)
def test_saved_key_wins_and_new_action_starts_unbound(
    taken: str, action: ReviewAction
) -> None:
    controls = ReviewControls.from_saved({"again": taken})
    assert controls.binding(ReviewAction.AGAIN) == taken
    assert controls.binding(action) is None
    assert controls.binding(ReviewAction.HARD) == "2"


def test_ctrl_l_is_fixed() -> None:
    from repetui.controls import is_supported_control_key

    assert not is_supported_control_key("ctrl+l")


def test_text_size_defaults_to_z_and_yields_z_to_an_older_saved_binding() -> None:
    assert ReviewControls.defaults().binding(ReviewAction.TEXT_SIZE) == "z"
    # someone who already bound z to undo keeps it; text size starts unbound
    migrated = ReviewControls.from_saved({"undo": "z"})
    assert migrated.binding(ReviewAction.UNDO) == "z"
    assert migrated.binding(ReviewAction.TEXT_SIZE) is None
