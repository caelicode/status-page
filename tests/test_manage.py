import json
import pytest
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path

from atlassian_statuspage.client import StatuspageError
from atlassian_statuspage.manage import (
    load_checks_config,
    load_statuspage_config,
    save_statuspage_config,
    cmd_sync_components,
    cmd_sync_metrics,
    cmd_delete_component,
    cmd_delete_metric,
    cmd_list_components,
    cmd_list_metrics,
    cmd_cleanup,
)


# ── Config loading tests ─────────────────────────────────────────────


class TestLoadChecksConfig:

    def test_load_valid_config(self, tmp_path):
        config = {
            "checks": [
                {"name": "API", "job_label": "api", "url": "https://api.example.com"}
            ]
        }
        config_file = tmp_path / "checks.json"
        config_file.write_text(json.dumps(config))

        result = load_checks_config(config_file)
        assert len(result["checks"]) == 1

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_checks_config(tmp_path / "nope.json")


class TestSaveStatuspageConfig:

    def test_save_creates_file(self, tmp_path):
        config = {"page_id": "test123", "component_mapping": {}}
        config_file = tmp_path / "statuspage.json"

        save_statuspage_config(config, config_file)

        loaded = json.loads(config_file.read_text())
        assert loaded["page_id"] == "test123"

    def test_save_overwrites_file(self, tmp_path):
        config_file = tmp_path / "statuspage.json"
        config_file.write_text(json.dumps({"old": True}))

        save_statuspage_config({"new": True}, config_file)
        loaded = json.loads(config_file.read_text())
        assert "new" in loaded
        assert "old" not in loaded


# ── sync-components tests ────────────────────────────────────────────


class TestSyncComponents:

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.load_checks_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_creates_new_component(self, mock_get_client, mock_checks, mock_save):
        mock_client = MagicMock()
        mock_client.list_components.return_value = []
        mock_client.create_component.return_value = {"id": "new-c1"}
        mock_get_client.return_value = (
            mock_client,
            {"page_id": "p1", "component_mapping": {}},
        )
        mock_checks.return_value = {
            "checks": [
                {"name": "API", "job_label": "api", "description": "API endpoint"}
            ]
        }

        args = MagicMock()
        result = cmd_sync_components(args)
        assert result == 0
        mock_client.create_component.assert_called_once_with(
            name="API",
            description="API endpoint",
            status="operational",
        )
        mock_save.assert_called_once()

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.load_checks_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_skips_existing_component_in_config(self, mock_get_client, mock_checks, mock_save):
        mock_client = MagicMock()
        mock_client.list_components.return_value = []
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": ""},
                },
            },
        )
        mock_checks.return_value = {
            "checks": [{"name": "API", "job_label": "api"}]
        }

        args = MagicMock()
        result = cmd_sync_components(args)
        assert result == 0
        mock_client.create_component.assert_not_called()

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.load_checks_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_adopts_existing_statuspage_component(self, mock_get_client, mock_checks, mock_save):
        mock_client = MagicMock()
        mock_client.list_components.return_value = [
            {"id": "existing-c1", "name": "API"}
        ]
        mock_get_client.return_value = (
            mock_client,
            {"page_id": "p1", "component_mapping": {}},
        )
        mock_checks.return_value = {
            "checks": [{"name": "API", "job_label": "api"}]
        }

        args = MagicMock()
        result = cmd_sync_components(args)
        assert result == 0
        mock_client.create_component.assert_not_called()

        saved_config = mock_save.call_args[0][0]
        assert saved_config["component_mapping"]["api"]["component_id"] == "existing-c1"


# ── sync-metrics tests ───────────────────────────────────────────────


class TestSyncMetrics:

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_creates_new_metric(self, mock_get_client, mock_save):
        mock_client = MagicMock()
        mock_client.list_metrics.return_value = []
        mock_client.create_metric.return_value = {"id": "new-m1"}
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": ""},
                },
            },
        )

        args = MagicMock()
        result = cmd_sync_metrics(args)
        assert result == 0
        mock_client.create_metric.assert_called_once_with(
            name="API Latency",
            suffix="ms",
            tooltip="Average response time for API",
        )

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_skips_existing_metric(self, mock_get_client, mock_save):
        mock_client = MagicMock()
        mock_client.list_metrics.return_value = []
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": "m1"},
                },
            },
        )

        args = MagicMock()
        result = cmd_sync_metrics(args)
        assert result == 0
        mock_client.create_metric.assert_not_called()


