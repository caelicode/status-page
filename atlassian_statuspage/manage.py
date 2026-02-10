#!/usr/bin/env python3
"""CLI tool for managing Statuspage components and metrics.

Usage:
    python -m atlassian_statuspage.manage <command> [options]

Commands:
    sync-components     Auto-create components from checks.json, update config
    sync-metrics        Auto-create latency metrics for each component, update config
    delete-component    Delete a component by job label
    delete-metric       Delete a metric by job label
    list-components     List all components on the page
    list-metrics        List all metrics on the page
    list-incidents      List all incidents on the page
    cleanup             Delete all components and metrics managed by this tool
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from atlassian_statuspage.client import StatuspageClient, StatuspageError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_checks_config(config_path=None):
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "checks.json"
    with open(config_path, "r") as f:
        return json.load(f)


def load_statuspage_config(config_path=None):
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "statuspage.json"
    with open(config_path, "r") as f:
        return json.load(f)


def save_statuspage_config(config, config_path=None):
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "statuspage.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    logger.info("Saved config to %s", config_path)


def get_client():
    api_key = os.environ.get("STATUSPAGE_API_KEY")
    if not api_key:
        logger.error("STATUSPAGE_API_KEY environment variable is required")
        sys.exit(1)

    config = load_statuspage_config()
    page_id = config.get("page_id")
    if not page_id:
        logger.error("page_id is required in config/statuspage.json")
        sys.exit(1)

    return StatuspageClient(api_key, page_id), config


def cmd_sync_components(args):
    """Create Statuspage components from checks.json and update config."""
    client, sp_config = get_client()
    checks = load_checks_config()
    component_mapping = sp_config.get("component_mapping", {})

    # Fetch existing components from Statuspage
    try:
        existing = client.list_components()
    except StatuspageError as e:
        logger.error("Failed to list existing components: %s", e)
        return 1

    existing_by_name = {c["name"]: c for c in existing}

    created_count = 0
    skipped_count = 0

    for check in checks.get("checks", []):
        name = check["name"]
        job_label = check["job_label"]
        description = check.get("description", "")

        # Check if already in our config with a component_id
        if job_label in component_mapping and component_mapping[job_label].get("component_id"):
            logger.info("  %s — already configured (component_id: %s)",
                        name, component_mapping[job_label]["component_id"])
            skipped_count += 1
            continue

        # Check if component already exists on Statuspage by name
        if name in existing_by_name:
            comp_id = existing_by_name[name]["id"]
            logger.info("  %s — exists on Statuspage (ID: %s), updating config", name, comp_id)
            component_mapping[job_label] = {
                "name": name,
                "component_id": comp_id,
                "metric_id": component_mapping.get(job_label, {}).get("metric_id", ""),
            }
            skipped_count += 1
            continue

        # Create new component
        try:
            result = client.create_component(
                name=name,
                description=description,
                status="operational",
            )
            comp_id = result["id"]
            logger.info("  %s — created (ID: %s)", name, comp_id)
            component_mapping[job_label] = {
                "name": name,
                "component_id": comp_id,
                "metric_id": "",
            }
            created_count += 1
        except StatuspageError as e:
            logger.error("  %s — failed to create: %s", name, e)
            return 1

    sp_config["component_mapping"] = component_mapping
    save_statuspage_config(sp_config)

    logger.info("Sync complete: %d created, %d skipped", created_count, skipped_count)
    return 0


def cmd_sync_metrics(args):
    """Create latency metrics for each component and update config."""
    client, sp_config = get_client()
    component_mapping = sp_config.get("component_mapping", {})

    if not component_mapping:
        logger.warning("No component mappings — run sync-components first")
        return 0

    # Fetch existing metrics from Statuspage
    try:
        existing = client.list_metrics()
    except StatuspageError as e:
        logger.error("Failed to list existing metrics: %s", e)
        return 1

    existing_by_name = {m["name"]: m for m in existing}

    created_count = 0
    skipped_count = 0

    for job_label, mapping in component_mapping.items():
        name = mapping.get("name", job_label)
        metric_name = f"{name} Latency"

        # Already has a metric_id
        if mapping.get("metric_id"):
            logger.info("  %s — already has metric (ID: %s)", name, mapping["metric_id"])
            skipped_count += 1
            continue

        # Check if metric exists on Statuspage by name
        if metric_name in existing_by_name:
            metric_id = existing_by_name[metric_name]["id"]
            logger.info("  %s — metric exists on Statuspage (ID: %s), updating config",
                        metric_name, metric_id)
            mapping["metric_id"] = metric_id
            skipped_count += 1
            continue

        # Create new metric
        try:
            result = client.create_metric(
                name=metric_name,
                suffix="ms",
                tooltip=f"Average response time for {name}",
            )
            metric_id = result["id"]
            logger.info("  %s — created (ID: %s)", metric_name, metric_id)
            mapping["metric_id"] = metric_id
            created_count += 1
        except StatuspageError as e:
            logger.error("  %s — failed to create: %s", metric_name, e)
            logger.warning("  (Metric creation may be disabled by Atlassian — check metastatuspage.com)")

    sp_config["component_mapping"] = component_mapping
    save_statuspage_config(sp_config)

    logger.info("Sync complete: %d created, %d skipped", created_count, skipped_count)
    return 0


def cmd_delete_component(args):
    """Delete a component by job label."""
    client, sp_config = get_client()
    component_mapping = sp_config.get("component_mapping", {})

    job_label = args.job_label
    if job_label not in component_mapping:
        logger.error("Job label '%s' not found in config", job_label)
        return 1

    mapping = component_mapping[job_label]
    component_id = mapping.get("component_id")
    name = mapping.get("name", job_label)

    if not component_id:
        logger.warning("No component_id for '%s' — removing from config only", job_label)
        del component_mapping[job_label]
        sp_config["component_mapping"] = component_mapping
        save_statuspage_config(sp_config)
        return 0

    # Delete metric first if it exists
    metric_id = mapping.get("metric_id")
    if metric_id:
        try:
            client.delete_metric(metric_id)
            logger.info("  Deleted metric %s for %s", metric_id, name)
        except StatuspageError as e:
            logger.warning("  Failed to delete metric %s: %s", metric_id, e)

    # Delete component
    try:
        client.delete_component(component_id)
        logger.info("  Deleted component %s (%s)", name, component_id)
    except StatuspageError as e:
        logger.error("  Failed to delete component %s: %s", component_id, e)
        return 1

    del component_mapping[job_label]
    sp_config["component_mapping"] = component_mapping
    save_statuspage_config(sp_config)

    logger.info("Removed '%s' from config and Statuspage", job_label)
    return 0


def cmd_delete_metric(args):
    """Delete a metric by job label."""
    client, sp_config = get_client()
    component_mapping = sp_config.get("component_mapping", {})

    job_label = args.job_label
    if job_label not in component_mapping:
        logger.error("Job label '%s' not found in config", job_label)
        return 1

    mapping = component_mapping[job_label]
    metric_id = mapping.get("metric_id")
    name = mapping.get("name", job_label)

    if not metric_id:
        logger.warning("No metric_id for '%s' — nothing to delete", job_label)
        return 0

    try:
        client.delete_metric(metric_id)
        logger.info("Deleted metric %s for %s", metric_id, name)
    except StatuspageError as e:
        logger.error("Failed to delete metric %s: %s", metric_id, e)
        return 1

    mapping["metric_id"] = ""
    sp_config["component_mapping"] = component_mapping
    save_statuspage_config(sp_config)

    return 0


def cmd_list_components(args):
    """List all components on the page."""
    client, _ = get_client()

    try:
        components = client.list_components()
    except StatuspageError as e:
        logger.error("Failed to list components: %s", e)
        return 1

    if not components:
        print("No components found.")
        return 0

    print(f"\n{'Name':<30} {'ID':<15} {'Status':<20} {'Group ID'}")
    print("-" * 85)
    for comp in components:
        print(
            f"{comp['name']:<30} {comp['id']:<15} "
            f"{comp['status']:<20} {comp.get('group_id', '-')}"
        )
    print(f"\nTotal: {len(components)} components")
    return 0


def cmd_list_metrics(args):
    """List all metrics on the page."""
    client, _ = get_client()

    try:
        metrics = client.list_metrics()
    except StatuspageError as e:
        logger.error("Failed to list metrics: %s", e)
        return 1

    if not metrics:
        print("No metrics found.")
        return 0

    print(f"\n{'Name':<35} {'ID':<15} {'Suffix':<10}")
    print("-" * 65)
    for m in metrics:
        print(f"{m['name']:<35} {m['id']:<15} {m.get('suffix', '-'):<10}")
    print(f"\nTotal: {len(metrics)} metrics")
    return 0


def cmd_list_incidents(args):
    """List all incidents on the page."""
    client, _ = get_client()

    try:
        incidents = client.list_incidents()
    except StatuspageError as e:
        logger.error("Failed to list incidents: %s", e)
        return 1

    if not incidents:
        print("No incidents found.")
        return 0

    print(f"\n{'Name':<45} {'ID':<15} {'Status':<15} {'Impact'}")
    print("-" * 90)
    for inc in incidents:
        print(
            f"{inc['name'][:44]:<45} {inc['id']:<15} "
            f"{inc['status']:<15} {inc.get('impact', '-')}"
        )
    print(f"\nTotal: {len(incidents)} incidents")
    return 0


def cmd_cleanup(args):
    """Delete all managed components and metrics from Statuspage."""
    client, sp_config = get_client()
    component_mapping = sp_config.get("component_mapping", {})

    if not component_mapping:
        logger.info("No components to clean up")
        return 0

    if not args.yes:
        names = [m.get("name", k) for k, m in component_mapping.items()]
        print(f"This will delete {len(names)} component(s): {', '.join(names)}")
        confirm = input("Are you sure? (y/N): ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return 0

    errors = 0
    for job_label, mapping in list(component_mapping.items()):
        name = mapping.get("name", job_label)

        # Delete metric
        metric_id = mapping.get("metric_id")
        if metric_id:
            try:
                client.delete_metric(metric_id)
                logger.info("  Deleted metric for %s", name)
            except StatuspageError as e:
                logger.warning("  Failed to delete metric for %s: %s", name, e)

        # Delete component
        component_id = mapping.get("component_id")
        if component_id:
            try:
                client.delete_component(component_id)
                logger.info("  Deleted component %s", name)
            except StatuspageError as e:
                logger.error("  Failed to delete component %s: %s", name, e)
                errors += 1

    sp_config["component_mapping"] = {}
    save_statuspage_config(sp_config)

    if errors:
        logger.error("Cleanup completed with %d error(s)", errors)
        return 1

    logger.info("Cleanup complete — all managed components removed")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Manage Statuspage components, metrics, and incidents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("sync-components", help="Create components from checks.json")
    subparsers.add_parser("sync-metrics", help="Create latency metrics for components")

    delete_comp = subparsers.add_parser("delete-component", help="Delete a component")
    delete_comp.add_argument("job_label", help="Job label from checks.json")

    delete_metric = subparsers.add_parser("delete-metric", help="Delete a metric")
    delete_metric.add_argument("job_label", help="Job label from checks.json")

    subparsers.add_parser("list-components", help="List all page components")
    subparsers.add_parser("list-metrics", help="List all page metrics")
    subparsers.add_parser("list-incidents", help="List all page incidents")

    cleanup = subparsers.add_parser("cleanup", help="Delete all managed components/metrics")
    cleanup.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "sync-components": cmd_sync_components,
        "sync-metrics": cmd_sync_metrics,
        "delete-component": cmd_delete_component,
        "delete-metric": cmd_delete_metric,
        "list-components": cmd_list_components,
        "list-metrics": cmd_list_metrics,
        "list-incidents": cmd_list_incidents,
        "cleanup": cmd_cleanup,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
