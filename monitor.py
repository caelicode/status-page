#!/usr/bin/env python3

import logging
import sys

from monitoring.config import load_config
from monitoring.grafana_client import GrafanaClient, GrafanaClientError
from monitoring.status_engine import (
    build_status_report,
    has_status_changed,
    load_existing_status,
    save_status,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    try:
        config = load_config()
    except KeyError as e:
        logger.error("Missing environment variable: %s", e)
        return 1
    except FileNotFoundError as e:
        logger.error("Config file not found: %s", e)
        return 1

    if not config.checks:
        logger.warning("No checks defined in config/checks.json — nothing to do")
        return 0

    client = GrafanaClient(config.grafana)

    metrics = {}
    for check in config.checks:
        job_label = check["job_label"]
        name = check["name"]
        logger.info("Fetching metrics for: %s (%s)", name, job_label)

        try:
            reachability, latency = client.fetch_metrics(
                job_label,
                reachability_window=config.reachability_query_window,
                latency_window=config.latency_query_window,
            )
            metrics[job_label] = (reachability, latency)

            if reachability is not None:
                logger.info("  Reachability: %.1f%%", reachability)
            if latency is not None:
                logger.info("  Latency: %.1f ms", latency)

        except GrafanaClientError as e:
            logger.error("  Failed: %s", e)
            metrics[job_label] = (None, None)

    report = build_status_report(config.checks, metrics, config.thresholds)
    logger.info("Overall status: %s", report["overall_status"])

    existing = load_existing_status(config.status_file)
    if has_status_changed(existing, report):
        logger.info("Status CHANGED — updating status.json")
    else:
        logger.info("Status unchanged — updating metrics only")

    save_status(report, config.status_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
