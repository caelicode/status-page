import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import yaml

from reconcile import (
    load_config_yaml,
    load_existing_statuspage_config,
    generate_checks_json,
    generate_statuspage_json,
    reconcile_grafana,
    reconcile_statuspage,
)
from monitoring.grafana_client import GrafanaClientError
from atlassian_statuspage.client import StatuspageError


@pytest.fixture
def sample_config():
    return {
        "statuspage": {"page_id": "test-page"},
        "settings": {
            "reachability_query_window": "15m",
            "latency_query_window": "5m",
            "thresholds": {
                "reachability": {"operational": 95, "degraded": 75},
                "latency_ms": {"operational": 200, "degraded": 1000},
            },
        },
        "incidents": {
            "auto_create": True,
            "auto_postmortem": True,
            "notify_subscribers": True,
        },
        "endpoints": {
            "api-service": {
                "name": "API Service",
                "url": "https://api.example.com/health",
                "description": "Main API",
                "frequency": 60000,
                "probes": [1, 2, 3],
                "component": True,
                "metric": True,
            },
            "website": {
                "name": "Website",
                "url": "https://example.com",
                "description": "Public website",
                "frequency": 300000,
                "probes": [1, 2],
                "component": True,
                "metric": False,
                "thresholds": {
                    "latency_ms": {"operational": 2000, "degraded": 5000},
                },
            },
        },
    }


@pytest.fixture
def mock_sm_client():
    client = MagicMock()
    client.list_checks.return_value = []
    client.create_check.return_value = "api-service"
    client.update_check.return_value = {}
    client.delete_check.return_value = None
    return client


@pytest.fixture
def mock_sp_client():
    client = MagicMock()
    client.list_components.return_value = []
    client.list_metrics.return_value = []
    client.create_component.return_value = {"id": "new-comp-1"}
    client.create_metric.return_value = {"id": "new-metric-1"}
    client.delete_component.return_value = None
    client.delete_metric.return_value = None
    return client


class TestLoadConfigYaml:

    def test_loads_valid_yaml(self, tmp_path):
        config = {"endpoints": {"test": {"name": "Test", "url": "https://test.com"}}}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config))

        result = load_config_yaml(config_file)
        assert result["endpoints"]["test"]["name"] == "Test"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config_yaml(tmp_path / "nope.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{invalid: yaml: [")
        with pytest.raises(yaml.YAMLError):
            load_config_yaml(bad)


class TestLoadExistingStatuspageConfig:

    def test_loads_existing(self, tmp_path):
        data = {"page_id": "abc", "component_mapping": {}}
        config_file = tmp_path / "statuspage.json"
        config_file.write_text(json.dumps(data))

        result = load_existing_statuspage_config(config_file)
        assert result["page_id"] == "abc"

    def test_missing_returns_none(self, tmp_path):
        result = load_existing_statuspage_config(tmp_path / "nope.json")
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        result = load_existing_statuspage_config(bad)
        assert result is None


class TestGenerateChecksJson:

    def test_generates_from_config(self, sample_config):
        result = generate_checks_json(sample_config)

        assert result["settings"]["reachability_query_window"] == "15m"
        assert result["settings"]["latency_query_window"] == "5m"
        assert result["settings"]["thresholds"]["reachability"]["operational"] == 95

        assert len(result["checks"]) == 2
        names = {c["name"] for c in result["checks"]}
        assert "API Service" in names
        assert "Website" in names

    def test_includes_per_check_thresholds(self, sample_config):
        result = generate_checks_json(sample_config)
        website_check = [c for c in result["checks"] if c["name"] == "Website"][0]
        assert "thresholds" in website_check
        assert website_check["thresholds"]["latency_ms"]["operational"] == 2000

    def test_omits_thresholds_when_not_set(self, sample_config):
        result = generate_checks_json(sample_config)
        api_check = [c for c in result["checks"] if c["name"] == "API Service"][0]
        assert "thresholds" not in api_check

    def test_empty_endpoints(self):
        config = {"settings": {}, "endpoints": {}}
        result = generate_checks_json(config)
        assert result["checks"] == []


