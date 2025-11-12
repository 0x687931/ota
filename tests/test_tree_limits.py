import pytest
from ota import OTA, OTAError


class Resp:
    """Mock response object for testing"""
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data

    def close(self):
        pass


class RespWithContentLength:
    """Mock response with Content-Length header"""
    def __init__(self, size_bytes):
        self.headers = {"Content-Length": str(size_bytes)}
        self._data = None

    def json(self):
        # Should not be called if size check happens first
        raise RuntimeError("Should not parse JSON when size limit exceeded")

    def close(self):
        pass


def test_fetch_tree_normal_size(monkeypatch, tmp_path):
    """Test that fetch_tree succeeds with tree under file count limit"""
    monkeypatch.chdir(tmp_path)

    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": 200,  # Default or custom limit
    }
    client = OTA(cfg)

    # Create a tree with 100 files (well under limit)
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 1024,
                "sha": "a" * 40
            }
            for i in range(100)
        ]
    }

    # Mock _get to return our tree
    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should succeed without raising
    result = client.fetch_tree("commit_sha_123")
    assert len(result) == 100
    assert result[0]["path"] == "file_0.py"


def test_fetch_tree_too_many_files(monkeypatch, tmp_path):
    """Test that fetch_tree raises OTAError when tree exceeds file count limit"""
    monkeypatch.chdir(tmp_path)

    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": 200,  # Set explicit limit
    }
    client = OTA(cfg)

    # Create a tree with 500 files (over limit)
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 1024,
                "sha": "a" * 40
            }
            for i in range(500)
        ]
    }

    # Mock _get to return our tree
    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should raise OTAError due to file count
    with pytest.raises(OTAError) as excinfo:
        client.fetch_tree("commit_sha_123")

    msg = str(excinfo.value)
    assert "too many files" in msg.lower() or "file count" in msg.lower()
    assert "500" in msg  # Should mention actual count
    assert "200" in msg  # Should mention limit


def test_fetch_tree_size_limit_via_header(monkeypatch, tmp_path):
    """Test that fetch_tree raises OTAError when Content-Length exceeds size limit"""
    monkeypatch.chdir(tmp_path)

    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_bytes": 500_000,  # 500KB limit
    }
    client = OTA(cfg)

    # Create mock response with Content-Length of 2MB (over limit)
    large_size = 2_000_000

    # Mock _get to return response with large Content-Length
    monkeypatch.setattr(
        client,
        "_get",
        lambda url, raw=False: RespWithContentLength(large_size)
    )

    # Should raise OTAError due to size limit
    with pytest.raises(OTAError) as excinfo:
        client.fetch_tree("commit_sha_123")

    msg = str(excinfo.value)
    assert "too large" in msg.lower() or "size" in msg.lower()
    # Should mention the size issue
    assert "500" in msg or "2000000" in msg or "2.0" in msg


def test_fetch_tree_custom_limits(monkeypatch, tmp_path):
    """Test that custom configuration limits are respected"""
    monkeypatch.chdir(tmp_path)

    # Test with custom lower file limit
    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": 50,  # Custom lower limit
    }
    client = OTA(cfg)

    # Create tree with 75 files (over custom limit)
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 100,
                "sha": "b" * 40
            }
            for i in range(75)
        ]
    }

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should raise with custom limit
    with pytest.raises(OTAError) as excinfo:
        client.fetch_tree("commit_sha_123")

    msg = str(excinfo.value)
    assert "50" in msg  # Should mention custom limit


def test_fetch_tree_custom_limits_passes(monkeypatch, tmp_path):
    """Test that trees under custom limits succeed"""
    monkeypatch.chdir(tmp_path)

    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": 50,
    }
    client = OTA(cfg)

    # Create tree with 30 files (under custom limit)
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 100,
                "sha": "c" * 40
            }
            for i in range(30)
        ]
    }

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should succeed
    result = client.fetch_tree("commit_sha_123")
    assert len(result) == 30


def test_fetch_tree_helpful_error_message(monkeypatch, tmp_path):
    """Test that error message includes helpful guidance"""
    monkeypatch.chdir(tmp_path)

    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": 100,
    }
    client = OTA(cfg)

    # Create tree that exceeds limit
    tree_data = {
        "tree": [
            {
                "path": f"dir/subdir/file_{i}.py",
                "type": "blob",
                "size": 1024,
                "sha": "d" * 40
            }
            for i in range(150)
        ]
    }

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    with pytest.raises(OTAError) as excinfo:
        client.fetch_tree("commit_sha_123")

    msg = str(excinfo.value)

    # Error message should be helpful and include:
    # 1. Clear indication of what went wrong
    assert "tree" in msg.lower() or "repository" in msg.lower()

    # 2. Actual count/size that exceeded limit
    assert "150" in msg

    # 3. The limit that was exceeded
    assert "100" in msg

    # 4. Guidance on how to fix (at least one of these)
    helpful_keywords = [
        "allow",
        "filter",
        "reduce",
        "config",
        "limit",
        "narrow",
        "specify"
    ]
    assert any(keyword in msg.lower() for keyword in helpful_keywords), \
        f"Error message should include guidance. Got: {msg}"


def test_fetch_tree_default_limits(monkeypatch, tmp_path):
    """Test that default limits are used when not specified in config"""
    monkeypatch.chdir(tmp_path)

    # Config without explicit limits - should use defaults
    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
    }
    client = OTA(cfg)

    # Create a reasonably sized tree (should work with defaults)
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 500,
                "sha": "e" * 40
            }
            for i in range(50)
        ]
    }

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should succeed with default limits
    result = client.fetch_tree("commit_sha_123")
    assert len(result) == 50


def test_fetch_tree_empty_tree(monkeypatch, tmp_path):
    """Test that empty trees are handled correctly"""
    monkeypatch.chdir(tmp_path)

    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
    }
    client = OTA(cfg)

    # Empty tree
    tree_data = {"tree": []}

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should succeed
    result = client.fetch_tree("commit_sha_123")
    assert len(result) == 0


def test_fetch_tree_at_exact_limit(monkeypatch, tmp_path):
    """Test tree with exactly the maximum allowed files"""
    monkeypatch.chdir(tmp_path)

    limit = 100
    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": limit,
    }
    client = OTA(cfg)

    # Create tree with exactly the limit
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 100,
                "sha": "f" * 40
            }
            for i in range(limit)
        ]
    }

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should succeed (at limit, not over)
    result = client.fetch_tree("commit_sha_123")
    assert len(result) == limit


def test_fetch_tree_one_over_limit(monkeypatch, tmp_path):
    """Test tree with one file over the limit"""
    monkeypatch.chdir(tmp_path)

    limit = 100
    cfg = {
        "owner": "test_owner",
        "repo": "test_repo",
        "max_tree_files": limit,
    }
    client = OTA(cfg)

    # Create tree with one more than limit
    tree_data = {
        "tree": [
            {
                "path": f"file_{i}.py",
                "type": "blob",
                "size": 100,
                "sha": "g" * 40
            }
            for i in range(limit + 1)
        ]
    }

    monkeypatch.setattr(client, "_get", lambda url, raw=False: Resp(tree_data))

    # Should fail
    with pytest.raises(OTAError) as excinfo:
        client.fetch_tree("commit_sha_123")

    msg = str(excinfo.value)
    assert str(limit + 1) in msg
    assert str(limit) in msg
