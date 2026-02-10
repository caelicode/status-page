import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from atlassian_statuspage.client import StatuspageError
from atlassian_statuspage.incident_manager import (
    find_open_incident_for_component,
    generate_incident_name,
    generate_incident_body,
    generate_resolve_body,
    generate_postmortem,
    process_incidents,
)


class TestFindOpenIncident:

    def test_finds_matching_incident(self):
        incidents = [
            {
                "id": "inc1",
                "components": [{"id": "c1"}, {"id": "c2"}],
            },
            {
                "id": "inc2",
                "components": [{"id": "c3"}],
            },
        ]
        result = find_open_incident_for_component(incidents, "c2")
        assert result["id"] == "inc1"

    def test_returns_none_when_not_found(self):
        incidents = [
            {"id": "inc1", "components": [{"id": "c1"}]},
        ]
        result = find_open_incident_for_component(incidents, "c99")
        assert result is None

    def test_empty_incidents_list(self):
        result = find_open_incident_for_component([], "c1")
        assert result is None

    def test_incident_with_no_components(self):
        incidents = [{"id": "inc1", "components": []}]
        result = find_open_incident_for_component(incidents, "c1")
        assert result is None


class TestGenerateIncidentName:

    def test_degraded_name(self):
        name = generate_incident_name("API Gateway", "degraded_performance")
        assert "API Gateway" in name
        assert "degraded performance" in name

    def test_outage_name(self):
        name = generate_incident_name("caelicode.com", "major_outage")
        assert "caelicode.com" in name
        assert "major outage" in name

    def test_unknown_status_name(self):
        name = generate_incident_name("Service", "partial_outage")
        assert "Service" in name
        assert "issues" in name


class TestGenerateIncidentBody:

    def test_includes_component_name(self):
        body = generate_incident_body("API", "degraded_performance", 95.0, 350.0)
        assert "API" in body

    def test_includes_metrics(self):
        body = generate_incident_body("API", "degraded_performance", 85.5, 1200.0)
        assert "85.5%" in body
        assert "1200ms" in body

    def test_degraded_message(self):
        body = generate_incident_body("API", "degraded_performance", None, None)
        assert "degraded performance" in body

    def test_outage_message(self):
        body = generate_incident_body("API", "major_outage", None, None)
        assert "major outage" in body

    def test_none_metrics_omitted(self):
        body = generate_incident_body("API", "major_outage", None, None)
        assert "Reachability" not in body
        assert "Latency" not in body


class TestGenerateResolveBody:

    def test_includes_component_name(self):
        body = generate_resolve_body("caelicode.com")
        assert "caelicode.com" in body
        assert "resolved" in body
        assert "operational" in body


class TestGeneratePostmortem:

    def test_basic_postmortem(self):
        incident = {
            "name": "API outage",
            "created_at": "2026-02-09T10:00:00Z",
            "impact": "critical",
            "incident_updates": [
                {
                    "status": "investigating",
                    "body": "Looking into it",
                    "created_at": "2026-02-09T10:00:00Z",
                },
                {
                    "status": "resolved",
                    "body": "Fixed",
                    "created_at": "2026-02-09T11:00:00Z",
                },
            ],
        }
        body = generate_postmortem(incident, "API Gateway", "2026-02-09 11:00 UTC")
        assert "API outage" in body
        assert "API Gateway" in body
        assert "critical" in body
        assert "2026-02-09 10:00 UTC" in body
        assert "2026-02-09 11:00 UTC" in body
        assert "Looking into it" in body
        assert "Fixed" in body

    def test_postmortem_without_updates(self):
        incident = {
            "name": "Brief outage",
            "created_at": "2026-02-09T10:00:00Z",
            "impact": "minor",
            "incident_updates": [],
        }
        body = generate_postmortem(incident, "Service", "2026-02-09 10:05 UTC")
        assert "No detailed updates recorded" in body

    def test_postmortem_with_malformed_timestamp(self):
        incident = {
            "name": "Outage",
            "created_at": "not-a-date",
            "impact": "major",
            "incident_updates": [],
        }
        body = generate_postmortem(incident, "Service", "now")
        assert "not-a-date" in body


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.list_unresolved_incidents.return_value = []
    client.list_incidents.return_value = []
    client.create_incident.return_value = {"id": "new-inc-1"}
    client.update_incident.return_value = {"id": "inc1", "status": "identified"}
    client.resolve_incident.return_value = {"id": "inc1", "status": "resolved"}
    client.create_postmortem.return_value = {}
    return client


@pytest.fixture
def component_mapping():
    return {
        "example-api": {
            "name": "Example API",
            "component_id": "c1",
            "metric_id": "",
        },
        "website": {
            "name": "Website",
            "component_id": "c2",
            "metric_id": "",
        },
    }


def make_report(api_status="operational", web_status="operational",
                api_reach=100.0, web_reach=100.0,
                api_latency=100.0, web_latency=200.0):
    return {
        "overall_status": api_status if api_status != "operational" else web_status,
        "components": [
            {
                "name": "Example API",
                "status": api_status,
                "reachability": api_reach,
                "latency_ms": api_latency,
            },
            {
                "name": "Website",
                "status": web_status,
                "reachability": web_reach,
                "latency_ms": web_latency,
            },
        ],
    }


