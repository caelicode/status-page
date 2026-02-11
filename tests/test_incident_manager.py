import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from atlassian_statuspage.client import StatuspageError
from atlassian_statuspage.incident_manager import (
    find_open_incident_for_component,
    generate_incident_name,
    generate_incident_body,
    generate_update_body,
    generate_heartbeat_body,
    generate_resolve_body,
    generate_postmortem,
    get_last_update_time,
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
        body = generate_incident_body("API", "degraded_performance")
        assert "API" in body

    def test_degraded_message(self):
        body = generate_incident_body("API", "degraded_performance")
        assert "degraded performance" in body
        assert "investigating" in body.lower() or "updates" in body.lower()

    def test_outage_message(self):
        body = generate_incident_body("API", "major_outage")
        assert "major outage" in body
        assert "restore" in body.lower()

    def test_no_raw_metrics(self):
        body = generate_incident_body("API", "major_outage")
        assert "Reachability" not in body
        assert "Latency" not in body
        assert "%" not in body
        assert "ms" not in body

    def test_no_embedded_timestamp(self):
        body = generate_incident_body("API", "degraded_performance")
        assert "UTC" not in body

    def test_unknown_status_fallback(self):
        body = generate_incident_body("API", "partial_outage")
        assert "API" in body
        assert "investigating" in body.lower()


class TestGenerateUpdateBody:

    def test_degraded_update(self):
        body = generate_update_body("API", "degraded_performance")
        assert "API" in body
        assert "identified" in body.lower() or "working" in body.lower()

    def test_outage_update(self):
        body = generate_update_body("API", "major_outage")
        assert "API" in body
        assert "unavailable" in body.lower() or "restoring" in body.lower()

    def test_escalation_body(self):
        body = generate_update_body("API", "major_outage", escalated=True)
        assert "API" in body
        assert "escalated" in body.lower()
        assert "urgently" in body.lower()

    def test_no_escalation_no_escalation_language(self):
        body = generate_update_body("API", "major_outage", escalated=False)
        assert "escalated" not in body.lower()

    def test_no_raw_metrics(self):
        body = generate_update_body("API", "degraded_performance")
        assert "Reachability" not in body
        assert "Latency" not in body

    def test_no_embedded_timestamp(self):
        body = generate_update_body("API", "major_outage")
        assert "UTC" not in body

    def test_differs_from_creation_body(self):
        create_body = generate_incident_body("API", "degraded_performance")
        update_body = generate_update_body("API", "degraded_performance")
        assert create_body != update_body


class TestGenerateResolveBody:

    def test_includes_component_name(self):
        body = generate_resolve_body("caelicode.com")
        assert "caelicode.com" in body
        assert "resolved" in body

    def test_no_embedded_timestamp(self):
        body = generate_resolve_body("API")
        assert "UTC" not in body

    def test_professional_tone(self):
        body = generate_resolve_body("API")
        assert "patience" in body.lower() or "normal" in body.lower()


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
                "impact": "critical",
                "components": [{"id": "c1"}],
            }
        ]
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=0,
        )
        assert len(result["updated"]) == 1
        assert result["updated"][0]["incident_id"] == "existing-inc"
        mock_client.create_incident.assert_not_called()
        mock_client.update_incident.assert_called_once()

        call_kwargs = mock_client.update_incident.call_args[1]
        assert call_kwargs["impact_override"] == "critical"
        assert "major outage" in call_kwargs["name"]

    def test_escalation_degraded_to_outage(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "degraded-inc",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
            }
        ]
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report
        )
        assert len(result["updated"]) == 1
        assert result["updated"][0]["escalated"] is True

        call_kwargs = mock_client.update_incident.call_args[1]
        assert call_kwargs["impact_override"] == "critical"
        assert "major outage" in call_kwargs["name"]
        assert call_kwargs["components"] == {"c1": "major_outage"}
        assert call_kwargs["deliver_notifications"] is True

    def test_no_escalation_same_impact(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "existing-inc",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
            }
        ]
        report = make_report(api_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=0,
        )
        assert len(result["updated"]) == 1
        assert result["updated"][0]["escalated"] is False

        call_kwargs = mock_client.update_incident.call_args[1]
        assert call_kwargs["impact_override"] == "minor"
        assert call_kwargs["deliver_notifications"] is False

    def test_escalation_notifies_subscribers(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "degraded-inc",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
            }
        ]
        report = make_report(api_status="major_outage")
        process_incidents(
            mock_client, component_mapping, report,
            notify_subscribers=True,
        )
        call_kwargs = mock_client.update_incident.call_args[1]
        assert call_kwargs["deliver_notifications"] is True

    def test_escalation_respects_notify_disabled(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "degraded-inc",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
            }
        ]
        report = make_report(api_status="major_outage")
        process_incidents(
            mock_client, component_mapping, report,
            notify_subscribers=False,
        )
        call_kwargs = mock_client.update_incident.call_args[1]
        assert call_kwargs["deliver_notifications"] is False

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
        assert result == {"created": [], "updated": [], "resolved": [], "suppressed": [], "errors": []}
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

    def test_suppresses_duplicate_update_within_quiet_period(self, mock_client, component_mapping):
        recent = datetime.now(timezone.utc).isoformat()
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "inc1",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
                "incident_updates": [
                    {"created_at": recent, "body": "Some update"},
                ],
            }
        ]
        report = make_report(api_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=60,
        )
        assert len(result["suppressed"]) == 1
        assert result["suppressed"][0]["component"] == "Example API"
        assert result["updated"] == []
        mock_client.update_incident.assert_not_called()

    def test_posts_heartbeat_after_quiet_period_elapses(self, mock_client, component_mapping):
        old_time = "2020-01-01T00:00:00Z"
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "inc1",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
                "incident_updates": [
                    {"created_at": old_time, "body": "Old update"},
                ],
            }
        ]
        report = make_report(api_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=60,
        )
        assert len(result["updated"]) == 1
        assert result["suppressed"] == []
        mock_client.update_incident.assert_called_once()
        call_kwargs = mock_client.update_incident.call_args[1]
        assert "continue to monitor" in call_kwargs["body"].lower()

    def test_escalation_always_updates_regardless_of_quiet_period(self, mock_client, component_mapping):
        recent = datetime.now(timezone.utc).isoformat()
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "inc1",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
                "incident_updates": [
                    {"created_at": recent, "body": "Recent update"},
                ],
            }
        ]
        report = make_report(api_status="major_outage")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=60,
        )
        assert len(result["updated"]) == 1
        assert result["updated"][0]["escalated"] is True
        assert result["suppressed"] == []
        mock_client.update_incident.assert_called_once()
        call_kwargs = mock_client.update_incident.call_args[1]
        assert "escalated" in call_kwargs["body"].lower()

    def test_quiet_period_zero_disables_suppression(self, mock_client, component_mapping):
        recent = datetime.now(timezone.utc).isoformat()
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "inc1",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
                "incident_updates": [
                    {"created_at": recent, "body": "Recent update"},
                ],
            }
        ]
        report = make_report(api_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=0,
        )
        assert len(result["updated"]) == 1
        assert result["suppressed"] == []
        mock_client.update_incident.assert_called_once()

    def test_suppressed_count_in_result(self, mock_client, component_mapping):
        recent = datetime.now(timezone.utc).isoformat()
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "inc1",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
                "incident_updates": [
                    {"created_at": recent, "body": "Update"},
                ],
            },
            {
                "id": "inc2",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c2"}],
                "incident_updates": [
                    {"created_at": recent, "body": "Update"},
                ],
            },
        ]
        report = make_report(
            api_status="degraded_performance",
            web_status="degraded_performance",
        )
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=60,
        )
        assert len(result["suppressed"]) == 2
        names = {s["component"] for s in result["suppressed"]}
        assert "Example API" in names
        assert "Website" in names

    def test_no_incident_updates_falls_through_to_update(self, mock_client, component_mapping):
        mock_client.list_unresolved_incidents.return_value = [
            {
                "id": "inc1",
                "status": "investigating",
                "impact": "minor",
                "components": [{"id": "c1"}],
                "incident_updates": [],
            }
        ]
        report = make_report(api_status="degraded_performance")
        result = process_incidents(
            mock_client, component_mapping, report,
            quiet_period_minutes=60,
        )
        assert len(result["updated"]) == 1
        mock_client.update_incident.assert_called_once()


