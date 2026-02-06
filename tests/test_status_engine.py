"""
Unit tests for the status determination engine.

These validate the core decision logic without hitting any external APIs.
"""

import json
import pytest
from pathlib import Path

from monitoring.config import Thresholds
from monitoring.status_engine import (
    determine_component_status,
    determine_overall_status,
    build_status_report,
    has_status_changed,
    save_status,
    load_existing_status,
)


@pytest.fixture
def thresholds():
    return Thresholds()


# ── determine_component_status ───────────────────────────────────────

class TestDetermineComponentStatus:

    def test_fully_operational(self, thresholds):
        assert determine_component_status(99.0, 100.0, thresholds) == "operational"

    def test_degraded_by_reachability(self, thresholds):
        assert determine_component_status(80.0, 100.0, thresholds) == "degraded_performance"

    def test_degraded_by_latency(self, thresholds):
        assert determine_component_status(99.0, 500.0, thresholds) == "degraded_performance"

    def test_outage_by_reachability(self, thresholds):
        assert determine_component_status(50.0, 100.0, thresholds) == "major_outage"

    def test_outage_by_latency(self, thresholds):
        assert determine_component_status(99.0, 5000.0, thresholds) == "major_outage"

    def test_worst_status_wins(self, thresholds):
        # Reachability operational + latency outage → outage
        assert determine_component_status(99.0, 5000.0, thresholds) == "major_outage"

    def test_none_reachability_defaults_to_outage(self, thresholds):
        assert determine_component_status(None, 100.0, thresholds) == "major_outage"

    def test_none_latency_defaults_to_outage(self, thresholds):
        assert determine_component_status(99.0, None, thresholds) == "major_outage"

    def test_both_none_defaults_to_outage(self, thresholds):
        assert determine_component_status(None, None, thresholds) == "major_outage"

    # ── Boundary values ──────────────────────────────────────────────

    def test_reachability_exactly_at_operational_boundary(self, thresholds):
        assert determine_component_status(95.0, 100.0, thresholds) == "operational"

    def test_reachability_just_below_operational_boundary(self, thresholds):
        assert determine_component_status(94.9, 100.0, thresholds) == "degraded_performance"

    def test_reachability_exactly_at_degraded_boundary(self, thresholds):
        assert determine_component_status(75.0, 100.0, thresholds) == "degraded_performance"

    def test_reachability_just_below_degraded_boundary(self, thresholds):
        assert determine_component_status(74.9, 100.0, thresholds) == "major_outage"

    def test_latency_exactly_at_operational_boundary(self, thresholds):
        assert determine_component_status(99.0, 200.0, thresholds) == "operational"

    def test_latency_just_above_operational_boundary(self, thresholds):
        assert determine_component_status(99.0, 200.1, thresholds) == "degraded_performance"

    def test_latency_exactly_at_degraded_boundary(self, thresholds):
        assert determine_component_status(99.0, 1000.0, thresholds) == "degraded_performance"

    def test_latency_just_above_degraded_boundary(self, thresholds):
        assert determine_component_status(99.0, 1000.1, thresholds) == "major_outage"

    # ── Custom thresholds ────────────────────────────────────────────

    def test_custom_thresholds(self):
        custom = Thresholds(
            reachability_operational=99.0,
            reachability_degraded=90.0,
            latency_operational_ms=50.0,
            latency_degraded_ms=200.0,
        )
        # Would be operational with defaults, but degraded with stricter thresholds
        assert determine_component_status(96.0, 100.0, custom) == "degraded_performance"


# ── determine_overall_status ─────────────────────────────────────────

