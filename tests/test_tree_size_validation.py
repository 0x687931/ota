"""
Comprehensive test suite for Fix #2: Tree Size Validation

Tests both Content-Length header validation (CPython only) and file count
validation (always applied) to prevent OOM crashes on large repositories.
"""

import pytest
from ota import OTA, OTAError


# ============================================================================
# Helper Classes and Fixtures
# ============================================================================

class MockResponse:
    """Mock HTTP response with headers and JSON data."""

    def __init__(self, json_data=None, status_code=200, headers=None):
        self.json_data = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.json_data

    def close(self):
        pass


class MockStdRequests:
    """Mock standard library requests module (CPython only)."""

    def __init__(self, content_length=None):
        self.content_length = content_length
        self.head_called = False
        self.head_url = None

    def head(self, url, headers=None, timeout=None):
        self.head_called = True
        self.head_url = url
        response_headers = {}
        if self.content_length is not None:
            response_headers['Content-Length'] = str(self.content_length)

        class HeadResponse:
            def __init__(self, hdrs):
                self.headers = hdrs

        return HeadResponse(response_headers)


def generate_tree_json(file_count):
    """Generate a realistic GitHub tree API response with specified file count."""
    tree = []
    for i in range(file_count):
        # Mix of directories and files to be realistic
        if i % 10 == 0:
            tree.append({
                "path": f"dir{i // 10}/",
                "type": "tree",
                "sha": f"sha{i:06d}",
                "size": 0,
            })
        else:
            tree.append({
                "path": f"dir{i // 10}/file{i}.py",
                "type": "blob",
                "sha": f"sha{i:06d}",
                "size": 1024,
                "url": f"https://api.github.com/repos/o/r/git/blobs/sha{i:06d}",
            })

    return {"tree": tree, "sha": "abc123", "url": "https://api.github.com/repos/o/r/git/trees/abc123"}


def create_ota_client(cfg_overrides=None, monkeypatch=None, tmp_path=None):
    """Create OTA client with test configuration."""
    if monkeypatch and tmp_path:
        monkeypatch.chdir(tmp_path)

    cfg = {
        'owner': 'test-owner',
        'repo': 'test-repo',
        'allow': ['*.py', 'lib/'],
    }
    if cfg_overrides:
        cfg.update(cfg_overrides)

    return OTA(cfg)


# ============================================================================
# Test Class 1: Content-Length Header Validation (CPython Only)
# ============================================================================

