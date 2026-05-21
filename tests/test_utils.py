"""Unit tests for lance_ray.utils credential fallback functions.

This module loads utils.py directly via importlib to avoid importing the
full lance_ray package (which depends on lance / ray at import time).
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Module-level setup: load utils.py without importing lance_ray package
# ---------------------------------------------------------------------------

def _load_utils_module():
    """Load lance_ray.utils directly, stubbing out heavy dependencies."""
    repo_root = Path(__file__).resolve().parents[1]

    # Provide a minimal lance_ray package so relative imports in utils.py work
    package = ModuleType("lance_ray")
    package.__path__ = [str(repo_root / "lance_ray")]
    sys.modules.setdefault("lance_ray", package)

    # Stub lance (needed by _pylance_version)
    lance_stub = ModuleType("lance")
    lance_stub.__version__ = "5.0.0"
    sys.modules.setdefault("lance", lance_stub)

    # Stub lance_namespace
    ln = ModuleType("lance_namespace")
    ln.DescribeTableRequest = type(
        "DescribeTableRequest", (), {"__init__": lambda self, **kw: None}
    )
    sys.modules.setdefault("lance_namespace", ln)

    # Stub packaging.version
    pkg = sys.modules.get("packaging") or ModuleType("packaging")
    pkg_ver = sys.modules.get("packaging.version") or ModuleType("packaging.version")

    class _FakeVersion:
        def __init__(self, v):
            parts = v.split(".")
            self.major = int(parts[0])
            self.minor = int(parts[1]) if len(parts) > 1 else 0
            self.micro = int(parts[2]) if len(parts) > 2 else 0

    pkg_ver.parse = _FakeVersion
    pkg.version = pkg_ver
    sys.modules.setdefault("packaging", pkg)
    sys.modules.setdefault("packaging.version", pkg_ver)

    # Stub more_itertools (used on Python < 3.12)
    mi = ModuleType("more_itertools")
    mi.divide = lambda n, it: [list(it)]  # dummy
    sys.modules.setdefault("more_itertools", mi)

    spec = importlib.util.spec_from_file_location(
        "lance_ray.utils",
        repo_root / "lance_ray" / "utils.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["lance_ray.utils"] = module
    spec.loader.exec_module(module)
    return module


utils_mod = _load_utils_module()
get_namespace_kwargs_with_fallback = utils_mod.get_namespace_kwargs_with_fallback
get_write_fragments_kwargs_with_fallback = utils_mod.get_write_fragments_kwargs_with_fallback


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NS_KWARGS = {"namespace_client": "fake_client", "table_id": ["db", "tbl"]}
_WRITE_KWARGS = {"namespace_client": "fake_write_client", "table_id": ["db", "tbl"]}

_IMPL = "rest"
_PROPS = {"uri": "http://catalog"}
_TABLE_ID = ["db", "tbl"]

_MOD = "lance_ray.utils"


# ---------------------------------------------------------------------------
# get_namespace_kwargs_with_fallback
# ---------------------------------------------------------------------------


class TestGetNamespaceKwargsWithFallback:

    @patch(f"{_MOD}.get_namespace_kwargs", return_value=dict(_NS_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value={"ak": "ns_key"})
    def test_tier1_ns_creds_only(self, mock_resolve, mock_get_ns):
        """Tier 1: namespace vends creds, user provides nothing."""
        result = get_namespace_kwargs_with_fallback(_IMPL, _PROPS, _TABLE_ID)

        mock_resolve.assert_called_once_with(_IMPL, _PROPS, _TABLE_ID)
        mock_get_ns.assert_called_once_with(_IMPL, _PROPS, _TABLE_ID)
        assert "namespace_client" in result
        assert result["storage_options"] == {"ak": "ns_key"}

    @patch(f"{_MOD}.get_namespace_kwargs", return_value=dict(_NS_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value={"ak": "ns_key"})
    def test_tier1_merge_ns_overrides_user(self, mock_resolve, mock_get_ns):
        """Tier 1: ns creds override user on same key, user-only keys preserved."""
        result = get_namespace_kwargs_with_fallback(
            _IMPL, _PROPS, _TABLE_ID,
            user_storage_options={"ak": "user_key", "region": "cn"},
        )

        assert result["storage_options"] == {"ak": "ns_key", "region": "cn"}
        assert "namespace_client" in result

    @patch(f"{_MOD}.get_namespace_kwargs")
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_tier2_fallback_to_user(self, mock_resolve, mock_get_ns):
        """Tier 2: namespace returns nothing, use user creds, skip ns integration."""
        result = get_namespace_kwargs_with_fallback(
            _IMPL, _PROPS, _TABLE_ID,
            user_storage_options={"ak": "user_key"},
        )

        mock_get_ns.assert_not_called()
        assert "namespace_client" not in result
        assert result["storage_options"] == {"ak": "user_key"}

    @patch(f"{_MOD}.get_namespace_kwargs", return_value=dict(_NS_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_tier3_keep_ns_integration(self, mock_resolve, mock_get_ns):
        """Tier 3: no creds anywhere, keep ns integration for pylance."""
        result = get_namespace_kwargs_with_fallback(_IMPL, _PROPS, _TABLE_ID)

        mock_get_ns.assert_called_once()
        assert "namespace_client" in result
        assert "storage_options" not in result

    @patch(f"{_MOD}.get_namespace_kwargs", return_value=dict(_NS_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value={})
    def test_empty_dict_treated_as_no_creds(self, mock_resolve, mock_get_ns):
        """Empty dict from resolve is falsy, should behave like tier 2."""
        result = get_namespace_kwargs_with_fallback(
            _IMPL, _PROPS, _TABLE_ID,
            user_storage_options={"ak": "user_key"},
        )

        mock_get_ns.assert_not_called()
        assert "namespace_client" not in result
        assert result["storage_options"] == {"ak": "user_key"}

    @patch(f"{_MOD}.get_namespace_kwargs")
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_tier2_emits_info_log(self, mock_resolve, mock_get_ns, caplog):
        """Tier 2 should log an info message about the fallback."""
        import logging

        with caplog.at_level(logging.INFO, logger="lance_ray.utils"):
            get_namespace_kwargs_with_fallback(
                _IMPL, _PROPS, _TABLE_ID,
                user_storage_options={"ak": "user_key"},
            )
        assert any("did not vend credentials" in msg for msg in caplog.messages)

    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_no_namespace_params_returns_user_opts(self, mock_resolve):
        """No namespace_impl -> resolve returns None, result has user opts only."""
        result = get_namespace_kwargs_with_fallback(
            None, None, None,
            user_storage_options={"ak": "user_key"},
        )
        assert result == {"storage_options": {"ak": "user_key"}}


# ---------------------------------------------------------------------------
# get_write_fragments_kwargs_with_fallback
# ---------------------------------------------------------------------------


class TestGetWriteFragmentsKwargsWithFallback:

    @patch(f"{_MOD}.get_write_fragments_kwargs", return_value=dict(_WRITE_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value={"ak": "ns_key"})
    def test_tier1_ns_creds_only(self, mock_resolve, mock_get_wf):
        """Tier 1: namespace vends creds, user provides nothing."""
        result = get_write_fragments_kwargs_with_fallback(_IMPL, _PROPS, _TABLE_ID)

        mock_get_wf.assert_called_once_with(_IMPL, _PROPS, _TABLE_ID)
        assert "namespace_client" in result
        assert result["storage_options"] == {"ak": "ns_key"}

    @patch(f"{_MOD}.get_write_fragments_kwargs", return_value=dict(_WRITE_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value={"ak": "ns_key"})
    def test_tier1_merge_ns_overrides_user(self, mock_resolve, mock_get_wf):
        """Tier 1: ns creds override user on same key, user-only keys preserved."""
        result = get_write_fragments_kwargs_with_fallback(
            _IMPL, _PROPS, _TABLE_ID,
            user_storage_options={"ak": "user_key", "region": "cn"},
        )
        assert result["storage_options"] == {"ak": "ns_key", "region": "cn"}

    @patch(f"{_MOD}.get_write_fragments_kwargs")
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_tier2_fallback_to_user(self, mock_resolve, mock_get_wf):
        """Tier 2: namespace returns nothing, use user creds, skip ns integration."""
        result = get_write_fragments_kwargs_with_fallback(
            _IMPL, _PROPS, _TABLE_ID,
            user_storage_options={"ak": "user_key"},
        )

        mock_get_wf.assert_not_called()
        assert "namespace_client" not in result
        assert result["storage_options"] == {"ak": "user_key"}

    @patch(f"{_MOD}.get_write_fragments_kwargs", return_value=dict(_WRITE_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_tier3_keep_ns_integration(self, mock_resolve, mock_get_wf):
        """Tier 3: no creds anywhere, keep ns integration for pylance."""
        result = get_write_fragments_kwargs_with_fallback(_IMPL, _PROPS, _TABLE_ID)

        mock_get_wf.assert_called_once()
        assert "namespace_client" in result
        assert "storage_options" not in result

    @patch(f"{_MOD}.get_write_fragments_kwargs", return_value=dict(_WRITE_KWARGS))
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value={})
    def test_empty_dict_treated_as_no_creds(self, mock_resolve, mock_get_wf):
        """Empty dict from resolve is falsy, should behave like tier 2."""
        result = get_write_fragments_kwargs_with_fallback(
            _IMPL, _PROPS, _TABLE_ID,
            user_storage_options={"ak": "user_key"},
        )

        mock_get_wf.assert_not_called()
        assert result["storage_options"] == {"ak": "user_key"}

    @patch(f"{_MOD}.get_write_fragments_kwargs")
    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_tier2_emits_info_log(self, mock_resolve, mock_get_wf, caplog):
        """Tier 2 should log an info message about the fallback."""
        import logging

        with caplog.at_level(logging.INFO, logger="lance_ray.utils"):
            get_write_fragments_kwargs_with_fallback(
                _IMPL, _PROPS, _TABLE_ID,
                user_storage_options={"ak": "user_key"},
            )
        assert any("did not vend credentials" in msg for msg in caplog.messages)

    @patch(f"{_MOD}.resolve_namespace_storage_options", return_value=None)
    def test_no_namespace_no_user_returns_empty(self, mock_resolve):
        """No namespace, no user creds -> completely empty result."""
        result = get_write_fragments_kwargs_with_fallback(None, None, None)
        assert result == {}