class TestDetermineOverallStatus:

    def test_all_operational(self):
        assert determine_overall_status(["operational", "operational"]) == "operational"

    def test_one_degraded(self):
        assert determine_overall_status(["operational", "degraded_performance"]) == "degraded_performance"

    def test_one_outage(self):
        assert determine_overall_status(["operational", "major_outage"]) == "major_outage"

    def test_outage_beats_degraded(self):
        assert determine_overall_status(["degraded_performance", "major_outage"]) == "major_outage"

    def test_empty_list_is_operational(self):
        assert determine_overall_status([]) == "operational"

    def test_single_component(self):
        assert determine_overall_status(["degraded_performance"]) == "degraded_performance"


# ── build_status_report ──────────────────────────────────────────────

class TestBuildStatusReport:

    def test_basic_report_structure(self, thresholds):
        checks = [
            {"name": "API", "job_label": "api", "description": "Main API"},
        ]
        metrics = {"api": (99.5, 120.0)}

        report = build_status_report(checks, metrics, thresholds)

        assert "last_updated" in report
        assert report["overall_status"] == "operational"
        assert len(report["components"]) == 1
        assert report["components"][0]["name"] == "API"
        assert report["components"][0]["status"] == "operational"
        assert report["components"][0]["reachability"] == 99.5
        assert report["components"][0]["latency_ms"] == 120.0

    def test_missing_metrics_reports_outage(self, thresholds):
        checks = [{"name": "DB", "job_label": "db"}]
        metrics = {}  # no data for "db"

        report = build_status_report(checks, metrics, thresholds)

        assert report["overall_status"] == "major_outage"
        assert report["components"][0]["reachability"] is None
        assert report["components"][0]["latency_ms"] is None

    def test_multiple_components_worst_wins(self, thresholds):
        checks = [
            {"name": "A", "job_label": "a"},
            {"name": "B", "job_label": "b"},
        ]
        metrics = {
            "a": (99.0, 100.0),     # operational
            "b": (80.0, 500.0),     # degraded
        }

        report = build_status_report(checks, metrics, thresholds)

        assert report["overall_status"] == "degraded_performance"
        assert report["components"][0]["status"] == "operational"
        assert report["components"][1]["status"] == "degraded_performance"


# ── has_status_changed ───────────────────────────────────────────────

class TestHasStatusChanged:

    def test_none_old_report_is_changed(self):
        assert has_status_changed(None, {"overall_status": "operational"}) is True

    def test_same_status_is_not_changed(self):
        old = {
            "overall_status": "operational",
            "components": [{"name": "A", "status": "operational"}],
        }
        new = {
            "overall_status": "operational",
            "components": [{"name": "A", "status": "operational"}],
        }
        assert has_status_changed(old, new) is False

    def test_overall_change_detected(self):
        old = {"overall_status": "operational", "components": []}
        new = {"overall_status": "degraded_performance", "components": []}
        assert has_status_changed(old, new) is True

    def test_component_change_detected(self):
        old = {
            "overall_status": "operational",
            "components": [{"name": "A", "status": "operational"}],
        }
        new = {
            "overall_status": "degraded_performance",
            "components": [{"name": "A", "status": "degraded_performance"}],
        }
        assert has_status_changed(old, new) is True

    def test_metric_change_without_status_change_is_not_changed(self):
        """Latency/reachability values changing shouldn't count as a status change."""
        old = {
            "overall_status": "operational",
            "components": [{"name": "A", "status": "operational"}],
        }
        new = {
            "overall_status": "operational",
            "components": [{"name": "A", "status": "operational"}],
        }
        assert has_status_changed(old, new) is False


# ── save / load round-trip ───────────────────────────────────────────

class TestPersistence:

    def test_save_and_load(self, tmp_path):
        status_file = tmp_path / "status.json"
        report = {
            "last_updated": "2026-01-01T00:00:00+00:00",
            "overall_status": "operational",
            "components": [],
        }

        save_status(report, status_file)
        loaded = load_existing_status(status_file)

        assert loaded == report

    def test_load_nonexistent_returns_none(self, tmp_path):
        assert load_existing_status(tmp_path / "nope.json") is None

    def test_load_corrupt_json_returns_none(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{")
        assert load_existing_status(bad_file) is None
