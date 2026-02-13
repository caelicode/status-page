#!/usr/bin/env python3

import json
import logging
import os
import sys
from pathlib import Path

import yaml

from atlassian_statuspage.client import StatuspageClient, StatuspageError
from monitoring.grafana_client import SyntheticMonitoringClient, GrafanaClientError
from monitoring.config import GrafanaConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent


def load_config_yaml(config_path=None):
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def load_existing_statuspage_config(config_path=None):
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "statuspage.json"
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def generate_checks_json(config):
    settings = config.get("settings", {})
    endpoints = config.get("endpoints", {})

    checks = []
    for job_label, endpoint in endpoints.items():
        check = {
            "name": endpoint["name"],
            "job_label": job_label,
            "url": endpoint["url"],
            "description": endpoint.get("description", ""),
        }
        if "thresholds" in endpoint:
            check["thresholds"] = endpoint["thresholds"]
        checks.append(check)

    return {
        "settings": {
            "reachability_query_window": settings.get("reachability_query_window", "15m"),
            "latency_query_window": settings.get("latency_query_window", "5m"),
            "thresholds": settings.get("thresholds", {
                "reachability": {"operational": 95, "degraded": 75},
                "latency_ms": {"operational": 200, "degraded": 1000},
            }),
        },
        "checks": checks,
    }


def generate_statuspage_json(config, component_mapping):
    statuspage = config.get("statuspage", {})
    incidents = config.get("incidents", {})

    return {
        "page_id": statuspage.get("page_id", ""),
        "component_mapping": component_mapping,
        "incidents": {
            "auto_create": incidents.get("auto_create", True),
            "auto_postmortem": incidents.get("auto_postmortem", True),
            "notify_subscribers": incidents.get("notify_subscribers", True),
        },
    }


def save_json(data, path):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def reconcile_grafana(config, sm_client):
    endpoints = config.get("endpoints", {})
    result = {"created": [], "updated": [], "deleted": [], "errors": []}

    try:
        existing_checks = sm_client.list_checks()
    except GrafanaClientError as e:
        logger.error("Failed to list Grafana checks: %s", e)
        result["errors"].append(f"List checks: {e}")
        return result

    existing_by_job = {}
    for check in existing_checks:
        existing_by_job[check.get("job", "")] = check

    desired_jobs = set(endpoints.keys())
    existing_jobs = set(existing_by_job.keys())

    for job_label in desired_jobs:
        endpoint = endpoints[job_label]
        url = endpoint.get("url", "")
        if not url:
            continue

        frequency = endpoint.get("frequency", 60000)
        probes = endpoint.get("probes")
        headers = endpoint.get("headers", [])

        if job_label in existing_jobs:
            existing = existing_by_job[job_label]
            needs_update = (
                existing.get("target") != url
                or existing.get("frequency") != frequency
                or sorted(existing.get("probes", [])) != sorted(probes)
            )

            if needs_update:
                try:
                    sm_client.update_check(
                        check_id=existing["id"],
                        job_name=job_label,
                        target_url=url,
                        frequency_ms=frequency,
                        probe_ids=probes,
                        headers=headers,
                    )
                    result["updated"].append(job_label)
                    logger.info("Updated Grafana check: %s", job_label)
                except GrafanaClientError as e:
                    logger.error("Failed to update check %s: %s", job_label, e)
                    result["errors"].append(f"Update {job_label}: {e}")
            else:
                logger.info("Grafana check %s is up to date", job_label)
        else:
            try:
                sm_client.create_check(
                    job_name=job_label,
                    target_url=url,
                    frequency_ms=frequency,
                    probe_ids=probes,
                    headers=headers,
                )
                result["created"].append(job_label)
                logger.info("Created Grafana check: %s", job_label)
            except GrafanaClientError as e:
                logger.error("Failed to create check %s: %s", job_label, e)
                result["errors"].append(f"Create {job_label}: {e}")

    allow_deletions = os.environ.get("ALLOW_DELETIONS", "false").lower() == "true"
    orphaned = existing_jobs - desired_jobs

    for job_label in orphaned:
        if not allow_deletions:
            logger.warning(
                "Grafana check '%s' is not in config.yaml — skipping deletion "
                "(set ALLOW_DELETIONS=true to remove)", job_label,
            )
            continue

        check = existing_by_job[job_label]
        try:
            sm_client.delete_check(check["id"])
            result["deleted"].append(job_label)
            logger.info("Deleted orphaned Grafana check: %s", job_label)
        except GrafanaClientError as e:
            logger.error("Failed to delete check %s: %s", job_label, e)
            result["errors"].append(f"Delete {job_label}: {e}")

    return result


