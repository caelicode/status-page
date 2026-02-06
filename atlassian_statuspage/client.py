import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.statuspage.io/v1"


class StatuspageError(Exception):
    pass


class StatuspageClient:

    def __init__(self, api_key: str, page_id: str):
        self.api_key = api_key
        self.page_id = page_id
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"OAuth {api_key}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{BASE_URL}/pages/{self.page_id}/{path}"

    def list_components(self) -> list:
        try:
            response = self._session.get(self._url("components"), timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to list components: {e}") from e

    def update_component_status(self, component_id: str, status: str) -> dict:
        valid = {
            "operational",
            "degraded_performance",
            "partial_outage",
            "major_outage",
            "under_maintenance",
        }
        if status not in valid:
            raise StatuspageError(
                f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid))}"
            )

        try:
            response = self._session.patch(
                self._url(f"components/{component_id}"),
                json={"component": {"status": status}},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to update component {component_id}: {e}"
            ) from e

    def list_metrics(self) -> list:
        try:
            response = self._session.get(self._url("metrics"), timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to list metrics: {e}") from e

    def create_metric(
        self,
        name: str,
        suffix: str = "ms",
        tooltip: str = "",
        y_axis_min: float = 0,
        decimal_places: int = 0,
    ) -> dict:
        try:
            response = self._session.post(
                self._url("metrics"),
                json={
                    "metric": {
                        "name": name,
                        "display": True,
                        "suffix": suffix,
                        "y_axis_min": y_axis_min,
                        "decimal_places": decimal_places,
                        "tooltip_description": tooltip or name,
                    }
                },
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to create metric '{name}': {e}") from e

    def submit_metric_data(
        self, metric_id: str, value: float, timestamp: Optional[int] = None
    ) -> dict:
        if timestamp is None:
            timestamp = int(time.time())

        try:
            response = self._session.post(
                self._url(f"metrics/{metric_id}/data.json"),
                json={"data": {"timestamp": timestamp, "value": value}},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to submit data for metric {metric_id}: {e}"
            ) from e
