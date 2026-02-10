import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from monitoring.config import GrafanaConfig
from monitoring.grafana_client import SyntheticMonitoringClient, GrafanaClientError


@pytest.fixture
def grafana_config():
    return GrafanaConfig(
        prometheus_url="https://prom.example.com/api/v1/query",
        prometheus_user_id="12345",
        api_key="fake-api-key",
        synthetic_monitoring_url="https://sm-api.example.com",
        synthetic_monitoring_token="fake-sm-token",
        stack_id=100,
        metrics_instance_id=200,
        logs_instance_id=300,
    )


class TestSMRegister:

    def test_uses_existing_token_when_list_succeeds(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 1, "tenantId": 42, "job": "test-check"},
        ]

        with patch.object(client._session, "get", return_value=mock_response) as mock_get:
            token, tenant = client.register()

        assert token == "fake-sm-token"
        assert tenant == 42
        assert client._access_token == "fake-sm-token"
        assert client._tenant_id == 42
        mock_get.assert_called_once()

    def test_uses_existing_token_empty_checks(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []

        with patch.object(client._session, "get", return_value=mock_response):
            token, tenant = client.register()

        assert token == "fake-sm-token"
        assert tenant == 0

    def test_falls_back_to_install_when_list_fails(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)

        probe_response = MagicMock()
        probe_response.status_code = 401

        install_response = MagicMock()
        install_response.status_code = 200
        install_response.raise_for_status = MagicMock()
        install_response.json.return_value = {
            "accessToken": "new-access-token",
            "tenantInfo": {"id": 99},
        }

        with patch.object(client._session, "get", return_value=probe_response), \
             patch.object(client._session, "post", return_value=install_response):
            token, tenant = client.register()

        assert token == "new-access-token"
        assert tenant == 99

    def test_falls_back_to_install_when_list_errors(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)

        import requests as req
        install_response = MagicMock()
        install_response.status_code = 200
        install_response.raise_for_status = MagicMock()
        install_response.json.return_value = {
            "accessToken": "new-token",
            "tenantInfo": {"id": 50},
        }

        with patch.object(client._session, "get", side_effect=req.ConnectionError("down")), \
             patch.object(client._session, "post", return_value=install_response):
            token, tenant = client.register()

        assert token == "new-token"
        assert tenant == 50

    def test_raises_when_both_paths_fail(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)

        probe_response = MagicMock()
        probe_response.status_code = 401

        import requests as req
        install_response = MagicMock()
        install_response.status_code = 400
        install_response.raise_for_status.side_effect = req.HTTPError("400 Bad Request")

        with patch.object(client._session, "get", return_value=probe_response), \
             patch.object(client._session, "post", return_value=install_response):
            with pytest.raises(GrafanaClientError, match="Registration failed"):
                client.register()

    def test_install_missing_access_token_raises(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)

        probe_response = MagicMock()
        probe_response.status_code = 401

        install_response = MagicMock()
        install_response.status_code = 200
        install_response.raise_for_status = MagicMock()
        install_response.json.return_value = {"tenantInfo": {"id": 1}}

        with patch.object(client._session, "get", return_value=probe_response), \
             patch.object(client._session, "post", return_value=install_response):
            with pytest.raises(GrafanaClientError, match="No access token"):
                client.register()


class TestSMEnsureRegistered:

    def test_raises_before_register(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)
        with pytest.raises(GrafanaClientError, match="Must call register"):
            client._ensure_registered()

    def test_passes_after_register(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)
        client._access_token = "token"
        client._tenant_id = 0
        client._ensure_registered()

    def test_passes_with_zero_tenant_id(self, grafana_config):
        client = SyntheticMonitoringClient(grafana_config)
        client._access_token = "token"
        client._tenant_id = 0
        client._ensure_registered()