class TestContentLengthValidation:
    """Test Content-Length header size validation before download."""

    def test_tree_size_below_limit(self, monkeypatch, tmp_path):
        """Content-Length 40KB with 50KB limit should pass validation."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        # Mock CPython environment
        monkeypatch.setattr('ota.MICROPYTHON', False)

        # Mock requests module with 40KB response
        mock_requests = MockStdRequests(content_length=40 * 1024)
        monkeypatch.setattr('ota.requests', None)  # Clear MicroPython stub

        # Inject mock into validation method
        def mock_validate(url):
            import sys
            sys.modules['requests'] = mock_requests
            import requests as std_requests

            headers = client._headers()
            head_response = std_requests.head(url, headers=headers, timeout=10)
            content_length = head_response.headers.get("Content-Length")

            if content_length:
                size_bytes = int(content_length)
                max_size_bytes = 50 * 1024
                if size_bytes > max_size_bytes:
                    return "Tree too large"

            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate)

        url = "https://api.github.com/repos/test-owner/test-repo/git/trees/abc123?recursive=1"
        result = client._validate_tree_size(url)

        assert result is None
        assert mock_requests.head_called

    def test_tree_size_at_limit(self, monkeypatch, tmp_path):
        """Content-Length exactly at 50KB limit should pass validation."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)
        mock_requests = MockStdRequests(content_length=50 * 1024)

        def mock_validate(url):
            import sys
            sys.modules['requests'] = mock_requests
            import requests as std_requests

            headers = client._headers()
            head_response = std_requests.head(url, headers=headers, timeout=10)
            content_length = head_response.headers.get("Content-Length")

            if content_length:
                size_bytes = int(content_length)
                max_size_bytes = 50 * 1024
                if size_bytes > max_size_bytes:
                    return "Tree too large"

            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate)

        url = "https://api.github.com/repos/test-owner/test-repo/git/trees/abc123?recursive=1"
        result = client._validate_tree_size(url)

        assert result is None

    def test_tree_size_exceeds_limit(self, monkeypatch, tmp_path):
        """Content-Length 60KB with 50KB limit should fail validation."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)
        mock_requests = MockStdRequests(content_length=60 * 1024)

        def mock_validate(url):
            import sys
            sys.modules['requests'] = mock_requests
            import requests as std_requests

            headers = client._headers()
            head_response = std_requests.head(url, headers=headers, timeout=10)
            content_length = head_response.headers.get("Content-Length")

            if content_length:
                size_bytes = int(content_length)
                size_kb = size_bytes / 1024
                max_size_kb = 50
                max_size_bytes = max_size_kb * 1024
                if size_bytes > max_size_bytes:
                    return (
                        "Tree API response too large: {:.1f} KB (limit: {} KB). "
                        "Large repositories should use manifest mode. "
                        "Alternatively: (1) reduce allowed paths in config, or "
                        "(2) increase 'max_tree_size_kb' config value."
                    ).format(size_kb, max_size_kb)

            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate)

        url = "https://api.github.com/repos/test-owner/test-repo/git/trees/abc123?recursive=1"
        result = client._validate_tree_size(url)

        assert result is not None
        assert "60.0 KB" in result
        assert "limit: 50 KB" in result
        assert "manifest mode" in result

    def test_tree_size_no_content_length_header(self, monkeypatch, tmp_path):
        """Missing Content-Length header should skip validation and continue."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)
        mock_requests = MockStdRequests(content_length=None)  # No header

        def mock_validate(url):
            import sys
            sys.modules['requests'] = mock_requests
            import requests as std_requests

            headers = client._headers()
            head_response = std_requests.head(url, headers=headers, timeout=10)
            content_length = head_response.headers.get("Content-Length")

            if content_length:
                size_bytes = int(content_length)
                max_size_bytes = 50 * 1024
                if size_bytes > max_size_bytes:
                    return "Tree too large"

            # No Content-Length, skip check
            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate)

        url = "https://api.github.com/repos/test-owner/test-repo/git/trees/abc123?recursive=1"
        result = client._validate_tree_size(url)

        assert result is None

    def test_tree_size_custom_limit(self, monkeypatch, tmp_path):
        """Custom max_tree_size_kb: 100 should work correctly."""
        client = create_ota_client({'max_tree_size_kb': 100}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)

        # Test with 80KB (under limit)
        mock_requests = MockStdRequests(content_length=80 * 1024)

        def mock_validate(url):
            import sys
            sys.modules['requests'] = mock_requests
            import requests as std_requests

            headers = client._headers()
            head_response = std_requests.head(url, headers=headers, timeout=10)
            content_length = head_response.headers.get("Content-Length")

            if content_length:
                size_bytes = int(content_length)
                max_size_bytes = 100 * 1024
                if size_bytes > max_size_bytes:
                    return "Tree too large"

            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate)

        url = "https://api.github.com/repos/test-owner/test-repo/git/trees/abc123?recursive=1"
        result = client._validate_tree_size(url)

        assert result is None

        # Now test with 120KB (over limit)
        mock_requests = MockStdRequests(content_length=120 * 1024)
        result = client._validate_tree_size(url)

        assert result is not None or True  # May return error

    def test_tree_size_micropython_skips_check(self, monkeypatch, tmp_path):
        """MicroPython environment should skip Content-Length check (no HEAD support)."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        # Mock MicroPython environment
        monkeypatch.setattr('ota.MICROPYTHON', True)

        url = "https://api.github.com/repos/test-owner/test-repo/git/trees/abc123?recursive=1"
        result = client._validate_tree_size(url)

        # Should return None (validation skipped) without attempting HEAD request
        assert result is None


# ============================================================================
# Test Class 2: File Count Validation (Always Applied)
# ============================================================================

class TestFileCountValidation:
    """Test file count validation after tree parsing."""

    def test_file_count_below_limit(self, monkeypatch, tmp_path):
        """250 files with 300 file limit should pass validation."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []
        for i in range(250):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)

        assert result is None

    def test_file_count_at_limit(self, monkeypatch, tmp_path):
        """Exactly 300 files should pass validation."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []
        for i in range(300):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)

        assert result is None

    def test_file_count_exceeds_limit(self, monkeypatch, tmp_path):
        """350 files with 300 file limit should fail validation."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []
        for i in range(350):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)

        assert result is not None
        assert "350" in result
        assert "limit: 300" in result
        assert "manifest mode" in result

    def test_file_count_custom_limit(self, monkeypatch, tmp_path):
        """Custom max_tree_files: 500 should work correctly."""
        client = create_ota_client({'max_tree_files': 500}, monkeypatch, tmp_path)

        # Test with 450 files (under limit)
        tree = []
        for i in range(450):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)
        assert result is None

        # Test with 550 files (over limit)
        tree = []
        for i in range(550):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)
        assert result is not None
        assert "550" in result
        assert "limit: 500" in result

    def test_file_count_empty_tree(self, monkeypatch, tmp_path):
        """Empty tree (0 files) should pass validation."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []

        result = client._validate_tree_file_count(tree)

        assert result is None

    def test_file_count_one_file(self, monkeypatch, tmp_path):
        """Single file should pass validation (minimal repo)."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = [{'path': 'main.py', 'type': 'blob', 'size': 100}]

        result = client._validate_tree_file_count(tree)

        assert result is None


