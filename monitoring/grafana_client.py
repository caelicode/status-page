"""
Grafana Cloud API client.

Provides two client classes:
    GrafanaClient          — queries Prometheus metrics (used every monitoring cycle)
    SyntheticMonitoringClient — provisions synthetic checks (used once during setup)
"""

import logging
from typing import Optional, Tuple

import requests

from .config import GrafanaConfig

logger = logging.getLogger(__name__)


class GrafanaClientError(Exception):
    """Raised when a Grafana API call fails."""


# ---------------------------------------------------------------------------
# Prometheus query client (used by monitor.py)
# ---------------------------------------------------------------------------

class GrafanaClient:
    """Client for querying Grafana Cloud Prometheus."""

    def __init__(self, config: GrafanaConfig):
        self.config = config
        self._session = requests.Session()

    def query_prometheus(self, query: str) -> dict:
        """
        Execute an instant PromQL query.

        Returns the raw Prometheus API response dict.
        Raises GrafanaClientError on any failure.
        """
        try:
            response = self._session.get(
                self.config.prometheus_url,
                params={"query": query},
                auth=(self.config.prometheus_user_id, self.config.api_key),
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                raise GrafanaClientError(
                    f"Prometheus query failed: {data.get('error', 'unknown error')}"
                )
            return data

        except requests.RequestException as e:
            raise GrafanaClientError(f"Prometheus request failed: {e}") from e

    def fetch_reachability(
        self, job_label: str, window: str = "15m"
    ) -> Optional[float]:
        """
        Fetch average probe success rate over a time window.

        A wider window (e.g. 15m) provides natural debouncing — a single
        transient failure among many probes won't tank the percentage.

        Returns reachability as a percentage (0–100), or None if no data.
        """
        query = f'avg_over_time(probe_success{{job="{job_label}"}}[{window}])'

        try:
            data = self.query_prometheus(query)
            results = data.get("data", {}).get("result", [])

            if not results:
                logger.warning("No reachability data for job '%s'", job_label)
                return None

            return float(results[0]["value"][1]) * 100

        except (KeyError, IndexError, ValueError) as e:
            logger.error("Failed to parse reachability for '%s': %s", job_label, e)
            return None

    def fetch_latency(
        self, job_label: str, window: str = "5m"
    ) -> Optional[float]:
        """
        Fetch average probe duration over a time window.

        Returns latency in milliseconds, or None if no data.
        """
        query = (
            f'avg_over_time(probe_duration_seconds{{job="{job_label}"}}[{window}])'
        )

        try:
            data = self.query_prometheus(query)
            results = data.get("data", {}).get("result", [])

            if not results:
                logger.warning("No latency data for job '%s'", job_label)
                return None

            # Prometheus stores duration in seconds; convert to ms
            return float(results[0]["value"][1]) * 1000

        except (KeyError, IndexError, ValueError) as e:
            logger.error("Failed to parse latency for '%s': %s", job_label, e)
            return None

    def fetch_metrics(
        self,
        job_label: str,
        reachability_window: str = "15m",
        latency_window: str = "5m",
    ) -> Tuple[Optional[float], Optional[float]]:
        """Fetch both reachability and latency for a check."""
        reachability = self.fetch_reachability(job_label, reachability_window)
        latency = self.fetch_latency(job_label, latency_window)
        return reachability, latency


# ---------------------------------------------------------------------------
# Synthetic Monitoring provisioning client (used by setup/provision.py)
# ---------------------------------------------------------------------------

class SyntheticMonitoringClient:
    """Client for creating Grafana Synthetic Monitoring checks."""

    def __init__(self, config: GrafanaConfig):
        self.config = config
        self._session = requests.Session()
        self._access_token: Optional[str] = None
        self._tenant_id: Optional[int] = None

    def register(self) -> Tuple[str, int]:
        """
        Register with the Synthetic Monitoring API.

        Returns (access_token, tenant_id).
        """
        try:
            response = self._session.post(
                f"{self.config.synthetic_monitoring_url}/api/v1/register/install",
                headers={
                    "Authorization": f"Bearer {self.config.synthetic_monitoring_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "stackId": self.config.stack_id,
                    "metricsInstanceId": self.config.metrics_instance_id,
                    "logsInstanceId": self.config.logs_instance_id,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            if "accessToken" not in data:
                raise GrafanaClientError(
                    f"No access token in registration response: {data}"
                )

            self._access_token = data["accessToken"]
            self._tenant_id = data["tenantInfo"]["id"]
            logger.info("Registered with tenant ID: %s", self._tenant_id)
            return self._access_token, self._tenant_id

        except requests.RequestException as e:
            raise GrafanaClientError(f"Registration failed: {e}") from e

    def _ensure_registered(self):
        if not self._access_token or not self._tenant_id:
            raise GrafanaClientError("Must call register() first")

    def check_exists(self, job_name: str) -> bool:
        """Return True if a check with this job name already exists."""
        self._ensure_registered()

        try:
            response = self._session.get(
                f"{self.config.synthetic_monitoring_url}/api/v1/check/list",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json={"tenantId": self._tenant_id},
                timeout=30,
            )
            response.raise_for_status()

            for check in response.json():
                if check.get("job") == job_name:
                    return True
            return False

        except requests.RequestException as e:
            raise GrafanaClientError(f"Failed to list checks: {e}") from e

    def create_check(
        self,
        job_name: str,
        target_url: str,
        frequency_ms: int = 60000,
        timeout_ms: int = 5000,
        probe_ids: Optional[list] = None,
        headers: Optional[list] = None,
    ) -> str:
        """
        Create a synthetic HTTP check.

        Uses multiple probe locations by default for geographic redundancy.
        Skips creation if a check with this job name already exists.

        Returns the job name (used as job_label in PromQL queries).
        """
        self._ensure_registered()

        if probe_ids is None:
            probe_ids = [1, 2, 3]  # Atlanta, Chicago, San Francisco

        if self.check_exists(job_name):
            logger.info("Check '%s' already exists — skipping", job_name)
            return job_name

        settings = {"http": {"method": "GET", "validStatusCodes": [200]}}
        if headers:
            settings["http"]["headers"] = headers

        try:
            response = self._session.post(
                f"{self.config.synthetic_monitoring_url}/api/v1/check/add",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "tenantId": self._tenant_id,
                    "job": job_name,
                    "target": target_url,
                    "probes": probe_ids,
                    "frequency": frequency_ms,
                    "timeout": timeout_ms,
                    "enabled": True,
                    "settings": settings,
                },
                timeout=30,
            )
            response.raise_for_status()
            logger.info("Created check: %s", job_name)
            return job_name

        except requests.RequestException as e:
            raise GrafanaClientError(
                f"Failed to create check '{job_name}': {e}"
            ) from e
