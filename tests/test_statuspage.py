import json
import time
import pytest
import requests
from unittest.mock import patch, MagicMock, call

from atlassian_statuspage.client import StatuspageClient, StatuspageError
from atlassian_statuspage.sync import load_statuspage_config, load_status_report, STATUS_MAP


@pytest.fixture
def client():
    return StatuspageClient(api_key="test-key", page_id="test-page-id")


class TestStatuspageClientInit:

    def test_headers_set(self, client):
        assert client._session.headers["Authorization"] == "OAuth test-key"
        assert client._session.headers["Content-Type"] == "application/json"

    def test_url_construction(self, client):
        url = client._url("components")
        assert url == "https://api.statuspage.io/v1/pages/test-page-id/components"

    def test_url_with_subpath(self, client):
        url = client._url("components/abc123")
        assert url == "https://api.statuspage.io/v1/pages/test-page-id/components/abc123"


class TestListComponents:

    def test_returns_component_list(self, client):
        with patch.object(client._session, "get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: [{"id": "c1", "name": "API"}],
            )
            mock_get.return_value.raise_for_status = MagicMock()
            result = client.list_components()
            assert len(result) == 1
            assert result[0]["name"] == "API"

    def test_failure_raises(self, client):
        with patch.object(client._session, "get") as mock_get:
            mock_get.side_effect = requests.Timeout("timeout")
            with pytest.raises(StatuspageError, match="Failed to list components"):
                client.list_components()


