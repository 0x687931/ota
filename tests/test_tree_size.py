"""
Tests for tree size OOM protection (Fix #2).

Validates dual validation approach:
1. Content-Length header check (preemptive)
2. File count validation (post-parse)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from ota import OTA, OTAError


@pytest.fixture
def minimal_config():
    """Minimal valid configuration."""
    return {
        "owner": "test_owner",
        "repo": "test_repo",
        "ssid": "test_ssid",
        "password": "test_password",
        "channel": "developer",
        "allow": ["*.py"],
    }


@pytest.fixture
def config_with_limits(minimal_config):
    """Config with tree size protection enabled."""
    cfg = minimal_config.copy()
    cfg["max_tree_size_kb"] = 100
    cfg["max_tree_files"] = 250
    return cfg


@pytest.fixture
def config_with_debug(config_with_limits):
    """Config with debug logging enabled."""
    cfg = config_with_limits.copy()
    cfg["debug"] = True
    return cfg


def mock_response(content, status=200, headers=None):
    """Create a mock HTTP response."""
    mock = Mock()
    mock.status_code = status
    mock.headers = headers or {}
    mock.json = Mock(return_value=content)
    mock.close = Mock()
    return mock


class TestSmallRepoValidation:
    """Test that small repositories pass validation."""

    def test_small_tree_passes_all_checks(self, config_with_limits):
        """Small tree with valid size and file count passes."""
        ota = OTA(config_with_limits)

        # Mock small tree response (10 files, ~5KB)
        small_tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 500}
                for i in range(10)
            ]
        }

        mock_resp = mock_response(
            small_tree,
            headers={"Content-Length": "5000"}
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 10
            assert result[0]["path"] == "file0.py"

    def test_no_limits_allows_any_size(self, minimal_config):
        """Without limits configured, any tree size is accepted."""
        ota = OTA(minimal_config)

        # Mock large tree (1000 files)
        large_tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 100}
                for i in range(1000)
            ]
        }

        mock_resp = mock_response(
            large_tree,
            headers={"Content-Length": "500000"}  # 500KB
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 1000  # No error, limit not enforced


class TestContentLengthValidation:
    """Test Content-Length header-based size validation."""

    def test_large_response_rejected_by_header(self, config_with_limits):
        """Response exceeding max_tree_size_kb is rejected before parsing."""
        ota = OTA(config_with_limits)

        mock_resp = mock_response(
            {"tree": []},
            headers={"Content-Length": "200000"}  # 200KB > 100KB limit
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")

            assert "Response too large" in str(exc_info.value)
            assert "200000 bytes" in str(exc_info.value)
            assert "100 KB" in str(exc_info.value)
            # json() should not be called since we reject early
            mock_resp.json.assert_not_called()

    def test_case_insensitive_content_length_header(self, config_with_limits):
        """Content-Length header is case-insensitive."""
        ota = OTA(config_with_limits)

        # Test lowercase 'content-length'
        mock_resp = mock_response(
            {"tree": []},
            headers={"content-length": "200000"}
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")
            assert "Response too large" in str(exc_info.value)

    def test_missing_content_length_falls_back_to_count(self, config_with_limits):
        """When Content-Length is missing, file count validation still works."""
        ota = OTA(config_with_limits)

        # Response without Content-Length header but too many files
        large_tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 100}
                for i in range(300)  # Exceeds 250 file limit
            ]
        }

        mock_resp = mock_response(
            large_tree,
            headers={}  # No Content-Length
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")

            assert "too many files" in str(exc_info.value)
            assert "300" in str(exc_info.value)
            assert "250" in str(exc_info.value)


class TestFileCountValidation:
    """Test file count validation after parsing."""

    def test_too_many_files_rejected(self, config_with_limits):
        """Tree with too many files is rejected after parsing."""
        ota = OTA(config_with_limits)

        # Response within size limit but too many files
        large_tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 10}
                for i in range(300)  # Exceeds 250 file limit
            ]
        }

        # Small Content-Length (passes header check)
        mock_resp = mock_response(
            large_tree,
            headers={"Content-Length": "50000"}  # 50KB < 100KB limit
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")

            assert "too many files" in str(exc_info.value)
            assert "300" in str(exc_info.value)
            assert "250" in str(exc_info.value)

    def test_exactly_at_file_limit_passes(self, config_with_limits):
        """Tree with exactly max_tree_files is accepted."""
        ota = OTA(config_with_limits)

        tree_at_limit = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 10}
                for i in range(250)  # Exactly at limit
            ]
        }

        mock_resp = mock_response(
            tree_at_limit,
            headers={"Content-Length": "40000"}
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 250  # Should pass

    def test_one_over_file_limit_fails(self, config_with_limits):
        """Tree with max_tree_files + 1 is rejected."""
        ota = OTA(config_with_limits)

        tree_over_limit = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 10}
                for i in range(251)  # One over limit
            ]
        }

        mock_resp = mock_response(
            tree_over_limit,
            headers={"Content-Length": "40000"}
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")
            assert "251" in str(exc_info.value)


class TestCustomLimits:
    """Test custom limit configuration."""

    def test_custom_size_limit(self, minimal_config):
        """Custom max_tree_size_kb is respected."""
        cfg = minimal_config.copy()
        cfg["max_tree_size_kb"] = 50  # Custom 50KB limit

        ota = OTA(cfg)

        mock_resp = mock_response(
            {"tree": []},
            headers={"Content-Length": "60000"}  # 60KB > 50KB
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")
            assert "50 KB" in str(exc_info.value)

    def test_custom_file_limit(self, minimal_config):
        """Custom max_tree_files is respected."""
        cfg = minimal_config.copy()
        cfg["max_tree_files"] = 100  # Custom 100 file limit

        ota = OTA(cfg)

        tree_over_custom_limit = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 10}
                for i in range(150)  # Exceeds custom 100 limit
            ]
        }

        mock_resp = mock_response(
            tree_over_custom_limit,
            headers={"Content-Length": "20000"}
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            with pytest.raises(OTAError) as exc_info:
                ota.fetch_tree("abc123")
            assert "150" in str(exc_info.value)
            assert "100" in str(exc_info.value)

    def test_only_size_limit_configured(self, minimal_config):
        """Can configure only size limit without file limit."""
        cfg = minimal_config.copy()
        cfg["max_tree_size_kb"] = 50

        ota = OTA(cfg)

        # Large file count but small response size
        tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 1}
                for i in range(5000)  # Many files, no limit set
            ]
        }

        mock_resp = mock_response(
            tree,
            headers={"Content-Length": "30000"}  # Within 50KB limit
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 5000  # File count not enforced

    def test_only_file_limit_configured(self, minimal_config):
        """Can configure only file limit without size limit."""
        cfg = minimal_config.copy()
        cfg["max_tree_files"] = 100

        ota = OTA(cfg)

        # Small file count but large response size
        tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 1000}
                for i in range(50)  # Within 100 file limit
            ]
        }

        mock_resp = mock_response(
            tree,
            headers={"Content-Length": "500000"}  # 500KB, no limit set
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 50  # Size not enforced


class TestDebugLogging:
    """Test debug logging for validation."""

    def test_debug_logs_size_validation(self, config_with_debug):
        """Debug mode logs successful size validation."""
        ota = OTA(config_with_debug)

        small_tree = {"tree": [{"type": "blob", "path": "test.py", "size": 100}]}
        mock_resp = mock_response(small_tree, headers={"Content-Length": "5000"})

        with patch.object(ota, "_get", return_value=mock_resp):
            with patch.object(ota, "_debug") as mock_debug:
                ota.fetch_tree("abc123")

                # Check that size validation was logged
                log_calls = [str(call) for call in mock_debug.call_args_list]
                assert any("5000 bytes" in call and "100 KB limit" in call for call in log_calls)

    def test_debug_logs_file_count_validation(self, config_with_debug):
        """Debug mode logs successful file count validation."""
        ota = OTA(config_with_debug)

        tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 100}
                for i in range(50)
            ]
        }
        mock_resp = mock_response(tree, headers={"Content-Length": "10000"})

        with patch.object(ota, "_get", return_value=mock_resp):
            with patch.object(ota, "_debug") as mock_debug:
                ota.fetch_tree("abc123")

                # Check that file count validation was logged
                log_calls = [str(call) for call in mock_debug.call_args_list]
                assert any("50" in call and "250 file limit" in call for call in log_calls)


class TestBackwardCompatibility:
    """Test backward compatibility with existing configs."""

    def test_no_limits_in_config_works(self, minimal_config):
        """Config without max_tree_size_kb or max_tree_files works normally."""
        ota = OTA(minimal_config)

        tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 1000}
                for i in range(500)
            ]
        }
        mock_resp = mock_response(tree, headers={"Content-Length": "1000000"})

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 500  # No restrictions

    def test_null_limits_in_config_works(self, minimal_config):
        """Config with null values for limits works normally."""
        cfg = minimal_config.copy()
        cfg["max_tree_size_kb"] = None
        cfg["max_tree_files"] = None

        ota = OTA(cfg)

        tree = {
            "tree": [
                {"type": "blob", "path": "file%d.py" % i, "size": 1000}
                for i in range(500)
            ]
        }
        mock_resp = mock_response(tree, headers={"Content-Length": "1000000"})

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota.fetch_tree("abc123")
            assert len(result) == 500  # No restrictions


class TestOtherGetJsonCallers:
    """Test that other _get_json() callers still work without max_size_kb."""

    def test_get_json_without_max_size_parameter(self, minimal_config):
        """_get_json() can be called without max_size_kb parameter."""
        ota = OTA(minimal_config)

        response_data = {"some": "data"}
        mock_resp = mock_response(response_data, headers={"Content-Length": "50"})

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota._get_json("https://api.example.com/test")
            assert result == response_data

    def test_get_json_with_large_response_no_limit(self, minimal_config):
        """_get_json() without max_size_kb allows large responses."""
        ota = OTA(minimal_config)

        response_data = {"large": "data" * 10000}
        mock_resp = mock_response(
            response_data,
            headers={"Content-Length": "1000000"}  # 1MB
        )

        with patch.object(ota, "_get", return_value=mock_resp):
            result = ota._get_json("https://api.example.com/test")
            assert result == response_data  # No error
