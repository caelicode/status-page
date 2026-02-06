#!/usr/bin/env python3

import logging
import sys

from monitoring.config import load_config
from monitoring.grafana_client import SyntheticMonitoringClient, GrafanaClientError

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

    if not config.grafana.synthetic_monitoring_token:
        logger.error(
            "GRAFANA_SM_TOKEN is required for provisioning. "
            "Generate one at grafana.com → your stack → Synthetic Monitoring."
        )
        return 1

    sm = SyntheticMonitoringClient(config.grafana)

    logger.info("Registering with Grafana Synthetic Monitoring...")
    try:
        _token, tenant_id = sm.register()
        logger.info("Registered (tenant: %s)", tenant_id)
    except GrafanaClientError as e:
        logger.error("Registration failed: %s", e)
        return 1

    created = []
    failed = []

    for check in config.checks:
        name = check["name"]
        url = check.get("url", "")
        if not url:
            logger.warning("Check '%s' has no URL — skipping", name)
            continue

        logger.info("Creating check: %s → %s", name, url)
        try:
            sm.create_check(
                job_name=check["job_label"],
                target_url=url,
                headers=check.get("headers", []),
            )
            created.append(name)
        except GrafanaClientError as e:
            logger.error("  Failed: %s", e)
            failed.append(name)

    logger.info("Provisioning complete: %d created/verified, %d failed",
                len(created), len(failed))

    if failed:
        logger.error("Failed checks: %s", ", ".join(failed))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
