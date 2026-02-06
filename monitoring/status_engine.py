"""
Status determination engine.

Determines component and overall system status based on reachability
and latency metrics against configurable thresholds.

Status levels (worst to best):
    major_outage           — service unreachable or unacceptably slow
    degraded_performance   — reachable but experiencing issues
    operational            — healthy

Debouncing strategy:
    Rather than tracking consecutive failure counts (which requires
    persistent state between runs), we use wide Prometheus query windows
    (e.g. 15 minutes for reachability). This means a single bad probe
    among many won't flip the status — the average naturally smooths
    transient blips. No state file needed.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Thresholds

logger = logging.getLogger(__name__)

# Ordered worst-first so we can find the worst status in a list
STATUS_SEVERITY = ["major_outage", "degraded_performance", "operational"]


def determine_component_status(
    reachability: Optional[float],
    latency: Optional[float],
    thresholds: Thresholds,
) -> str:
    """
    Determine a single component's status.

    If either metric is None (fetch failed), we conservatively report
    major_outage — we can't confirm the service is healthy.
    """
    if reachability is None or latency is None:
        logger.warning("Missing metrics — conservatively reporting major_outage")
        return "major_outage"

    # Reachability status
    if reachability >= thresholds.reachability_operational:
        reach_status = "operational"
    elif reachability >= thresholds.reachability_degraded:
        reach_status = "degraded_performance"
    else:
        reach_status = "major_outage"

    # Latency status
    if latency <= thresholds.latency_operational_ms:
        lat_status = "operational"
    elif latency <= thresholds.latency_degraded_ms:
        lat_status = "degraded_performance"
    else:
        lat_status = "major_outage"

    # Worst of the two wins
    for level in STATUS_SEVERITY:
        if level in (reach_status, lat_status):
            return level

    return "operational"


def determine_overall_status(component_statuses: list) -> str:
    """Return the worst status across all components."""
    if not component_statuses:
        return "operational"

    for level in STATUS_SEVERITY:
        if level in component_statuses:
            return level

    return "operational"


def build_status_report(
    checks: list,
    metrics: dict,
    thresholds: Thresholds,
) -> dict:
    """
    Build a complete status report for the status page.

    Args:
        checks:     List of check dicts from config/checks.json.
        metrics:    Dict mapping job_label -> (reachability, latency).
        thresholds: Status thresholds.

    Returns:
        Status report dict ready to be serialized as JSON.
    """
    now = datetime.now(timezone.utc).isoformat()
    components = []
    statuses = []

    for check in checks:
        job_label = check["job_label"]
        reachability, latency = metrics.get(job_label, (None, None))
        status = determine_component_status(reachability, latency, thresholds)
        statuses.append(status)

        components.append({
            "name": check["name"],
            "description": check.get("description", ""),
            "status": status,
            "reachability": round(reachability, 2) if reachability is not None else None,
            "latency_ms": round(latency, 2) if latency is not None else None,
            "last_checked": now,
        })

    return {
        "last_updated": now,
        "overall_status": determine_overall_status(statuses),
        "components": components,
    }


def has_status_changed(old_report: Optional[dict], new_report: dict) -> bool:
    """
    Check if any component's status (not just its metrics) has changed.

    Only considers status transitions — latency/reachability value changes
    alone don't count as a "change" for notification purposes.
    """
    if old_report is None:
        return True

    if old_report.get("overall_status") != new_report.get("overall_status"):
        return True

    old_statuses = {
        c["name"]: c["status"] for c in old_report.get("components", [])
    }
    new_statuses = {
        c["name"]: c["status"] for c in new_report.get("components", [])
    }

    return old_statuses != new_statuses


def load_existing_status(status_path: Path) -> Optional[dict]:
    """Load the current status.json, or None if it doesn't exist."""
    try:
        with open(status_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_status(report: dict, status_path: Path) -> None:
    """Write the status report to disk."""
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with open(status_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Status written to %s", status_path)
