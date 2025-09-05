import pytest
from ota_client import OtaClient, OTAError


class Resp:
    def __init__(self, status_code=404, body="Not Found"):
        self.status_code = status_code
        self.text = body
    def close(self):
        pass


def test_get_json_http_error():
    client = OtaClient({"owner": "o", "repo": "r"})
    client._get = lambda url, raw=False: Resp()
    with pytest.raises(OTAError) as excinfo:
        client._get_json("http://example.com")
    msg = str(excinfo.value)
    assert "404" in msg
    assert "Not Found" in msg