class TestGenerateStatuspageJson:

    def test_generates_from_config(self, sample_config):
        mapping = {
            "api-service": {"name": "API Service", "component_id": "c1", "metric_id": "m1"},
        }
        result = generate_statuspage_json(sample_config, mapping)

        assert result["page_id"] == "test-page"
        assert result["component_mapping"]["api-service"]["component_id"] == "c1"
        assert result["incidents"]["auto_create"] is True

    def test_empty_mapping(self, sample_config):
        result = generate_statuspage_json(sample_config, {})
        assert result["component_mapping"] == {}


class TestReconcileGrafana:

    def test_creates_new_checks(self, sample_config, mock_sm_client):
        result = reconcile_grafana(sample_config, mock_sm_client)

        assert len(result["created"]) == 2
        assert "api-service" in result["created"]
        assert "website" in result["created"]
        assert mock_sm_client.create_check.call_count == 2

    def test_skips_existing_check_no_changes(self, sample_config, mock_sm_client):
        mock_sm_client.list_checks.return_value = [
            {
                "id": 100,
                "job": "api-service",
                "target": "https://api.example.com/health",
                "frequency": 60000,
                "probes": [1, 2, 3],
            },
        ]

        result = reconcile_grafana(sample_config, mock_sm_client)

        assert "api-service" not in result["created"]
        assert "api-service" not in result["updated"]
        assert len(result["created"]) == 1
        assert "website" in result["created"]

    def test_updates_check_when_url_changed(self, sample_config, mock_sm_client):
        mock_sm_client.list_checks.return_value = [
            {
                "id": 100,
                "job": "api-service",
                "target": "https://old-url.com/health",
                "frequency": 60000,
                "probes": [1, 2, 3],
            },
        ]

        result = reconcile_grafana(sample_config, mock_sm_client)

        assert "api-service" in result["updated"]
        mock_sm_client.update_check.assert_called_once()
        call_kwargs = mock_sm_client.update_check.call_args[1]
        assert call_kwargs["target_url"] == "https://api.example.com/health"

    def test_updates_check_when_frequency_changed(self, sample_config, mock_sm_client):
        mock_sm_client.list_checks.return_value = [
            {
                "id": 100,
                "job": "api-service",
                "target": "https://api.example.com/health",
                "frequency": 30000,
                "probes": [1, 2, 3],
            },
        ]

        result = reconcile_grafana(sample_config, mock_sm_client)
        assert "api-service" in result["updated"]

    @patch.dict("os.environ", {"ALLOW_DELETIONS": "true"})
    def test_deletes_orphaned_checks(self, sample_config, mock_sm_client):
        mock_sm_client.list_checks.return_value = [
            {"id": 999, "job": "old-service", "target": "https://old.com", "frequency": 60000, "probes": [1]},
        ]

        result = reconcile_grafana(sample_config, mock_sm_client)
        assert "old-service" in result["deleted"]
        mock_sm_client.delete_check.assert_called_once_with(999)

    @patch.dict("os.environ", {"ALLOW_DELETIONS": "false"})
    def test_skips_deletion_when_not_allowed(self, sample_config, mock_sm_client):
        mock_sm_client.list_checks.return_value = [
            {"id": 999, "job": "old-service", "target": "https://old.com", "frequency": 60000, "probes": [1]},
        ]

        result = reconcile_grafana(sample_config, mock_sm_client)
        assert result["deleted"] == []
        mock_sm_client.delete_check.assert_not_called()

    def test_list_failure_returns_errors(self, sample_config, mock_sm_client):
        mock_sm_client.list_checks.side_effect = GrafanaClientError("API down")

        result = reconcile_grafana(sample_config, mock_sm_client)
        assert len(result["errors"]) == 1
        mock_sm_client.create_check.assert_not_called()

    def test_create_failure_recorded(self, sample_config, mock_sm_client):
        mock_sm_client.create_check.side_effect = GrafanaClientError("fail")

        result = reconcile_grafana(sample_config, mock_sm_client)
        assert len(result["errors"]) == 2

    def test_skips_endpoint_without_url(self, sample_config, mock_sm_client):
        sample_config["endpoints"]["no-url"] = {
            "name": "No URL",
            "url": "",
            "component": True,
        }

        result = reconcile_grafana(sample_config, mock_sm_client)
        created_jobs = result["created"]
        assert "no-url" not in created_jobs


