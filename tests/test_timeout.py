from ota_client import OtaClient


class DummyRequests:
    def __init__(self):
        self.timeout = None

    def get(self, url, headers=None, stream=None, timeout=None):
        self.timeout = timeout

        class Resp:
            status_code = 200

            def close(self):
                pass

        return Resp()


def test_get_uses_configured_timeouts(monkeypatch):
    dummy = DummyRequests()
    monkeypatch.setattr("ota_client.requests", dummy)
    client = OtaClient(
        {
            "owner": "o",
            "repo": "r",
            "connect_timeout_sec": 1,
            "http_timeout_sec": 2,
        }
    )
    client._get("http://example.com")
    assert dummy.timeout == (1, 2)


def test_get_uses_single_timeout_on_micropython(monkeypatch):
    dummy = DummyRequests()
    monkeypatch.setattr("ota_client.requests", dummy)
    monkeypatch.setattr("ota_client.MICROPYTHON", True)
    client = OtaClient(
        {
            "owner": "o",
            "repo": "r",
            "connect_timeout_sec": 1,
            "http_timeout_sec": 2,
        }
    )
    client._get("http://example.com")
    assert dummy.timeout == 2

