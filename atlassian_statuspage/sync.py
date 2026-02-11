#!/usr/bin/env python3

import json
import logging
import os
import sys
from pathlib import Path

from atlassian_statuspage.client import StatuspageClient, StatuspageError
from atlassian_statuspage.incident_manager import process_incidents

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

STATUS_MAP = {
    "operational": "operational",
    "degraded_performance": "degraded_performance",
    "major_outage": "major_outage",
}


def load_statuspage_config(config_path=None):
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "statuspage.json"
    with open(config_path, "r") as f:
        return json.load(f)


def load_status_report(status_path=None):
    if status_path is None:
        status_path = PROJECT_ROOT / "github-pages" / "status.json"
    with open(status_path, "r") as f:
        return json.load(f)


def main() -> int:
    api_key = os.environ.get("STATUSPAGE_API_KEY")
    if not api_key:
        logger.error("STATUSPAGE_API_KEY environment variable is required")
        return 1

    try:
        config = load_statuspage_config()
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load config/statuspage.json: %s", e)
        return 1

    page_id = config.get("page_id")
    if not page_id:
        logger.error("page_id is required in config/statuspage.json")
        return 1

    try:
        report = load_status_report()
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load github-pages/status.json: %s", e)
        return 1

    client = StatuspageClient(api_key, page_id)
    component_mapping = config.get("component_mapping", {})

    if not component_mapping:
        logger.warning("No component mappings in config/statuspage.json — nothing to sync")
        return 0

    updated = []
    failed = []

    for job_label, mapping in component_mapping.items():
        component_id = mapping.get("component_id")
        metric_id = mapping.get("metric_id")
        component_name = mapping.get("name", job_label)

        component_data = None
        for comp in report.get("components", []):
            if comp["name"] == component_name:
                component_data = comp
                break

        if not component_data:
            logger.warning("No status data for '%s' — skipping", component_name)
            continue

        our_status = component_data["status"]
        sp_status = STATUS_MAP.get(our_status, "major_outage")

        if component_id:
            logger.info("Updating %s → %s", component_name, sp_status)
            try:
                client.update_component_status(component_id, sp_status)
                updated.append(component_name)
            except StatuspageError as e:
                logger.error("  Failed: %s", e)
                failed.append(component_name)

        latency = component_data.get("latency_ms")
        if metric_id and latency is not None:
            logger.info(
                "Submitting latency metric for %s: %.1f ms", component_name, latency
            )
            try:
                client.submit_metric_data(metric_id, latency)
            except StatuspageError as e:
                logger.error("  Metric submission failed: %s", e)

    logger.info(
        "Sync complete: %d updated, %d failed", len(updated), len(failed)
    )

    incident_settings = config.get("incidents", {})
    auto_incidents = incident_settings.get("auto_create", True)
    auto_postmortem = incident_settings.get("auto_postmortem", True)
    notify_subscribers = incident_settings.get("notify_subscribers", True)
    quiet_period_minutes = incident_settings.get("quiet_period_minutes", 60)

    if auto_incidents:
        logger.info("Running incident automation...")
        incident_result = process_incidents(
            client=client,
            component_mapping=component_mapping,
            status_report=report,
            auto_incidents=auto_incidents,
            auto_postmortem=auto_postmortem,
            notify_subscribers=notify_subscribers,
            quiet_period_minutes=quiet_period_minutes,
        )

        if incident_result["created"]:
            logger.info(
                "Incidents created: %s",
                ", ".join(i["component"] for i in incident_result["created"]),
            )
        if incident_result["resolved"]:
            logger.info(
                "Incidents resolved: %s",
                ", ".join(i["component"] for i in incident_result["resolved"]),
            )
        if incident_result.get("suppressed"):
            logger.info(
                "Suppressed duplicate updates: %s",
                ", ".join(i["component"] for i in incident_result["suppressed"]),
            )
        if incident_result["errors"]:
            logger.warning(
                "Incident errors: %s",
                "; ".join(incident_result["errors"]),
            )
    else:
        logger.info("Incident automation disabled in config")

    if failed:
        logger.error("Failed components: %s", ", ".join(failed))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