def reconcile_statuspage(config, sp_client, existing_mapping):
    endpoints = config.get("endpoints", {})
    result = {"created": [], "updated": [], "deleted": [], "errors": [], "warnings": []}
    component_mapping = dict(existing_mapping) if existing_mapping else {}

    try:
        existing_components = sp_client.list_components()
    except StatuspageError as e:
        logger.error("Failed to list Statuspage components: %s", e)
        result["errors"].append(f"List components: {e}")
        return result, component_mapping

    existing_comp_by_name = {c["name"]: c for c in existing_components}
    existing_comp_ids = {c["id"] for c in existing_components}

    existing_metrics = []
    try:
        existing_metrics = sp_client.list_metrics()
    except StatuspageError as e:
        logger.warning("Failed to list Statuspage metrics (may be unavailable): %s", e)
        result["warnings"].append(f"List metrics: {e}")

    existing_metric_by_name = {m["name"]: m for m in existing_metrics}
    existing_metric_ids = {m["id"] for m in existing_metrics}

    desired_jobs = set()
    for job_label, endpoint in endpoints.items():
        if endpoint.get("component", True):
            desired_jobs.add(job_label)

    for job_label in desired_jobs:
        endpoint = endpoints[job_label]
        name = endpoint["name"]
        description = endpoint.get("description", "")
        wants_metric = endpoint.get("metric", True)
        metric_name = f"{name} Latency"

        current = component_mapping.get(job_label, {})
        comp_id = current.get("component_id", "")
        metric_id = current.get("metric_id", "")

        if comp_id and comp_id not in existing_comp_ids:
            logger.warning(
                "Stored component ID %s for %s no longer exists on Statuspage — recreating",
                comp_id, name,
            )
            comp_id = ""

        if metric_id and metric_id not in existing_metric_ids:
            logger.warning(
                "Stored metric ID %s for %s no longer exists on Statuspage — recreating",
                metric_id, name,
            )
            metric_id = ""

        if not comp_id:
            if name in existing_comp_by_name:
                comp_id = existing_comp_by_name[name]["id"]
                logger.info("Adopted existing Statuspage component: %s (ID: %s)", name, comp_id)
            else:
                try:
                    created = sp_client.create_component(
                        name=name,
                        description=description,
                        status="operational",
                    )
                    comp_id = created["id"]
                    result["created"].append(f"component:{name}")
                    logger.info("Created Statuspage component: %s (ID: %s)", name, comp_id)
                except StatuspageError as e:
                    logger.error("Failed to create component %s: %s", name, e)
                    result["errors"].append(f"Create component {name}: {e}")
                    continue

        if wants_metric and not metric_id:
            if metric_name in existing_metric_by_name:
                metric_id = existing_metric_by_name[metric_name]["id"]
                logger.info("Adopted existing Statuspage metric: %s (ID: %s)", metric_name, metric_id)
            else:
                try:
                    created = sp_client.create_metric(
                        name=metric_name,
                        suffix="ms",
                        tooltip=f"Average response time for {name}",
                    )
                    metric_id = created["id"]
                    result["created"].append(f"metric:{metric_name}")
                    logger.info("Created Statuspage metric: %s (ID: %s)", metric_name, metric_id)
                except StatuspageError as e:
                    logger.warning("Failed to create metric %s: %s", metric_name, e)
                    result["warnings"].append(f"Create metric {metric_name}: {e}")

        component_mapping[job_label] = {
            "name": name,
            "component_id": comp_id,
            "metric_id": metric_id if wants_metric else "",
        }

    allow_deletions = os.environ.get("ALLOW_DELETIONS", "false").lower() == "true"
    orphaned = set(component_mapping.keys()) - desired_jobs

    for job_label in orphaned:
        mapping = component_mapping[job_label]
        name = mapping.get("name", job_label)

        if not allow_deletions:
            logger.warning(
                "Statuspage component '%s' is not in config.yaml — skipping deletion "
                "(set ALLOW_DELETIONS=true to remove)", name,
            )
            continue

        metric_id = mapping.get("metric_id")
        if metric_id:
            try:
                sp_client.delete_metric(metric_id)
                result["deleted"].append(f"metric:{name}")
                logger.info("Deleted orphaned metric for %s", name)
            except StatuspageError as e:
                logger.warning("Failed to delete metric for %s: %s", name, e)

        comp_id = mapping.get("component_id")
        if comp_id:
            try:
                sp_client.delete_component(comp_id)
                result["deleted"].append(f"component:{name}")
                logger.info("Deleted orphaned component: %s", name)
            except StatuspageError as e:
                logger.error("Failed to delete component %s: %s", name, e)
                result["errors"].append(f"Delete component {name}: {e}")
                continue

        del component_mapping[job_label]

    return result, component_mapping


