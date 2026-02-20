import logging
import time
from typing import Optional, Tuple

import requests

from .config import GrafanaConfig

logger = logging.getLogger(__name__)


class GrafanaClientError(Exception):
    pass


class GrafanaClient:

    def __init__(self, config: GrafanaConfig):
        self.config = config
        self._session = requests.Session()

    def query_prometheus(self, query: str) -> dict:
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
        query = (
            f'avg_over_time(probe_duration_seconds{{job="{job_label}"}}[{window}])'
        )

        try:
            data = self.query_prometheus(query)
            results = data.get("data", {}).get("result", [])

            if not results:
                logger.warning("No latency data for job '%s'", job_label)
                return None

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
        reachability = self.fetch_reachability(job_label, reachability_window)
        latency = self.fetch_latency(job_label, latency_window)
        return reachability, latency


class SyntheticMonitoringClient:

    def __init__(self, config: GrafanaConfig):
        self.config = config
        self._session = requests.Session()
        self._access_token: Optional[str] = None
        self._tenant_id: Optional[int] = None

    def _get_tenant_id(self, token: str) -> Optional[int]:
        try:
            resp = self._session.get(
                f"{self.config.synthetic_monitoring_url}/api/v1/tenant",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                tenant_id = data.get("id", 0)
                if tenant_id:
                    return tenant_id
        except requests.RequestException:
            pass
        return None

    def register(self) -> Tuple[str, int]:
        sm_token = self.config.synthetic_monitoring_token
        if sm_token:
            try:
                probe = self._session.get(
                    f"{self.config.synthetic_monitoring_url}/api/v1/check/list",
                    headers={
                        "Authorization": f"Bearer {sm_token}",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
                if probe.status_code == 200:
                    checks = probe.json()
                    if isinstance(checks, list) and checks:
                        self._access_token = sm_token
                        self._tenant_id = checks[0].get("tenantId", 0)
                        logger.info(
                            "Using existing SM access token (tenant: %s)",
                            self._tenant_id,
                        )
                        return self._access_token, self._tenant_id

                    tenant_id = self._get_tenant_id(sm_token)
                    if tenant_id:
                        self._access_token = sm_token
                        self._tenant_id = tenant_id
                        logger.info(
                            "Resolved tenant ID %s via /api/v1/tenant",
                            self._tenant_id,
                        )
                        return self._access_token, self._tenant_id

                    logger.info(
                        "SM token valid but could not resolve tenant ID — "
                        "falling back to /register/install"
                    )
            except requests.RequestException:
                pass

        try:
            response = self._session.post(
                f"{self.config.synthetic_monitoring_url}/api/v1/register/install",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
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
        if self._access_token is None:
            raise GrafanaClientError("Must call register() first")

    def list_probes(self) -> list:
        self._ensure_registered()

        try:
            response = self._session.get(
                f"{self.config.synthetic_monitoring_url}/api/v1/probe/list",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            raise GrafanaClientError(f"Failed to list probes: {e}") from e

    def get_default_probe_ids(self, count: int = 3) -> list:
        probes = self.list_probes()
        public_probes = [p for p in probes if p.get("public") is True]
        if not public_probes:
            public_probes = probes
        selected = [p["id"] for p in public_probes[:count]]
        logger.info(
            "Resolved %d probe IDs from %d total (%d public): %s",
            len(selected), len(probes), len(public_probes), selected,
        )
        return selected

    def list_checks(self) -> list:
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
            return response.json()

        except requests.RequestException as e:
            raise GrafanaClientError(f"Failed to list checks: {e}") from e

    def check_exists(self, job_name: str) -> bool:
        self._ensure_registered()

        checks = self.list_checks()
        for check in checks:
            if check.get("job") == job_name:
                return True
        return False

    def delete_check(self, check_id: int, verify: bool = True) -> None:
        self._ensure_registered()

        try:
            response = self._session.post(
                f"{self.config.synthetic_monitoring_url}/api/v1/check/delete",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json={"tenantId": self._tenant_id, "id": check_id},
                timeout=30,
            )
            if response.status_code >= 400:
                logger.error(
                    "Check delete response (%s): %s",
                    response.status_code,
                    response.text[:500],
                )
            response.raise_for_status()

            resp_body = response.text[:500] if response.text else "(empty)"
            logger.info(
                "Delete API returned %s for check ID %s: %s",
                response.status_code, check_id, resp_body,
            )

        except requests.RequestException as e:
            raise GrafanaClientError(
                f"Failed to delete check {check_id}: {e}"
            ) from e

        if not verify:
            return

        for attempt in range(1, 4):
            time.sleep(2 * attempt)
            remaining = self.list_checks()
            remaining_ids = {c.get("id") for c in remaining}
            if check_id not in remaining_ids:
                logger.info(
                    "Verified check ID %s deleted (attempt %d)", check_id, attempt,
                )
                return
            logger.warning(
                "Check ID %s still present after delete (attempt %d/3)",
                check_id, attempt,
            )

        raise GrafanaClientError(
            f"Check {check_id} still exists after deletion - "
            f"API returned success but check was not removed"
        )

    def update_check(
        self,
        check_id: int,
        job_name: str,
        target_url: str,
        frequency_ms: int = 60000,
        timeout_ms: int = 5000,
        probe_ids: Optional[list] = None,
        headers: Optional[list] = None,
    ) -> dict:
        self._ensure_registered()

        if probe_ids is None:
            probe_ids = self.get_default_probe_ids()

        settings = {"http": {"method": "GET", "validStatusCodes": [200]}}
        if headers:
            settings["http"]["headers"] = headers

        try:
            response = self._session.post(
                f"{self.config.synthetic_monitoring_url}/api/v1/check/update",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "tenantId": self._tenant_id,
                    "id": check_id,
                    "job": job_name,
                    "target": target_url,
                    "probes": probe_ids,
                    "frequency": frequency_ms,
                    "timeout": timeout_ms,
                    "enabled": True,
                    "alertSensitivity": "none",
                    "basicMetricsOnly": True,
                    "labels": [],
                    "settings": settings,
                },
                timeout=30,
            )
            response.raise_for_status()
            logger.info("Updated check: %s", job_name)
            return response.json()

        except requests.RequestException as e:
            raise GrafanaClientError(
                f"Failed to update check '{job_name}': {e}"
            ) from e

    def create_check(
        self,
        job_name: str,
        target_url: str,
        frequency_ms: int = 60000,
        timeout_ms: int = 5000,
        probe_ids: Optional[list] = None,
        headers: Optional[list] = None,
    ) -> str:
        self._ensure_registered()

        if probe_ids is None:
            probe_ids = self.get_default_probe_ids()

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
                    "alertSensitivity": "none",
                    "basicMetricsOnly": True,
                    "labels": [],
                    "settings": settings,
                },
                timeout=30,
            )
            if response.status_code >= 400:
                logger.error(
                    "Check add response (%s): %s",
                    response.status_code,
                    response.text[:500],
                )
            response.raise_for_status()
            logger.info("Created check: %s", job_name)
            return job_name

        except requests.RequestException as e:
            raise GrafanaClientError(
                f"Failed to create check '{job_name}': {e}"
            ) from e