class TestGetLastUpdateTime:

    def test_returns_timestamp_from_first_update(self):
        incident = {
            "incident_updates": [
                {"created_at": "2026-02-10T12:00:00Z", "body": "Latest"},
                {"created_at": "2026-02-10T10:00:00Z", "body": "Oldest"},
            ],
        }
        result = get_last_update_time(incident)
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 10
        assert result.hour == 12

    def test_returns_none_for_empty_updates(self):
        incident = {"incident_updates": []}
        assert get_last_update_time(incident) is None

    def test_returns_none_for_missing_key(self):
        incident = {}
        assert get_last_update_time(incident) is None

    def test_returns_none_for_malformed_timestamp(self):
        incident = {
            "incident_updates": [
                {"created_at": "not-a-date", "body": "Update"},
            ],
        }
        assert get_last_update_time(incident) is None

    def test_handles_offset_timestamps(self):
        incident = {
            "incident_updates": [
                {"created_at": "2026-02-10T12:00:00+05:00", "body": "Update"},
            ],
        }
        result = get_last_update_time(incident)
        assert result is not None
        assert result.hour == 12

    def test_handles_missing_created_at(self):
        incident = {
            "incident_updates": [
                {"body": "No timestamp"},
            ],
        }
        assert get_last_update_time(incident) is None


class TestGenerateHeartbeatBody:

    def test_includes_component_name(self):
        body = generate_heartbeat_body("API Gateway", "degraded_performance")
        assert "API Gateway" in body

    def test_degraded_mentions_reduced_performance(self):
        body = generate_heartbeat_body("API", "degraded_performance")
        assert "reduced performance" in body.lower()

    def test_outage_mentions_unavailable(self):
        body = generate_heartbeat_body("API", "major_outage")
        assert "unavailable" in body.lower()

    def test_differs_from_update_body(self):
        heartbeat = generate_heartbeat_body("API", "degraded_performance")
        update = generate_update_body("API", "degraded_performance")
        assert heartbeat != update

    def test_differs_from_escalation_body(self):
        heartbeat = generate_heartbeat_body("API", "major_outage")
        escalation = generate_update_body("API", "major_outage", escalated=True)
        assert heartbeat != escalation

    def test_no_escalation_language(self):
        body = generate_heartbeat_body("API", "major_outage")
        assert "escalated" not in body.lower()
        assert "urgently" not in body.lower()

    def test_reassuring_tone(self):
        body = generate_heartbeat_body("API", "degraded_performance")
        assert "monitor" in body.lower() or "working" in body.lower()