class TestReconcileStatuspage:

    def test_creates_new_components_and_metrics(self, sample_config, mock_sp_client):
        mock_sp_client.create_component.side_effect = [
            {"id": "comp-1"},
            {"id": "comp-2"},
        ]
        mock_sp_client.create_metric.return_value = {"id": "met-1"}

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, {})

        assert len(result["created"]) >= 2
        assert mapping["api-service"]["component_id"] == "comp-1"
        assert mapping["api-service"]["metric_id"] == "met-1"
        assert mapping["website"]["component_id"] == "comp-2"
        assert mapping["website"]["metric_id"] == ""

    def test_skips_existing_component(self, sample_config, mock_sp_client):
        existing = {
            "api-service": {
                "name": "API Service",
                "component_id": "existing-comp",
                "metric_id": "existing-met",
            },
        }

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, existing)

        assert mapping["api-service"]["component_id"] == "existing-comp"
        assert mapping["api-service"]["metric_id"] == "existing-met"
        comp_creates = [c for c in result["created"] if "API Service" in c]
        assert len(comp_creates) == 0

    def test_adopts_existing_statuspage_component(self, sample_config, mock_sp_client):
        mock_sp_client.list_components.return_value = [
            {"id": "found-comp", "name": "API Service", "status": "operational"},
        ]

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, {})

        assert mapping["api-service"]["component_id"] == "found-comp"

    @patch.dict("os.environ", {"ALLOW_DELETIONS": "true"})
    def test_deletes_orphaned_components(self, sample_config, mock_sp_client):
        existing = {
            "api-service": {"name": "API Service", "component_id": "c1", "metric_id": ""},
            "website": {"name": "Website", "component_id": "c2", "metric_id": ""},
            "old-service": {"name": "Old Service", "component_id": "c-old", "metric_id": "m-old"},
        }
        mock_sp_client.create_component.return_value = {"id": "c1"}

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, existing)

        assert "old-service" not in mapping
        deleted_names = [d for d in result["deleted"] if "Old Service" in d]
        assert len(deleted_names) >= 1

    @patch.dict("os.environ", {"ALLOW_DELETIONS": "false"})
    def test_skips_deletion_when_not_allowed(self, sample_config, mock_sp_client):
        existing = {
            "old-service": {"name": "Old Service", "component_id": "c-old", "metric_id": ""},
        }

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, existing)

        assert "old-service" in mapping
        assert result["deleted"] == []

    def test_list_components_failure(self, sample_config, mock_sp_client):
        mock_sp_client.list_components.side_effect = StatuspageError("down")

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, {})
        assert len(result["errors"]) == 1

    def test_create_component_failure_skips_endpoint(self, sample_config, mock_sp_client):
        mock_sp_client.create_component.side_effect = StatuspageError("fail")

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, {})
        assert len(result["errors"]) >= 1

    def test_metric_not_created_when_disabled(self, sample_config, mock_sp_client):
        mock_sp_client.create_component.side_effect = [
            {"id": "comp-1"},
            {"id": "comp-2"},
        ]
        mock_sp_client.create_metric.return_value = {"id": "met-1"}

        result, mapping = reconcile_statuspage(sample_config, mock_sp_client, {})

        assert mapping["website"]["metric_id"] == ""
        metric_creates = [c for c in result["created"] if "Website" in c and "metric" in c]
        assert len(metric_creates) == 0
