"""Tests for CatCClient HTTP interactions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from radkit_catc_sync.catc_client import CatCClient


class TestCatCClient:
    def test_authenticate_success(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com",
            username="admin",
            password="secret",  # noqa: S106
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"Token": "abc123"}
        with patch.object(client.session, "post", return_value=mock_resp):
            client.authenticate()
        assert client._token == "abc123"

    def test_authenticate_failure_raises(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com",
            username="admin",
            password="secret",  # noqa: S106
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with (
            patch.object(client.session, "post", return_value=mock_resp),
            pytest.raises(RuntimeError, match="auth failed"),
        ):
            client.authenticate()

    def test_authenticate_no_token_raises(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com",
            username="admin",
            password="secret",  # noqa: S106
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        with (
            patch.object(client.session, "post", return_value=mock_resp),
            pytest.raises(RuntimeError, match="no Token"),
        ):
            client.authenticate()

    def test_get_devices_single_page(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com",
            username="admin",
            password="secret",  # noqa: S106
        )
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "response": [
                {"hostname": "r1.example.com", "managementIpAddress": "10.0.0.1"},
            ]
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {"response": []}
        with patch.object(client.session, "get", side_effect=[page1, page2]):
            devices = client.get_devices()
        assert len(devices) == 1
        assert devices[0].hostname == "r1.example.com"

    def test_get_devices_http_error_raises(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com",
            username="admin",
            password="secret",  # noqa: S106
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with (
            patch.object(client.session, "get", return_value=mock_resp),
            pytest.raises(RuntimeError, match="Inventory fetch failed"),
        ):
            client.get_devices()

    def test_hostname_property(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com:443",
            username="admin",
            password="secret",  # noqa: S106
        )
        assert client.hostname == "catc.example.com"

    def test_verify_tls_false(self) -> None:
        client = CatCClient(
            base_url="https://catc.example.com",
            username="admin",
            password="secret",  # noqa: S106
            verify_tls=False,
        )
        assert client.session.verify is False
