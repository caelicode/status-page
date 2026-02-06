import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Thresholds

logger = logging.getLogger(__name__)

STATUS_SEVERITY = ["major_outage", "degraded_performance", "operational"]


def determine_component_status(
    reachability: Optional[float],
    latency: Optional[float],
    thresholds: Thresholds,
) -> str:
    if reachability is None or latency is None:
        logger.warning("Missing metrics — conservatively reporting major_outage")
        return "major_outage"

    if reachability >= thresholds.reachability_operational:
        reach_status = "operational"
    elif reachability >= thresholds.reachability_degraded:
        reach_status = "degraded_performance"
    else:
        reach_status = "major_outage"

    if latency <= thresholds.latency_operational_ms:
        lat_status = "operational"
    elif latency <= thresholds.latency_degraded_ms:
        lat_status = "degraded_performance"
    else:
        lat_status = "major_outage"

    for level in STATUS_SEVERITY:
        if level in (reach_status, lat_status):
            return level

    return "operational"


def determine_overall_status(component_statuses: list) -> str:
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
    now = datetime.now(timezone.utc).isoformat()
    components = []
    statuses = []

    for check in checks:
        job_label = check["job_label"]
        reachability, latency = metrics.get(job_label, (None, None))

        check_thresholds = thresholds
        override = check.get("thresholds")
        if override:
            reach_cfg = override.get("reachability", {})
            lat_cfg = override.get("latency_ms", {})
            check_thresholds = Thresholds(
                reachability_operational=reach_cfg.get(
                    "operational", thresholds.reachability_operational
                ),
                reachability_degraded=reach_cfg.get(
                    "degraded", thresholds.reachability_degraded
                ),
                latency_operational_ms=lat_cfg.get(
                    "operational", thresholds.latency_operational_ms
                ),
                latency_degraded_ms=lat_cfg.get(
                    "degraded", thresholds.latency_degraded_ms
                ),
            )

        status = determine_component_status(reachability, latency, check_thresholds)
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
    try:
        with open(status_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_status(report: dict, status_path: Path) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    with open(status_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Status written to %s", status_path)