def main() -> int:
    try:
        config = load_config_yaml()
    except (FileNotFoundError, yaml.YAMLError) as e:
        logger.error("Failed to load config.yaml: %s", e)
        return 1

    endpoints = config.get("endpoints", {})
    if not endpoints:
        logger.warning("No endpoints defined in config.yaml — nothing to reconcile")
        return 0

    checks_path = PROJECT_ROOT / "config" / "checks.json"
    statuspage_path = PROJECT_ROOT / "config" / "statuspage.json"

    checks_json = generate_checks_json(config)
    save_json(checks_json, checks_path)
    logger.info("Generated %s with %d checks", checks_path, len(checks_json["checks"]))

    grafana_result = None
    sm_token = os.environ.get("GRAFANA_SM_TOKEN", "")
    if sm_token:
        grafana_config = GrafanaConfig(
            prometheus_url=os.environ.get("GRAFANA_PROMETHEUS_URL", ""),
            prometheus_user_id=os.environ.get("GRAFANA_PROMETHEUS_USER_ID", ""),
            api_key=os.environ.get("GRAFANA_API_KEY", ""),
            synthetic_monitoring_url=os.environ.get(
                "GRAFANA_SM_URL",
                "https://synthetic-monitoring-api-us-east-0.grafana.net",
            ),
            synthetic_monitoring_token=sm_token,
            stack_id=int(os.environ.get("GRAFANA_STACK_ID", "0")),
            metrics_instance_id=int(os.environ.get("GRAFANA_METRICS_INSTANCE_ID", "0")),
            logs_instance_id=int(os.environ.get("GRAFANA_LOGS_INSTANCE_ID", "0")),
        )

        sm_client = SyntheticMonitoringClient(grafana_config)

        try:
            sm_client.register()
            logger.info("Authenticated with Grafana Synthetic Monitoring")
        except GrafanaClientError as e:
            logger.error("Grafana SM authentication failed: %s", e)
            return 1

        grafana_result = reconcile_grafana(config, sm_client)
        logger.info(
            "Grafana reconciliation: %d created, %d updated, %d deleted, %d errors",
            len(grafana_result["created"]),
            len(grafana_result["updated"]),
            len(grafana_result["deleted"]),
            len(grafana_result["errors"]),
        )
    else:
        logger.info("GRAFANA_SM_TOKEN not set — skipping Grafana check provisioning")

    sp_result = None
    sp_api_key = os.environ.get("STATUSPAGE_API_KEY", "")
    page_id = config.get("statuspage", {}).get("page_id", "")

    if sp_api_key and page_id:
        sp_client = StatuspageClient(sp_api_key, page_id)

        existing_sp = load_existing_statuspage_config(statuspage_path)
        existing_mapping = existing_sp.get("component_mapping", {}) if existing_sp else {}

        sp_result, component_mapping = reconcile_statuspage(
            config, sp_client, existing_mapping,
        )

        statuspage_json = generate_statuspage_json(config, component_mapping)
        save_json(statuspage_json, statuspage_path)
        logger.info("Generated %s with %d component mappings", statuspage_path, len(component_mapping))

        logger.info(
            "Statuspage reconciliation: %d created, %d deleted, %d errors, %d warnings",
            len(sp_result["created"]),
            len(sp_result["deleted"]),
            len(sp_result["errors"]),
            len(sp_result.get("warnings", [])),
        )
    else:
        existing_sp = load_existing_statuspage_config(statuspage_path)
        existing_mapping = existing_sp.get("component_mapping", {}) if existing_sp else {}
        statuspage_json = generate_statuspage_json(config, existing_mapping)
        save_json(statuspage_json, statuspage_path)
        logger.info("STATUSPAGE_API_KEY or page_id not set — generated config from existing mappings")

    has_errors = False
    if grafana_result and grafana_result["errors"]:
        has_errors = True
    if sp_result and sp_result["errors"]:
        has_errors = True

    if sp_result and sp_result.get("warnings"):
        for w in sp_result["warnings"]:
            logger.warning("Non-fatal: %s", w)

    if has_errors:
        logger.error("Reconciliation completed with errors")
        return 1

    logger.info("Reconciliation complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
