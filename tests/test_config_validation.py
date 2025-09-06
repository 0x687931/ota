import pytest
from ota_client import OtaClient


@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"repo": "r"},
        {"owner": "o"},
        {"owner": "", "repo": "r"},
        {"owner": "o", "repo": ""},
        {"owner": "YOUR_GITHUB_USERNAME", "repo": "r"},
        {"owner": "o", "repo": "YOUR_REPO_NAME"},
    ],
)

def test_invalid_owner_repo(cfg):
    with pytest.raises(ValueError):
        OtaClient(cfg)
