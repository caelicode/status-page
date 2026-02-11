#!/usr/bin/env python3

import logging
from datetime import datetime, timezone
from typing import Optional

from atlassian_statuspage.client import StatuspageClient, StatuspageError

logger = logging.getLogger(__name__)

COMPONENT_STATUS_MAP = {
    "operational": "operational",
    "degraded_performance": "degraded_performance",
    "major_outage": "major_outage",
}

STATUS_TO_IMPACT = {
    "degraded_performance": "minor",
    "major_outage": "critical",
}

STATUS_DISPLAY = {
    "degraded_performance": "degraded performance",
    "major_outage": "a major outage",
}


def find_open_incident_for_component(
    unresolved_incidents: list, component_id: str
) -> Optional[dict]:
    for incident in unresolved_incidents:
        affected_ids = []
        for comp in incident.get("components", []):
            affected_ids.append(comp.get("id", ""))
        if component_id in affected_ids:
            return incident
    return None


def generate_incident_name(component_name: str, status: str) -> str:
    description = STATUS_DISPLAY.get(status, "issues")
    return f"{component_name} experiencing {description}"


def generate_incident_body(component_name: str, status: str) -> str:
    if status == "major_outage":
        return (
            f"We're aware of a major outage affecting **{component_name}**. "
            f"Our team is actively investigating and working to restore service "
            f"as quickly as possible."
        )
    if status == "degraded_performance":
        return (
            f"We're investigating reports of degraded performance affecting "
            f"**{component_name}**. Some users may experience slower response times "
            f"or intermittent errors. We'll provide updates as we learn more."
        )
    return (
        f"We're investigating an issue affecting **{component_name}**. "
        f"We'll provide updates as we learn more."
    )


def generate_update_body(
    component_name: str, status: str, escalated: bool = False
) -> str:
    if escalated:
        return (
            f"The situation affecting **{component_name}** has escalated to a major "
            f"service disruption. Our team is working urgently to restore normal operation."
        )
    if status == "major_outage":
        return (
            f"Our team continues to work on restoring **{component_name}**. "
            f"The service remains unavailable. We'll provide another update shortly."
        )
    if status == "degraded_performance":
        return (
            f"We've identified the issue affecting **{component_name}** and are "
            f"working on a fix. The service continues to operate with reduced performance."
        )
    return (
        f"We continue to monitor the issue affecting **{component_name}**. "
        f"Another update will follow shortly."
    )


def generate_heartbeat_body(component_name: str, status: str) -> str:
    if status == "major_outage":
        return (
            f"We continue to monitor the situation affecting **{component_name}**. "
            f"The service remains unavailable. Our team is actively working on a resolution."
        )
    if status == "degraded_performance":
        return (
            f"We continue to monitor the situation affecting **{component_name}**. "
            f"The service remains operating with reduced performance. "
            f"Our team is actively working on a resolution."
        )
    return (
        f"We continue to monitor the situation affecting **{component_name}**. "
        f"Our team is actively working on a resolution."
    )


def get_last_update_time(incident: dict) -> Optional[datetime]:
    updates = incident.get("incident_updates", [])
    if not updates:
        return None
    last_update = updates[0]
    timestamp_str = last_update.get("created_at", "")
    if not isinstance(timestamp_str, str) or not timestamp_str:
        return None
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def generate_resolve_body(component_name: str) -> str:
    return (
        f"This incident has been resolved. **{component_name}** is back to normal "
        f"operation. Thank you for your patience."
    )


def generate_postmortem(
    incident: dict, component_name: str, resolve_time: str
) -> str:
    created = incident.get("created_at", "unknown")
    if isinstance(created, str) and "T" in created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            created = created_dt.strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            pass

    name = incident.get("name", "Incident")
    impact = incident.get("impact", "unknown")

    updates = incident.get("incident_updates", [])
    timeline_lines = []
    for update in reversed(updates):
        update_status = update.get("status", "update")
        update_body = update.get("body", "")
        update_time = update.get("created_at", "")
        if isinstance(update_time, str) and "T" in update_time:
            try:
                ut = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
                update_time = ut.strftime("%Y-%m-%d %H:%M UTC")
            except (ValueError, TypeError):
                pass
        timeline_lines.append(f"- **{update_time}** [{update_status}]: {update_body}")

    timeline = "\n".join(timeline_lines) if timeline_lines else "- No detailed updates recorded."

    return f"""## Postmortem: {name}

### Summary

**Component:** {component_name}
**Impact:** {impact}
**Started:** {created}
**Resolved:** {resolve_time}

This incident was automatically detected by our monitoring system and resolved when services returned to normal operation.

### Timeline

{timeline}

### Root Cause

This incident was detected via automated monitoring. The root cause was identified as a service degradation that was automatically resolved. If further investigation is needed, a manual follow-up will be added.

### Resolution

The service returned to normal operation and was automatically marked as resolved by our monitoring system.

### Preventive Measures

- Continuous automated monitoring remains active
- Alerting thresholds are reviewed periodically
- This postmortem was auto-generated and may be updated with additional details

---
*This postmortem was automatically generated by the status page monitoring system.*
"""


