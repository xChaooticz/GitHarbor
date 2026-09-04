from __future__ import annotations

import pytest

from githarbor.services.naming import (
    collision_destination_name,
    destination_name,
    safe_component,
)


def test_owned_keeps_repository_name() -> None:
    assert destination_name("someone", "my-project", 42, "owned") == "my-project"


def test_starred_names_are_readable_without_an_id_suffix() -> None:
    first = destination_name("user-a", "project", 42, "starred")
    second = destination_name("user-b", "project", 43, "starred")
    assert first == "user-a--project"
    assert second == "user-b--project"
    assert first != second


def test_stable_id_suffix_is_available_for_a_real_collision() -> None:
    assert destination_name("a b", "repo", 1, "starred") == destination_name(
        "a-b", "repo", 2, "starred"
    )
    assert collision_destination_name("a b", "repo", 1, "starred") != collision_destination_name(
        "a-b", "repo", 2, "starred"
    )


def test_name_is_bounded() -> None:
    assert len(destination_name("x" * 200, "y" * 300, 999, "starred")) <= 100
    assert len(collision_destination_name("x" * 200, "y" * 300, 999, "starred")) <= 100


def test_empty_component_is_rejected() -> None:
    with pytest.raises(ValueError):
        safe_component("../")
