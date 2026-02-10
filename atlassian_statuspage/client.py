import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.statuspage.io/v1"


class StatuspageError(Exception):
    pass


class StatuspageClient:

    VALID_COMPONENT_STATUSES = {
        "operational",
        "degraded_performance",
        "partial_outage",
        "major_outage",
        "under_maintenance",
    }

    VALID_INCIDENT_STATUSES = {"investigating", "identified", "monitoring", "resolved"}

    VALID_IMPACTS = {"none", "minor", "major", "critical"}

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

    def create_component(
        self,
        name: str,
        description: str = "",
        status: str = "operational",
        group_id: Optional[str] = None,
        showcase: bool = True,
        only_show_if_degraded: bool = False,
    ) -> dict:
        if status not in self.VALID_COMPONENT_STATUSES:
            raise StatuspageError(
                f"Invalid status '{status}'. Must be one of: {', '.join(sorted(self.VALID_COMPONENT_STATUSES))}"
            )

        payload = {
            "component": {
                "name": name,
                "description": description,
                "status": status,
                "showcase": showcase,
                "only_show_if_degraded": only_show_if_degraded,
            }
        }
        if group_id:
            payload["component"]["group_id"] = group_id

        try:
            response = self._session.post(
                self._url("components"),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to create component '{name}': {e}") from e

    def update_component_status(self, component_id: str, status: str) -> dict:
        if status not in self.VALID_COMPONENT_STATUSES:
            raise StatuspageError(
                f"Invalid status '{status}'. Must be one of: {', '.join(sorted(self.VALID_COMPONENT_STATUSES))}"
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

    def delete_component(self, component_id: str) -> None:
        try:
            response = self._session.delete(
                self._url(f"components/{component_id}"),
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to delete component {component_id}: {e}"
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

    def delete_metric(self, metric_id: str) -> None:
        try:
            response = self._session.delete(
                self._url(f"metrics/{metric_id}"),
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to delete metric {metric_id}: {e}"
            ) from e

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

    def list_unresolved_incidents(self) -> list:
        try:
            response = self._session.get(
                self._url("incidents/unresolved"),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to list unresolved incidents: {e}") from e

    def list_incidents(self) -> list:
        try:
            response = self._session.get(
                self._url("incidents"),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to list incidents: {e}") from e

    def create_incident(
        self,
        name: str,
        status: str = "investigating",
        body: str = "",
        component_ids: Optional[list] = None,
        components: Optional[dict] = None,
        deliver_notifications: bool = True,
        impact_override: Optional[str] = None,
    ) -> dict:
        if status not in self.VALID_INCIDENT_STATUSES:
            raise StatuspageError(
                f"Invalid incident status '{status}'. "
                f"Must be one of: {', '.join(sorted(self.VALID_INCIDENT_STATUSES))}"
            )

        if impact_override and impact_override not in self.VALID_IMPACTS:
            raise StatuspageError(
                f"Invalid impact '{impact_override}'. "
                f"Must be one of: {', '.join(sorted(self.VALID_IMPACTS))}"
            )

        payload = {
            "incident": {
                "name": name,
                "status": status,
                "body": body,
                "deliver_notifications": deliver_notifications,
            }
        }
        if component_ids:
            payload["incident"]["component_ids"] = component_ids
        if components:
            payload["incident"]["components"] = components
        if impact_override:
            payload["incident"]["impact_override"] = impact_override

        try:
            response = self._session.post(
                self._url("incidents"),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(f"Failed to create incident '{name}': {e}") from e

    def update_incident(
        self,
        incident_id: str,
        status: Optional[str] = None,
        body: Optional[str] = None,
        component_ids: Optional[list] = None,
        components: Optional[dict] = None,
        deliver_notifications: bool = True,
    ) -> dict:
        if status and status not in self.VALID_INCIDENT_STATUSES:
            raise StatuspageError(
                f"Invalid incident status '{status}'. "
                f"Must be one of: {', '.join(sorted(self.VALID_INCIDENT_STATUSES))}"
            )

        payload: dict = {"incident": {}}
        if status:
            payload["incident"]["status"] = status
        if body is not None:
            payload["incident"]["body"] = body
        payload["incident"]["deliver_notifications"] = deliver_notifications
        if component_ids:
            payload["incident"]["component_ids"] = component_ids
        if components:
            payload["incident"]["components"] = components

        try:
            response = self._session.patch(
                self._url(f"incidents/{incident_id}"),
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to update incident {incident_id}: {e}"
            ) from e

    def resolve_incident(
        self,
        incident_id: str,
        body: str = "This incident has been resolved.",
        components: Optional[dict] = None,
        deliver_notifications: bool = True,
    ) -> dict:
        return self.update_incident(
            incident_id=incident_id,
            status="resolved",
            body=body,
            components=components,
            deliver_notifications=deliver_notifications,
        )

    def delete_incident(self, incident_id: str) -> None:
        try:
            response = self._session.delete(
                self._url(f"incidents/{incident_id}"),
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to delete incident {incident_id}: {e}"
            ) from e

    def create_postmortem(
        self,
        incident_id: str,
        body: str,
        notify_subscribers: bool = True,
        notify_twitter: bool = False,
    ) -> dict:
        try:
            response = self._session.put(
                self._url(f"incidents/{incident_id}/postmortem"),
                json={
                    "postmortem": {
                        "body": body,
                        "notify_subscribers": notify_subscribers,
                        "notify_twitter": notify_twitter,
                    }
                },
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise StatuspageError(
                f"Failed to create postmortem for incident {incident_id}: {e}"
            ) from e