# ── delete-component tests ───────────────────────────────────────────


class TestDeleteComponent:

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_deletes_component_and_metric(self, mock_get_client, mock_save):
        mock_client = MagicMock()
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": "m1"},
                },
            },
        )

        args = MagicMock()
        args.job_label = "api"
        result = cmd_delete_component(args)
        assert result == 0
        mock_client.delete_metric.assert_called_once_with("m1")
        mock_client.delete_component.assert_called_once_with("c1")

        saved_config = mock_save.call_args[0][0]
        assert "api" not in saved_config["component_mapping"]

    @patch("atlassian_statuspage.manage.get_client")
    def test_delete_nonexistent_returns_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = (
            mock_client,
            {"page_id": "p1", "component_mapping": {}},
        )

        args = MagicMock()
        args.job_label = "nope"
        result = cmd_delete_component(args)
        assert result == 1


# ── delete-metric tests ──────────────────────────────────────────────


class TestDeleteMetric:

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_deletes_metric(self, mock_get_client, mock_save):
        mock_client = MagicMock()
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": "m1"},
                },
            },
        )

        args = MagicMock()
        args.job_label = "api"
        result = cmd_delete_metric(args)
        assert result == 0
        mock_client.delete_metric.assert_called_once_with("m1")

        saved_config = mock_save.call_args[0][0]
        assert saved_config["component_mapping"]["api"]["metric_id"] == ""

    @patch("atlassian_statuspage.manage.get_client")
    def test_no_metric_id_returns_zero(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": ""},
                },
            },
        )

        args = MagicMock()
        args.job_label = "api"
        result = cmd_delete_metric(args)
        assert result == 0
        mock_client.delete_metric.assert_not_called()


# ── list commands tests ──────────────────────────────────────────────


class TestListCommands:

    @patch("atlassian_statuspage.manage.get_client")
    def test_list_components(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.list_components.return_value = [
            {"id": "c1", "name": "API", "status": "operational", "group_id": None}
        ]
        mock_get_client.return_value = (mock_client, {})

        args = MagicMock()
        result = cmd_list_components(args)
        assert result == 0

    @patch("atlassian_statuspage.manage.get_client")
    def test_list_metrics(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.list_metrics.return_value = [
            {"id": "m1", "name": "Latency", "suffix": "ms"}
        ]
        mock_get_client.return_value = (mock_client, {})

        args = MagicMock()
        result = cmd_list_metrics(args)
        assert result == 0

    @patch("atlassian_statuspage.manage.get_client")
    def test_list_empty(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.list_components.return_value = []
        mock_get_client.return_value = (mock_client, {})

        args = MagicMock()
        result = cmd_list_components(args)
        assert result == 0


# ── cleanup tests ────────────────────────────────────────────────────


class TestCleanup:

    @patch("atlassian_statuspage.manage.save_statuspage_config")
    @patch("atlassian_statuspage.manage.get_client")
    def test_cleanup_deletes_all(self, mock_get_client, mock_save):
        mock_client = MagicMock()
        mock_get_client.return_value = (
            mock_client,
            {
                "page_id": "p1",
                "component_mapping": {
                    "api": {"name": "API", "component_id": "c1", "metric_id": "m1"},
                    "web": {"name": "Web", "component_id": "c2", "metric_id": ""},
                },
            },
        )

        args = MagicMock()
        args.yes = True
        result = cmd_cleanup(args)
        assert result == 0
        assert mock_client.delete_component.call_count == 2
        mock_client.delete_metric.assert_called_once_with("m1")

        saved_config = mock_save.call_args[0][0]
        assert saved_config["component_mapping"] == {}

    @patch("atlassian_statuspage.manage.get_client")
    def test_cleanup_empty_mapping(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = (
            mock_client,
            {"page_id": "p1", "component_mapping": {}},
        )

        args = MagicMock()
        args.yes = True
        result = cmd_cleanup(args)
        assert result == 0
        mock_client.delete_component.assert_not_called()