def process_incidents(
    client: StatuspageClient,
    component_mapping: dict,
    status_report: dict,
    auto_incidents: bool = True,
    auto_postmortem: bool = True,
    notify_subscribers: bool = True,
    quiet_period_minutes: int = 60,
) -> dict:
    result = {"created": [], "updated": [], "resolved": [], "suppressed": [], "errors": []}

    if not auto_incidents:
        logger.info("Incident automation is disabled — skipping")
        return result

    try:
        unresolved = client.list_unresolved_incidents()
    except StatuspageError as e:
        logger.error("Failed to fetch unresolved incidents: %s", e)
        result["errors"].append(f"Failed to fetch unresolved incidents: {e}")
        return result

    logger.info("Found %d unresolved incidents on Statuspage", len(unresolved))

    components_data = {
        comp["name"]: comp for comp in status_report.get("components", [])
    }

    for job_label, mapping in component_mapping.items():
        component_id = mapping.get("component_id")
        component_name = mapping.get("name", job_label)

        if not component_id:
            continue

        comp_data = components_data.get(component_name)
        if not comp_data:
            logger.debug("No status data for '%s' — skipping incident check", component_name)
            continue

        current_status = COMPONENT_STATUS_MAP.get(
            comp_data["status"], "major_outage"
        )
        is_healthy = current_status == "operational"

        open_incident = find_open_incident_for_component(unresolved, component_id)

        if not is_healthy and open_incident is None:
            incident_name = generate_incident_name(component_name, current_status)
            incident_body = generate_incident_body(component_name, current_status)
            impact = STATUS_TO_IMPACT.get(current_status, "minor")

            try:
                new_incident = client.create_incident(
                    name=incident_name,
                    status="investigating",
                    body=incident_body,
                    component_ids=[component_id],
                    components={component_id: current_status},
                    deliver_notifications=notify_subscribers,
                    impact_override=impact,
                )
                incident_id = new_incident.get("id", "unknown")
                logger.info(
                    "Created incident '%s' (ID: %s) for %s",
                    incident_name, incident_id, component_name,
                )
                result["created"].append({
                    "component": component_name,
                    "incident_id": incident_id,
                    "status": current_status,
                })
            except StatuspageError as e:
                logger.error("Failed to create incident for %s: %s", component_name, e)
                result["errors"].append(f"Create incident for {component_name}: {e}")

        elif not is_healthy and open_incident is not None:
            incident_id = open_incident["id"]
            new_impact = STATUS_TO_IMPACT.get(current_status, "minor")
            old_impact = open_incident.get("impact", "minor")
            impact_escalated = new_impact != old_impact

            if not impact_escalated and quiet_period_minutes > 0:
                last_update_time = get_last_update_time(open_incident)
                if last_update_time is not None:
                    elapsed = (datetime.now(timezone.utc) - last_update_time).total_seconds() / 60
                    if elapsed < quiet_period_minutes:
                        logger.info(
                            "Suppressed duplicate update for incident %s (%s) — "
                            "%.0f min since last update, quiet period is %d min",
                            incident_id, component_name, elapsed, quiet_period_minutes,
                        )
                        result["suppressed"].append({
                            "component": component_name,
                            "incident_id": incident_id,
                        })
                        continue

            if impact_escalated:
                update_body = generate_update_body(
                    component_name, current_status, escalated=True
                )
            else:
                update_body = generate_heartbeat_body(component_name, current_status)

            new_name = generate_incident_name(component_name, current_status)

            try:
                client.update_incident(
                    incident_id=incident_id,
                    status="identified",
                    body=update_body,
                    components={component_id: current_status},
                    deliver_notifications=impact_escalated and notify_subscribers,
                    impact_override=new_impact,
                    name=new_name,
                )
                if impact_escalated:
                    logger.info(
                        "Escalated incident %s for %s from %s to %s",
                        incident_id, component_name, old_impact, new_impact,
                    )
                else:
                    logger.info(
                        "Posted heartbeat for incident %s (%s)",
                        incident_id, component_name,
                    )
                result["updated"].append({
                    "component": component_name,
                    "incident_id": incident_id,
                    "status": current_status,
                    "escalated": impact_escalated,
                })
            except StatuspageError as e:
                logger.error("Failed to update incident %s: %s", incident_id, e)
                result["errors"].append(f"Update incident {incident_id}: {e}")

        elif is_healthy and open_incident is not None:
            incident_id = open_incident["id"]
            resolve_body = generate_resolve_body(component_name)
            resolve_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            try:
                client.resolve_incident(
                    incident_id=incident_id,
                    body=resolve_body,
                    components={component_id: "operational"},
                    deliver_notifications=notify_subscribers,
                )
                logger.info(
                    "Resolved incident %s for %s",
                    incident_id, component_name,
                )
                result["resolved"].append({
                    "component": component_name,
                    "incident_id": incident_id,
                })
            except StatuspageError as e:
                logger.error("Failed to resolve incident %s: %s", incident_id, e)
                result["errors"].append(f"Resolve incident {incident_id}: {e}")
                continue

            if auto_postmortem:
                try:
                    incidents = client.list_incidents()
                    full_incident = None
                    for inc in incidents:
                        if inc.get("id") == incident_id:
                            full_incident = inc
                            break

                    if full_incident is None:
                        full_incident = open_incident

                    postmortem_body = generate_postmortem(
                        full_incident, component_name, resolve_time
                    )
                    client.create_postmortem(
                        incident_id=incident_id,
                        body=postmortem_body,
                        notify_subscribers=notify_subscribers,
                        notify_twitter=False,
                    )
                    logger.info(
                        "Published postmortem for incident %s (%s)",
                        incident_id, component_name,
                    )
                except StatuspageError as e:
                    logger.warning(
                        "Failed to create postmortem for incident %s: %s "
                        "(incident was resolved successfully)",
                        incident_id, e,
                    )
                    result["errors"].append(
                        f"Postmortem for incident {incident_id}: {e}"
                    )

    return result
