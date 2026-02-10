# How-To Guide

Practical step-by-step instructions for common operations. For architecture and setup details, see [README.md](README.md).

Every `STATUSPAGE_API_KEY=...` command below assumes you have the key exported. To avoid repeating it, run `export STATUSPAGE_API_KEY=your-key` once per terminal session.

---

## Table of Contents

- [Monitoring: Add a new endpoint](#add-a-new-endpoint-to-monitor)
- [Monitoring: Remove an endpoint](#remove-an-endpoint)
- [Monitoring: Change status thresholds](#change-status-thresholds)
- [Monitoring: Override thresholds for one check](#override-thresholds-for-a-single-check)
- [Statuspage: Add a component](#add-a-component-to-statuspage)
- [Statuspage: Add a latency metric](#add-a-latency-metric)
- [Statuspage: Remove a component](#remove-a-component-from-statuspage)
- [Statuspage: Remove a metric only](#remove-a-metric-without-deleting-the-component)
- [Statuspage: Remove everything](#remove-all-components-and-metrics)
- [Statuspage: See what's on your page](#list-whats-on-your-statuspage)
- [Incidents: Enable/disable automation](#enabledisable-incident-automation)
- [Incidents: Enable/disable postmortems](#enabledisable-auto-postmortems)
- [Incidents: Enable/disable subscriber notifications](#enabledisable-subscriber-notifications)
- [Workflows: Run a management command from GitHub](#run-a-management-command-from-github-actions)
- [Workflows: Run monitoring manually](#run-the-monitor-manually)
- [Workflows: Provision Grafana checks](#provision-grafana-synthetic-checks)
- [Testing: Run the test suite](#run-the-test-suite)
- [Troubleshooting](#troubleshooting)

---

## Add a new endpoint to monitor

1. Open `config/checks.json` and add an entry to the `checks` array:

```json
{
  "name": "My New Service",
  "job_label": "my-new-service",
  "url": "https://api.example.com/health",
  "description": "Health endpoint for My New Service"
}
```

The `name` is what appears on the status page. The `job_label` must match the Grafana Synthetic Monitoring job label (lowercase, hyphens, no spaces).

2. Create the synthetic monitoring check in Grafana. Either do it in the Grafana UI, or run the provisioning workflow from the Actions tab (see [Provision Grafana synthetic checks](#provision-grafana-synthetic-checks)).

3. Commit and push. The monitor workflow will pick up the new check on its next 5-minute run.

4. If you use Statuspage, you also need to create a component for it — see [Add a component to Statuspage](#add-a-component-to-statuspage).

---

## Remove an endpoint

1. Remove the entry from the `checks` array in `config/checks.json`.

2. If the endpoint has a Statuspage component, remove it too — see [Remove a component from Statuspage](#remove-a-component-from-statuspage).

3. Optionally delete the synthetic check from the Grafana UI. The monitor will simply skip any job label it can't find metrics for.

4. Commit and push.

---

## Change status thresholds

Edit the `settings.thresholds` block in `config/checks.json`:

```json
"thresholds": {
  "reachability": { "operational": 95, "degraded": 75 },
  "latency_ms": { "operational": 200, "degraded": 1000 }
}
```

These are global defaults. A component is `operational` when reachability >= 95% AND latency <= 200ms. It becomes `degraded_performance` when either drops below operational but stays above degraded thresholds. Below both, it's `major_outage`.

Commit and push. Changes apply on the next monitoring run.

---

## Override thresholds for a single check

Add a `thresholds` block to that specific check in `config/checks.json`:

```json
{
  "name": "Website",
  "job_label": "website",
  "url": "https://example.com",
  "description": "Public website",
  "thresholds": {
    "latency_ms": { "operational": 500, "degraded": 2000 }
  }
}
```

Only the thresholds you specify are overridden. In this example, latency uses 500ms/2000ms while reachability inherits the global 95%/75%.

---

## Add a component to Statuspage

**Option A — Automatically from checks.json (recommended):**

```bash
python -m atlassian_statuspage.manage sync-components
```

This reads `config/checks.json`, creates a Statuspage component for each check that doesn't already have one, and saves the new component IDs to `config/statuspage.json`. It is safe to run multiple times — it skips anything already configured.

You can also run this from GitHub Actions (see [Run a management command from GitHub Actions](#run-a-management-command-from-github-actions)).

**Option B — Manually:**

1. Create the component in the Statuspage UI (manage.statuspage.io).
2. Copy the component ID from the URL (it appears when you click on the component).
3. Add it to `config/statuspage.json`:

```json
"component_mapping": {
  "my-new-service": {
    "name": "My New Service",
    "component_id": "paste-the-id-here",
    "metric_id": ""
  }
}
```

4. Commit and push.

---

## Add a latency metric

Components need to exist first. If you haven't created them yet, run `sync-components` first.

```bash
python -m atlassian_statuspage.manage sync-metrics
```

This creates a metric named `{Component Name} Latency` for each component that doesn't already have one, and saves the metric IDs to `config/statuspage.json`.

Note: Atlassian's free tier only allows 2 metrics. If you have more than 2 components, only 2 can have latency metrics.

You can also run this from GitHub Actions (see [Run a management command from GitHub Actions](#run-a-management-command-from-github-actions)).

---

## Remove a component from Statuspage

```bash
python -m atlassian_statuspage.manage delete-component my-new-service
```

Replace `my-new-service` with the job label from `config/checks.json`. This deletes the component and its metric (if any) from Statuspage, and removes the entry from `config/statuspage.json`.

Commit and push after running this so the config change is saved to the repo.

---

## Remove a metric without deleting the component

```bash
python -m atlassian_statuspage.manage delete-metric my-new-service
```

This deletes only the latency metric, not the component. The component will still sync status, just without latency data.

Commit and push after running this.

---

## Remove all components and metrics

```bash
python -m atlassian_statuspage.manage cleanup
```

You'll be prompted to confirm. To skip the confirmation (useful in scripts):

```bash
python -m atlassian_statuspage.manage cleanup -y
```

This deletes every component and metric listed in `config/statuspage.json` and empties the `component_mapping`.

---

## List what's on your Statuspage

```bash
python -m atlassian_statuspage.manage list-components
python -m atlassian_statuspage.manage list-metrics
python -m atlassian_statuspage.manage list-incidents
```

These query the Statuspage API directly, so they show everything on your page (not just what's in the config file).

---

## Enable/disable incident automation

Edit `config/statuspage.json`:

```json
"incidents": {
  "auto_create": true
}
```

Set `auto_create` to `false` to disable. When enabled, the sync workflow automatically creates incidents when a component degrades and resolves them when it recovers.

---

## Enable/disable auto-postmortems

```json
"incidents": {
  "auto_create": true,
  "auto_postmortem": false
}
```

Set `auto_postmortem` to `false` to stop auto-generating postmortems after incident resolution. Incidents will still be created and resolved automatically. You can write postmortems manually in the Statuspage UI.

---

## Enable/disable subscriber notifications

```json
"incidents": {
  "auto_create": true,
  "notify_subscribers": false
}
```

Set `notify_subscribers` to `false` to create and resolve incidents silently, without emailing subscribers. Useful for testing.

---

## Run a management command from GitHub Actions

If you don't have the repo cloned locally or don't want to set up the API key on your machine, you can run management commands directly from GitHub:

1. Go to your repo → **Actions** tab
2. Click **Sync to Atlassian Statuspage** in the left sidebar
3. Click **Run workflow** (top right)
4. Select a command from the dropdown: `sync-components`, `sync-metrics`, `list-components`, `list-metrics`, or `list-incidents`
5. Click the green **Run workflow** button

When `sync-components` or `sync-metrics` is selected, the workflow automatically commits the updated `config/statuspage.json` back to the repo.

Delete commands (`delete-component`, `delete-metric`, `cleanup`) require a job label argument and must be run from the CLI.

---

## Run the monitor manually

The monitor runs every 5 minutes via cron. To trigger it on demand:

1. Go to **Actions** → **Monitor & Deploy Status Page** → **Run workflow**
2. Click **Run workflow** (leave all inputs blank)

This queries Grafana for fresh metrics, updates `status.json`, and deploys the status page.

---

## Provision Grafana synthetic checks

If you defined checks in `config/checks.json` and want to create the corresponding Grafana Synthetic Monitoring checks programmatically:

1. Make sure you've set the provisioning secrets (see README: `GRAFANA_SM_TOKEN`, `GRAFANA_STACK_ID`, `GRAFANA_METRICS_INSTANCE_ID`, `GRAFANA_LOGS_INSTANCE_ID`)
2. Go to **Actions** → **Provision Grafana Checks** → **Run workflow**

This only needs to be done once per check. The Grafana UI can also be used instead.

---

## Run the test suite

```bash
pip install -r requirements.txt
pytest tests/ -v
```

All 132 tests should pass. Tests use mocks and don't require any API keys or network access.

---

## Troubleshooting

**"STATUSPAGE_API_KEY environment variable is required"**
You need to set the environment variable before running any Statuspage command. Either export it (`export STATUSPAGE_API_KEY=your-key`) or prepend it to the command (`STATUSPAGE_API_KEY=your-key python -m ...`).

**"No component mappings in config/statuspage.json"**
The sync workflow has nothing to sync because `component_mapping` is empty. Run `sync-components` first to populate it.

**"No status data for 'X' — skipping"**
The component name in `config/statuspage.json` doesn't match any component name in `config/checks.json`. The `name` fields must be identical.

**Metric creation fails**
Atlassian occasionally disables metric creation on free-tier pages. Check [metastatuspage.com](https://metastatuspage.com) for known issues. The free tier limit is 2 metrics.

**GitHub Actions workflow disabled**
GitHub disables scheduled workflows after 60 days of no repo activity. Go to the Actions tab, find the disabled workflow, and click "Enable workflow".

**Status stuck on major_outage**
If Prometheus returns no data for a check (the job label doesn't exist yet, or the check was just created), the status defaults to `major_outage` as a safety measure. Verify the check exists in Grafana and has been running long enough to produce data.