class TestCreateComponent:

    def test_create_with_defaults(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=201)
            mock_post.return_value.json.return_value = {
                "id": "new1", "name": "My Service", "status": "operational"
            }
            mock_post.return_value.raise_for_status = MagicMock()

            result = client.create_component("My Service", description="A service")
            assert result["id"] == "new1"

            payload = mock_post.call_args[1]["json"]["component"]
            assert payload["name"] == "My Service"
            assert payload["description"] == "A service"
            assert payload["status"] == "operational"
            assert payload["showcase"] is True
            assert payload["only_show_if_degraded"] is False
            assert "group_id" not in payload

    def test_create_with_group_id(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=201)
            mock_post.return_value.json.return_value = {"id": "new2"}
            mock_post.return_value.raise_for_status = MagicMock()

            client.create_component("Grouped", group_id="grp1")
            payload = mock_post.call_args[1]["json"]["component"]
            assert payload["group_id"] == "grp1"

    def test_create_invalid_status_raises(self, client):
        with pytest.raises(StatuspageError, match="Invalid status"):
            client.create_component("Bad", status="invalid")

    def test_create_failure_raises(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to create component"):
                client.create_component("Fail")


class TestUpdateComponentStatus:

    def test_valid_status_values(self, client):
        valid = [
            "operational",
            "degraded_performance",
            "partial_outage",
            "major_outage",
            "under_maintenance",
        ]
        for status in valid:
            with patch.object(client._session, "patch") as mock_patch:
                mock_patch.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {"id": "comp1", "status": status},
                )
                mock_patch.return_value.raise_for_status = MagicMock()
                result = client.update_component_status("comp1", status)
                assert result["status"] == status

    def test_invalid_status_raises(self, client):
        with pytest.raises(StatuspageError, match="Invalid status"):
            client.update_component_status("comp1", "invalid")

    def test_request_failure_raises(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.side_effect = requests.ConnectionError("Connection error")
            with pytest.raises(StatuspageError):
                client.update_component_status("comp1", "operational")

    def test_patch_called_with_correct_payload(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.return_value = MagicMock(status_code=200)
            mock_patch.return_value.json.return_value = {}
            mock_patch.return_value.raise_for_status = MagicMock()

            client.update_component_status("comp1", "major_outage")

            mock_patch.assert_called_once_with(
                "https://api.statuspage.io/v1/pages/test-page-id/components/comp1",
                json={"component": {"status": "major_outage"}},
                timeout=30,
            )


class TestDeleteComponent:

    def test_delete_success(self, client):
        with patch.object(client._session, "delete") as mock_delete:
            mock_delete.return_value = MagicMock(status_code=204)
            mock_delete.return_value.raise_for_status = MagicMock()
            client.delete_component("comp1")
            mock_delete.assert_called_once()

    def test_delete_failure_raises(self, client):
        with patch.object(client._session, "delete") as mock_delete:
            mock_delete.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to delete component"):
                client.delete_component("comp1")


class TestCreateMetric:

    def test_create_metric_payload(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=201)
            mock_post.return_value.json.return_value = {"id": "m1", "name": "Latency"}
            mock_post.return_value.raise_for_status = MagicMock()

            result = client.create_metric("Latency", suffix="ms", tooltip="Response time")

            assert result["name"] == "Latency"
            call_args = mock_post.call_args
            payload = call_args[1]["json"]["metric"]
            assert payload["name"] == "Latency"
            assert payload["suffix"] == "ms"
            assert payload["display"] is True
            assert payload["tooltip_description"] == "Response time"


class TestDeleteMetric:

    def test_delete_success(self, client):
        with patch.object(client._session, "delete") as mock_delete:
            mock_delete.return_value = MagicMock(status_code=204)
            mock_delete.return_value.raise_for_status = MagicMock()
            client.delete_metric("m1")
            mock_delete.assert_called_once()

    def test_delete_failure_raises(self, client):
        with patch.object(client._session, "delete") as mock_delete:
            mock_delete.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to delete metric"):
                client.delete_metric("m1")


class TestSubmitMetricData:

    def test_submit_with_explicit_timestamp(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.json.return_value = {"timestamp": 1000}
            mock_post.return_value.raise_for_status = MagicMock()

            client.submit_metric_data("metric1", 150.5, timestamp=1000)

            mock_post.assert_called_once_with(
                "https://api.statuspage.io/v1/pages/test-page-id/metrics/metric1/data.json",
                json={"data": {"timestamp": 1000, "value": 150.5}},
                timeout=30,
            )

    def test_submit_uses_current_time_when_no_timestamp(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.return_value.json.return_value = {}
            mock_post.return_value.raise_for_status = MagicMock()

            before = int(time.time())
            client.submit_metric_data("metric1", 200.0)
            after = int(time.time())

            call_args = mock_post.call_args
            submitted_ts = call_args[1]["json"]["data"]["timestamp"]
            assert before <= submitted_ts <= after

    def test_failure_raises(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("error")
            with pytest.raises(StatuspageError, match="Failed to submit data"):
                client.submit_metric_data("metric1", 100.0)


class TestListUnresolvedIncidents:

    def test_returns_list(self, client):
        with patch.object(client._session, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            mock_get.return_value.json.return_value = [
                {"id": "inc1", "status": "investigating"}
            ]
            mock_get.return_value.raise_for_status = MagicMock()

            result = client.list_unresolved_incidents()
            assert len(result) == 1
            assert result[0]["id"] == "inc1"
            mock_get.assert_called_once_with(
                "https://api.statuspage.io/v1/pages/test-page-id/incidents/unresolved",
                timeout=30,
            )

    def test_failure_raises(self, client):
        with patch.object(client._session, "get") as mock_get:
            mock_get.side_effect = requests.Timeout("timeout")
            with pytest.raises(StatuspageError, match="Failed to list unresolved"):
                client.list_unresolved_incidents()


class TestCreateIncident:

    def test_create_basic_incident(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=201)
            mock_post.return_value.json.return_value = {
                "id": "inc1", "name": "Test Incident", "status": "investigating"
            }
            mock_post.return_value.raise_for_status = MagicMock()

            result = client.create_incident(
                name="Test Incident",
                body="Something broke",
            )
            assert result["id"] == "inc1"

            payload = mock_post.call_args[1]["json"]["incident"]
            assert payload["name"] == "Test Incident"
            assert payload["status"] == "investigating"
            assert payload["body"] == "Something broke"
            assert payload["deliver_notifications"] is True

    def test_create_with_components(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=201)
            mock_post.return_value.json.return_value = {"id": "inc2"}
            mock_post.return_value.raise_for_status = MagicMock()

            client.create_incident(
                name="Outage",
                component_ids=["c1"],
                components={"c1": "major_outage"},
                impact_override="critical",
            )

            payload = mock_post.call_args[1]["json"]["incident"]
            assert payload["component_ids"] == ["c1"]
            assert payload["components"] == {"c1": "major_outage"}
            assert payload["impact_override"] == "critical"

    def test_invalid_status_raises(self, client):
        with pytest.raises(StatuspageError, match="Invalid incident status"):
            client.create_incident("Bad", status="broken")

    def test_invalid_impact_raises(self, client):
        with pytest.raises(StatuspageError, match="Invalid impact"):
            client.create_incident("Bad", impact_override="extreme")

    def test_failure_raises(self, client):
        with patch.object(client._session, "post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to create incident"):
                client.create_incident("Fail")


class TestUpdateIncident:

    def test_update_status_and_body(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.return_value = MagicMock(status_code=200)
            mock_patch.return_value.json.return_value = {
                "id": "inc1", "status": "identified"
            }
            mock_patch.return_value.raise_for_status = MagicMock()

            result = client.update_incident(
                "inc1", status="identified", body="Found the issue"
            )
            assert result["status"] == "identified"

            payload = mock_patch.call_args[1]["json"]["incident"]
            assert payload["status"] == "identified"
            assert payload["body"] == "Found the issue"

    def test_update_invalid_status_raises(self, client):
        with pytest.raises(StatuspageError, match="Invalid incident status"):
            client.update_incident("inc1", status="bad")

    def test_update_with_components(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.return_value = MagicMock(status_code=200)
            mock_patch.return_value.json.return_value = {"id": "inc1"}
            mock_patch.return_value.raise_for_status = MagicMock()

            client.update_incident(
                "inc1",
                components={"c1": "operational"},
            )
            payload = mock_patch.call_args[1]["json"]["incident"]
            assert payload["components"] == {"c1": "operational"}

    def test_update_with_impact_override(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.return_value = MagicMock(status_code=200)
            mock_patch.return_value.json.return_value = {"id": "inc1"}
            mock_patch.return_value.raise_for_status = MagicMock()

            client.update_incident(
                "inc1",
                impact_override="critical",
                name="API experiencing a major outage",
            )
            payload = mock_patch.call_args[1]["json"]["incident"]
            assert payload["impact_override"] == "critical"
            assert payload["name"] == "API experiencing a major outage"

    def test_update_invalid_impact_raises(self, client):
        with pytest.raises(StatuspageError, match="Invalid impact"):
            client.update_incident("inc1", impact_override="extreme")

    def test_update_omits_impact_when_none(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.return_value = MagicMock(status_code=200)
            mock_patch.return_value.json.return_value = {"id": "inc1"}
            mock_patch.return_value.raise_for_status = MagicMock()

            client.update_incident("inc1", body="no impact change")
            payload = mock_patch.call_args[1]["json"]["incident"]
            assert "impact_override" not in payload
            assert "name" not in payload

    def test_failure_raises(self, client):
        with patch.object(client._session, "patch") as mock_patch:
            mock_patch.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to update incident"):
                client.update_incident("inc1", body="update")


class TestResolveIncident:

    def test_resolve_calls_update(self, client):
        with patch.object(client, "update_incident") as mock_update:
            mock_update.return_value = {"id": "inc1", "status": "resolved"}

            result = client.resolve_incident(
                "inc1",
                body="Fixed it.",
                components={"c1": "operational"},
            )
            assert result["status"] == "resolved"

            mock_update.assert_called_once_with(
                incident_id="inc1",
                status="resolved",
                body="Fixed it.",
                components={"c1": "operational"},
                deliver_notifications=True,
            )


class TestDeleteIncident:

    def test_delete_success(self, client):
        with patch.object(client._session, "delete") as mock_delete:
            mock_delete.return_value = MagicMock(status_code=204)
            mock_delete.return_value.raise_for_status = MagicMock()
            client.delete_incident("inc1")
            mock_delete.assert_called_once()

    def test_delete_failure_raises(self, client):
        with patch.object(client._session, "delete") as mock_delete:
            mock_delete.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to delete incident"):
                client.delete_incident("inc1")


class TestCreatePostmortem:

    def test_create_postmortem_payload(self, client):
        with patch.object(client._session, "put") as mock_put:
            mock_put.return_value = MagicMock(status_code=200)
            mock_put.return_value.json.return_value = {
                "postmortem_body": "# Report"
            }
            mock_put.return_value.raise_for_status = MagicMock()

            result = client.create_postmortem(
                "inc1",
                body="# Postmortem Report",
                notify_subscribers=True,
            )

            mock_put.assert_called_once_with(
                "https://api.statuspage.io/v1/pages/test-page-id/incidents/inc1/postmortem",
                json={
                    "postmortem": {
                        "body": "# Postmortem Report",
                        "notify_subscribers": True,
                        "notify_twitter": False,
                    }
                },
                timeout=30,
            )

    def test_failure_raises(self, client):
        with patch.object(client._session, "put") as mock_put:
            mock_put.side_effect = requests.ConnectionError("fail")
            with pytest.raises(StatuspageError, match="Failed to create postmortem"):
                client.create_postmortem("inc1", body="report")


class TestStatusMap:

    def test_all_internal_statuses_mapped(self):
        assert STATUS_MAP["operational"] == "operational"
        assert STATUS_MAP["degraded_performance"] == "degraded_performance"
        assert STATUS_MAP["major_outage"] == "major_outage"

    def test_unknown_status_defaults_to_outage(self):
        assert STATUS_MAP.get("unknown", "major_outage") == "major_outage"


class TestLoadStatuspageConfig:

    def test_load_valid_config(self, tmp_path):
        config = {
            "page_id": "abc123",
            "component_mapping": {
                "api": {"name": "API", "component_id": "c1", "metric_id": "m1"}
            },
        }
        config_file = tmp_path / "statuspage.json"
        config_file.write_text(json.dumps(config))

        result = load_statuspage_config(config_file)
        assert result["page_id"] == "abc123"
        assert "api" in result["component_mapping"]

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_statuspage_config(tmp_path / "nope.json")

    def test_load_invalid_json_raises(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{")
        with pytest.raises(json.JSONDecodeError):
            load_statuspage_config(bad_file)


class TestLoadStatusReport:

    def test_load_valid_report(self, tmp_path):
        report = {
            "overall_status": "operational",
            "components": [
                {"name": "API", "status": "operational", "latency_ms": 150.0}
            ],
        }
        report_file = tmp_path / "status.json"
        report_file.write_text(json.dumps(report))

        result = load_status_report(report_file)
        assert result["overall_status"] == "operational"
        assert len(result["components"]) == 1

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_status_report(tmp_path / "nope.json")
