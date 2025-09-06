from ota import OTA


class ReqWithStream:
    def __init__(self):
        self.stream = None

    def get(self, url, headers=None, stream=None, timeout=None):
        self.stream = stream

        class Resp:
            status_code = 200

            def close(self):
                pass

        return Resp()


def test_get_passes_stream_when_supported(monkeypatch):
    dummy = ReqWithStream()
    monkeypatch.setattr("ota.requests", dummy)
    client = OTA({"owner": "o", "repo": "r"})
    client._get("http://example.com", raw=True)
    assert dummy.stream is True
    client._get("http://example.com", raw=False)
    assert dummy.stream is False


class ReqNoStream:
    def __init__(self):
        self.called = False

    def get(self, url, headers=None, timeout=None):
        self.called = True

        class Resp:
            status_code = 200

            def close(self):
                pass

        return Resp()


def test_get_omits_stream_when_not_supported(monkeypatch):
    dummy = ReqNoStream()
    monkeypatch.setattr("ota.requests", dummy)
    client = OTA({"owner": "o", "repo": "r"})
    client._get("http://example.com", raw=True)
    assert dummy.called