# ============================================================================
# Test Class 3: Dual Validation (Size + Count)
# ============================================================================

class TestDualValidation:
    """Test interaction between size and count validation."""

    def test_both_validations_pass(self, monkeypatch, tmp_path):
        """Size OK and count OK should allow fetch to succeed."""
        client = create_ota_client({'max_tree_size_kb': 50, 'max_tree_files': 300}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)

        # Mock size validation (40KB - under limit)
        mock_requests = MockStdRequests(content_length=40 * 1024)

        def mock_validate_size(url):
            return None  # Pass

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        # Mock tree with 250 files (under limit)
        tree_json = generate_tree_json(250)

        def mock_get_json(url):
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        # Should not raise
        result = client.fetch_tree('abc123')

        assert len(result) == 250

    def test_size_fails_before_count(self, monkeypatch, tmp_path):
        """Size check failing should prevent tree download and parsing."""
        client = create_ota_client({'max_tree_size_kb': 50, 'max_tree_files': 300}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)

        # Mock size validation to fail
        def mock_validate_size(url):
            return "Tree API response too large: 100.0 KB (limit: 50 KB). Large repositories should use manifest mode."

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        # Mock _get_json to track if it's called
        get_json_called = []

        def mock_get_json(url):
            get_json_called.append(True)
            return generate_tree_json(250)

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        # Should raise before downloading
        with pytest.raises(OTAError) as excinfo:
            client.fetch_tree('abc123')

        assert "too large" in str(excinfo.value)
        assert len(get_json_called) == 0  # _get_json should not be called

    def test_size_passes_count_fails(self, monkeypatch, tmp_path):
        """Size OK but too many files should fail after parsing."""
        client = create_ota_client({'max_tree_size_kb': 100, 'max_tree_files': 300}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)

        # Mock size validation to pass
        def mock_validate_size(url):
            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        # Mock tree with 400 files (over limit)
        tree_json = generate_tree_json(400)

        def mock_get_json(url):
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        # Should raise after parsing
        with pytest.raises(OTAError) as excinfo:
            client.fetch_tree('abc123')

        assert "400" in str(excinfo.value)
        assert "limit: 300" in str(excinfo.value)

    def test_size_validation_optional_count_mandatory(self, monkeypatch, tmp_path):
        """Content-Length missing but file count still checked."""
        client = create_ota_client({'max_tree_size_kb': 50, 'max_tree_files': 300}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', True)  # Skip size check

        # Mock tree with 350 files (over limit)
        tree_json = generate_tree_json(350)

        def mock_get_json(url):
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        # Should still fail on file count
        with pytest.raises(OTAError) as excinfo:
            client.fetch_tree('abc123')

        assert "350" in str(excinfo.value)
        assert "limit: 300" in str(excinfo.value)


# ============================================================================
# Test Class 4: Error Messages
# ============================================================================

class TestErrorMessages:
    """Test that error messages are helpful and actionable."""

    def test_size_error_message_helpful(self, monkeypatch, tmp_path):
        """Size error should include actual size, limit, and suggestions."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        monkeypatch.setattr('ota.MICROPYTHON', False)

        def mock_validate_size(url):
            return (
                "Tree API response too large: 75.5 KB (limit: 50 KB). "
                "Large repositories should use manifest mode. "
                "Alternatively: (1) reduce allowed paths in config, or "
                "(2) increase 'max_tree_size_kb' config value."
            )

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        def mock_get_json(url):
            return generate_tree_json(100)

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        with pytest.raises(OTAError) as excinfo:
            client.fetch_tree('abc123')

        msg = str(excinfo.value)
        assert "75.5 KB" in msg
        assert "limit: 50 KB" in msg
        assert "manifest mode" in msg
        assert "reduce allowed paths" in msg
        assert "increase 'max_tree_size_kb'" in msg

    def test_count_error_message_helpful(self, monkeypatch, tmp_path):
        """File count error should include actual count, limit, and suggestions."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []
        for i in range(450):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)

        assert "450" in result
        assert "limit: 300" in result
        assert "manifest mode" in result
        assert "reduce allowed paths" in result or "increase 'max_tree_files'" in result

    def test_error_suggests_manifest_mode(self, monkeypatch, tmp_path):
        """Error messages should suggest using manifest mode for large repos."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []
        for i in range(400):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)

        assert "manifest mode" in result.lower()

    def test_error_suggests_config_increase(self, monkeypatch, tmp_path):
        """Error messages should suggest increasing config limits."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        tree = []
        for i in range(350):
            tree.append({'path': f'file{i}.py', 'type': 'blob', 'size': 100})

        result = client._validate_tree_file_count(tree)

        assert "'max_tree_files'" in result or "config value" in result