class TestProcessIncidents:

    def test_all_operational_no_action(self, mock_client, component_mapping):
        report = make_report()
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert result["created"] == []
        assert result["updated"] == []
        assert result["resolved"] == []
        assert result["errors"] == []
        mock_client.create_incident.assert_not_called()
        mock_client.update_incident.assert_not_called()
        mock_client.resolve_incident.assert_not_called()

    def test_degraded_creates_incident(self, mock_client, component_mapping):
        report = make_report(api_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["created"]) == 1
        assert result["created"][0]["component"] == "Example API"
        assert result["created"][0]["incident_id"] == "new-inc-1"
        mock_client.create_incident.assert_called_once()

        call_kwargs = mock_client.create_incident.call_args[1]
        assert call_kwargs["component_ids"] == ["c1"]
        assert call_kwargs["components"] == {"c1": "degraded_performance"}
        assert call_kwargs["status"] == "investigating"

    def test_outage_creates_incident_with_critical_impact(self, mock_client, component_mapping):
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["created"]) == 1
        call_kwargs = mock_client.create_incident.call_args[1]
        assert call_kwargs["impact_override"] == "critical"

    def test_existing_incident_gets_updated(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "existing-inc",
                "status": "investigating",
                "components": [{"id": "c1"}],
            }
        ]
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["updated"]) == 1
        assert result["updated"][0]["incident_id"] == "existing-inc"
        mock_client.create_incident.assert_not_called()
        mock_client.update_incident.assert_called_once()

    def test_recovery_resolves_incident_and_creates_postmortem(
        self, mock_client, component_mapping
    ):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "open-inc",
                "status": "investigating",
                "components": [{"id": "c1"}],
            }
        ]
        mock_client.list_incidents.return_value = [
            {
                "id": "open-inc",
                "name": "Example API outage",
                "status": "resolved",
                "impact": "critical",
                "created_at": "2026-02-09T10:00:00Z",
                "incident_updates": [
                    {
                        "status": "investigating",
                        "body": "Looking into it",
                        "created_at": "2026-02-09T10:00:00Z",
                    }
                ],
            }
        ]

        report = make_report(api_status="operational")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["resolved"]) == 1
        assert result["resolved"][0]["incident_id"] == "open-inc"

        mock_client.resolve_incident.assert_called_once()
        resolve_kwargs = mock_client.resolve_incident.call_args[1]
        assert resolve_kwargs["components"] == {"c1": "operational"}

        mock_client.create_postmortem.assert_called_once()
        postmortem_kwargs = mock_client.create_postmortem.call_args[1]
        assert postmortem_kwargs["incident_id"] == "open-inc"
        assert "Example API" in postmortem_kwargs["body"]

    def test_disabled_incidents_skips_all(self, mock_client, component_mapping):
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report,
            auto_incidents=False,
        )
        assert result == {"created": [], "updated": [], "resolved": [], "errors": []}
        mock_client.list_unresolved_incidents.assert_not_called()

    def test_disabled_postmortem_skips_postmortem(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {"id": "inc1", "status": "investigating", "components": [{"id": "c1"}]}
        ]
        report = make_report(api_status="operational")
        result = process_incidents(
            mock_client, component_mapping, report,
            auto_postmortem=False,
        )
        assert len(result["resolved"]) == 1
        mock_client.create_postmortem.assert_not_called()

    def test_multiple_components_independent(self, mock_client, component_mapping):
        report = make_report(api_status="major_outage", web_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["created"]) == 2
        components_created = {c["component"] for c in result["created"]}
        assert "Example API" in components_created
        assert "Website" in components_created

    def test_unresolved_fetch_failure(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.side_effect = StatuspageError("API down")
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["errors"]) == 1
        assert "unresolved" in result["errors"][0].lower()

    def test_create_incident_failure(self, mock_client, component_mapping):
        mock_client.create_incident.side_effect = StatuspageError("API error")
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["errors"]) >= 1

    def test_resolve_failure_skips_postmortem(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {"id": "inc1", "status": "investigating", "components": [{"id": "c1"}]}
        ]
        mock_client.resolve_incident.side_effect = StatuspageError("API error")
        report = make_report(api_status="operational")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert result["resolved"] == []
        assert len(result["errors"]) >= 1
        mock_client.create_postmortem.assert_not_called()

    def test_postmortem_failure_non_fatal(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {"id": "inc1", "status": "investigating", "components": [{"id": "c1"}]}
        ]
        mock_client.create_postmortem.side_effect = StatuspageError("Postmortem API error")
        report = make_report(api_status="operational")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["resolved"]) == 1
        assert any("postmortem" in e.lower() for e in result["errors"])

    def test_missing_component_data_skipped(self, mock_client, component_mapping):
        report = {
            "overall_status": "operational",
            "components": [
                {"name": "Example API", "status": "operational", "reachability": 100.0, "latency_ms": 100.0},
            ],
        }
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert result["created"] == []

    def test_no_component_id_skipped(self, mock_client):
        mapping = {
            "test": {"name": "Test", "component_id": "", "metric_id": ""},
        }
        report = {
            "overall_status": "major_outage",
            "components": [{"name": "Test", "status": "major_outage", "reachability": 0.0, "latency_ms": None}],
        }
        result = process_incidents(mock_client, mapping, report)
        assert result["created"] == []
        mock_client.create_incident.assert_not_called()

    def test_notify_subscribers_false(self, mock_client, component_mapping):
        report = make_report(api_status="major_outage")
        process_incidents(
            mock_client, component_mapping, report,
            notify_subscribers=False,
        )
        call_kwargs = mock_client.create_incident.call_args[1]
        assert call_kwargs["deliver_notifications"] is False
