"""
Centralized configuration loading.

All environment variables, thresholds, and check definitions are loaded here.
Nothing is hardcoded in the monitoring or setup modules — everything flows
through this config layer.
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Project root: parent of the monitoring/ package directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Thresholds:
    """Status determination thresholds.

    Reachability is a percentage (0-100). Latency is in milliseconds.
    A component is 'operational' if reachability >= reachability_operational
    AND latency <= latency_operational_ms. It's 'degraded_performance' if
    either metric falls into the degraded range. It's 'major_outage' if
    either metric falls below the degraded threshold.
    """
    reachability_operational: float = 95.0
    reachability_degraded: float = 75.0
    latency_operational_ms: float = 200.0
    latency_degraded_ms: float = 1000.0


@dataclass
class GrafanaConfig:
    """Grafana Cloud connection configuration."""
    prometheus_url: str
    prometheus_user_id: str
    api_key: str
    synthetic_monitoring_url: str = (
        "https://synthetic-monitoring-api-us-east-0.grafana.net"
    )
    synthetic_monitoring_token: str = ""
    stack_id: int = 0
    metrics_instance_id: int = 0
    logs_instance_id: int = 0


@dataclass
class MonitorConfig:
    """Complete monitoring configuration."""
    grafana: GrafanaConfig
    thresholds: Thresholds
    reachability_query_window: str = "15m"
    latency_query_window: str = "5m"
    checks: list = field(default_factory=list)
    status_file: Path = field(default=None)

    def __post_init__(self):
        if self.status_file is None:
            self.status_file = PROJECT_ROOT / "github-pages" / "status.json"


def load_checks(config_path: Optional[Path] = None) -> list:
    """Load check definitions from the config file."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "checks.json"

    with open(config_path, "r") as f:
        data = json.load(f)
    return data.get("checks", [])


def load_settings(config_path: Optional[Path] = None) -> dict:
    """Load settings block from the config file."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "checks.json"

    with open(config_path, "r") as f:
        data = json.load(f)
    return data.get("settings", {})


def load_config() -> MonitorConfig:
    """
    Build a MonitorConfig from environment variables and config/checks.json.

    Required environment variables:
        GRAFANA_PROMETHEUS_URL       Grafana Cloud Prometheus query endpoint
        GRAFANA_PROMETHEUS_USER_ID   Grafana Cloud instance / user ID
        GRAFANA_API_KEY              Grafana Cloud API key

    Optional environment variables (needed only for provisioning):
        GRAFANA_SM_URL               Synthetic Monitoring API base URL
        GRAFANA_SM_TOKEN             Synthetic Monitoring access token
        GRAFANA_STACK_ID             Grafana Cloud stack ID
        GRAFANA_METRICS_INSTANCE_ID  Prometheus metrics instance ID
        GRAFANA_LOGS_INSTANCE_ID     Loki logs instance ID
    """
    settings = load_settings()
    thresholds_cfg = settings.get("thresholds", {})
    reachability_cfg = thresholds_cfg.get("reachability", {})
    latency_cfg = thresholds_cfg.get("latency_ms", {})

    thresholds = Thresholds(
        reachability_operational=reachability_cfg.get("operational", 95.0),
        reachability_degraded=reachability_cfg.get("degraded", 75.0),
        latency_operational_ms=latency_cfg.get("operational", 200.0),
        latency_degraded_ms=latency_cfg.get("degraded", 1000.0),
    )

    grafana = GrafanaConfig(
        prometheus_url=os.environ["GRAFANA_PROMETHEUS_URL"],
        prometheus_user_id=os.environ["GRAFANA_PROMETHEUS_USER_ID"],
        api_key=os.environ["GRAFANA_API_KEY"],
        synthetic_monitoring_url=os.getenv(
            "GRAFANA_SM_URL",
            "https://synthetic-monitoring-api-us-east-0.grafana.net",
        ),
        synthetic_monitoring_token=os.getenv("GRAFANA_SM_TOKEN", ""),
        stack_id=int(os.getenv("GRAFANA_STACK_ID", "0")),
        metrics_instance_id=int(os.getenv("GRAFANA_METRICS_INSTANCE_ID", "0")),
        logs_instance_id=int(os.getenv("GRAFANA_LOGS_INSTANCE_ID", "0")),
    )

    checks = load_checks()

    return MonitorConfig(
        grafana=grafana,
        thresholds=thresholds,
        reachability_query_window=settings.get("reachability_query_window", "15m"),
        latency_query_window=settings.get("latency_query_window", "5m"),
        checks=checks,
    )