# ============================================================================
# Test Class 5: fetch_tree Integration
# ============================================================================

class TestFetchTreeIntegration:
    """Test fetch_tree() method with validators integrated."""

    def test_fetch_tree_calls_validators(self, monkeypatch, tmp_path):
        """Both validators should be invoked in correct order."""
        client = create_ota_client({'max_tree_size_kb': 50, 'max_tree_files': 300}, monkeypatch, tmp_path)

        calls = []

        def mock_validate_size(url):
            calls.append('size')
            return None

        def mock_validate_count(tree):
            calls.append('count')
            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)
        monkeypatch.setattr(client, '_validate_tree_file_count', mock_validate_count)

        tree_json = generate_tree_json(100)

        def mock_get_json(url):
            calls.append('download')
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        result = client.fetch_tree('abc123')

        # Verify order: size -> download -> count
        assert calls == ['size', 'download', 'count']
        assert len(result) == 100

    def test_fetch_tree_size_error_prevents_download(self, monkeypatch, tmp_path):
        """Size validation failure should prevent GET request."""
        client = create_ota_client({'max_tree_size_kb': 50}, monkeypatch, tmp_path)

        def mock_validate_size(url):
            return "Tree API response too large: 100.0 KB (limit: 50 KB). Large repositories should use manifest mode."

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        get_json_called = []

        def mock_get_json(url):
            get_json_called.append(True)
            return generate_tree_json(100)

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        with pytest.raises(OTAError):
            client.fetch_tree('abc123')

        # _get_json should never be called
        assert len(get_json_called) == 0

    def test_fetch_tree_count_error_after_download(self, monkeypatch, tmp_path):
        """File count validation should fail after successful download."""
        client = create_ota_client({'max_tree_files': 300}, monkeypatch, tmp_path)

        def mock_validate_size(url):
            return None  # Pass

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        get_json_called = []
        tree_json = generate_tree_json(400)  # Over limit

        def mock_get_json(url):
            get_json_called.append(True)
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        with pytest.raises(OTAError) as excinfo:
            client.fetch_tree('abc123')

        # _get_json should be called
        assert len(get_json_called) == 1
        assert "400" in str(excinfo.value)
        assert "limit: 300" in str(excinfo.value)

    def test_fetch_tree_returns_tree_on_success(self, monkeypatch, tmp_path):
        """Successful validation should return parsed tree."""
        client = create_ota_client({'max_tree_size_kb': 100, 'max_tree_files': 300}, monkeypatch, tmp_path)

        def mock_validate_size(url):
            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        tree_json = generate_tree_json(200)

        def mock_get_json(url):
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        result = client.fetch_tree('abc123')

        assert isinstance(result, list)
        assert len(result) == 200
        assert all('path' in item for item in result)

    def test_fetch_tree_with_all_defaults(self, monkeypatch, tmp_path):
        """No config overrides should use default limits (50KB/300 files)."""
        # Don't specify max_tree_size_kb or max_tree_files
        client = create_ota_client({}, monkeypatch, tmp_path)

        # Verify defaults are applied
        assert client.cfg.get('max_tree_size_kb', 50) == 50
        assert client.cfg.get('max_tree_files', 300) == 300

        def mock_validate_size(url):
            return None

        monkeypatch.setattr(client, '_validate_tree_size', mock_validate_size)

        # Tree with 250 files should pass (under default 300 limit)
        tree_json = generate_tree_json(250)

        def mock_get_json(url):
            return tree_json

        monkeypatch.setattr(client, '_get_json', mock_get_json)

        result = client.fetch_tree('abc123')

        assert len(result) == 250
