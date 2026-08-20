from __future__ import annotations

import pytest

from githarbor.clients.gitea import DestinationSafetyError, GiteaClient, management_marker


def test_management_marker_uses_stable_identity() -> None:
    assert management_marker(123, "starred") == "GitHarbor managed; github-id:123; kind:starred"


def test_destination_safety_accepts_matching_marker() -> None:
    GiteaClient._verify_managed(
        {"description": f"{management_marker(123, 'starred')}; source:a/b"},
        management_marker(123, "starred"),
        "archive",
        "a--b--gh123",
    )


@pytest.mark.parametrize(
    "description",
    ["", "personal repository", management_marker(999, "starred"), management_marker(123, "owned")],
)
def test_destination_safety_rejects_unrelated_repository(description: str) -> None:
    with pytest.raises(DestinationSafetyError, match="Refusing destination"):
        GiteaClient._verify_managed(
            {"description": description},
            management_marker(123, "starred"),
            "archive",
            "a--b--gh123",
        )
