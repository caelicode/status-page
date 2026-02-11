# How-To Guide

Practical step-by-step instructions for common operations. For architecture and setup details, see [README.md](README.md).

---

## Table of Contents

- [Overview: How the GitOps flow works](#how-the-gitops-flow-works)
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
- [Incidents: Change the update quiet period](#change-the-incident-update-quiet-period)
- [Workflows: Trigger reconciliation manually](#trigger-reconciliation-manually)
- [Workflows: Provision infrastructure](#provision-infrastructure)
- [Workflows: Run monitoring manually](#run-the-monitor-manually)
- [Testing: Run the test suite](#run-the-test-suite)
- [Troubleshooting](#troubleshooting)

---

## How the GitOps flow works

This system uses a declarative, push-to-deploy model:

1. **Edit** `config.yaml` — Define your endpoints, thresholds, and Statuspage settings in one unified file
2. **Push** — Commit and push to the repository
3. **Reconcile** — The Reconcile Infrastructure workflow automatically runs and:
   - Provisions Grafana synthetic monitoring checks
   - Creates Statuspage components and metrics
   - Auto-generates the `config/checks.json` and `config/statuspage.json` files
   - Commits the generated files back to the repo
4. **Monitor** — The monitoring workflow picks up the new configuration on its next run (every 5 minutes)

The end result: your entire monitoring setup is defined in a single `config.yaml` file, and infrastructure is automatically provisioned and synced across Grafana and Statuspage.

---

## Add a new endpoint to monitor

1. Edit `config.yaml` and add a new endpoint to the `endpoints` section:

```yaml
endpoints:
  my-new-service:
    name: "My New Service"
    url: "https://api.example.com/health"
    description: "Health endpoint for My New Service"
    frequency: 60000
    probes: [1, 2, 3]
    component: true
    metric: true
```

The key fields are:
- `name`: What appears on the status page
- `url`: The endpoint to monitor
- `description`: More details (optional)
- `frequency`: Probe frequency in milliseconds (e.g., 60000 = 60 seconds)
- `probes`: Grafana Synthetic Monitoring probe IDs (usually [1, 2, 3] for multi-region coverage)
- `component`: Set to `true` to create a Statuspage component
- `metric`: Set to `true` to create a latency metric on Statuspage

2. Commit and push. The Reconcile Infrastructure workflow will run automatically and:
   - Create the Grafana synthetic check
   - Create the Statuspage component (if `component: true`)
   - Create the latency metric (if `metric: true`)
   - Generate and commit `config/checks.json` and `config/statuspage.json`

3. The monitor workflow will pick up the new endpoint on its next run (every 5 minutes).

---

## Remove an endpoint

1. Remove the endpoint block from `config.yaml`.

2. Commit and push (this is a safe push that skips deletion).

3. To clean up the Grafana check and Statuspage component/metric, manually trigger the Reconcile Infrastructure workflow with deletions enabled:
   - Go to **Actions** → **Reconcile Infrastructure**
   - Click **Run workflow**
   - Toggle **Enable deletions** to `true`
   - Click **Run workflow**

This will delete the Grafana synthetic check and any associated Statuspage components/metrics.

---

## Change status thresholds

Edit the `settings.thresholds` block in `config.yaml`:

```yaml
settings:
  thresholds:
    reachability:
      operational: 95
      degraded: 75
    latency_ms:
      operational: 200
      degraded: 1000
```

These are global defaults. A component is `operational` when reachability >= 95% AND latency <= 200ms. It becomes `degraded_performance` when either drops below operational but stays above degraded thresholds. Below both, it's `major_outage`.

Commit and push. Changes apply on the next monitoring run (within 5 minutes).

---

## Override thresholds for a single check

Add a `thresholds` block to a specific endpoint in `config.yaml`:

```yaml
endpoints:
  website:
    name: "Website"
    url: "https://example.com"
    description: "Public website"
    frequency: 60000
    probes: [1, 2, 3]
    component: true
    metric: false
    thresholds:
      latency_ms:
        operational: 500
        degraded: 2000
```

Only the thresholds you specify are overridden. In this example, latency uses 500ms/2000ms while reachability inherits the global 95%/75%.

---

## Add a component to Statuspage

Set `component: true` on the endpoint in `config.yaml`:

```yaml
endpoints:
  my-new-service:
    name: "My New Service"
    url: "https://api.example.com/health"
    description: "Health endpoint"
    frequency: 60000
    probes: [1, 2, 3]
    component: true
    metric: false
```

Commit and push. The Reconcile Infrastructure workflow will automatically create the Statuspage component for you.

---

## Add a latency metric

Set `metric: true` on the endpoint in `config.yaml`:

```yaml
endpoints:
  my-new-service:
    name: "My New Service"
    url: "https://api.example.com/health"
    description: "Health endpoint"
    frequency: 60000
    probes: [1, 2, 3]
    component: true
    metric: true
```

Commit and push. The Reconcile Infrastructure workflow will automatically create the latency metric on Statuspage.

Note: Atlassian's free tier only allows 2 metrics. If you have more than 2 components, only 2 can have latency metrics.

---

## Remove a component from Statuspage

Set `component: false` on the endpoint in `config.yaml` (or remove the entire endpoint block):

```yaml
endpoints:
  my-new-service:
    name: "My New Service"
    url: "https://api.example.com/health"
    description: "Health endpoint"
    frequency: 60000
    probes: [1, 2, 3]
    component: false
    metric: false
```

Commit and push, then trigger the Reconcile Infrastructure workflow with deletions enabled to clean up the component and any associated metrics.

---

## Remove a metric without deleting the component

Set `metric: false` on the endpoint in `config.yaml`:

```yaml
endpoints:
  my-new-service:
    name: "My New Service"
    url: "https://api.example.com/health"
    description: "Health endpoint"
    frequency: 60000
    probes: [1, 2, 3]
    component: true
    metric: false
```

Commit and push. The Reconcile Infrastructure workflow will remove the metric but keep the component.

---

## Remove all components and metrics

Set all endpoints to have `component: false` and `metric: false` in `config.yaml`, then trigger the Reconcile Infrastructure workflow with deletions enabled.

Alternatively, if you want to delete everything at once:

```bash
python -m atlassian_statuspage.manage cleanup -y
```

This deletes every component and metric on your Statuspage and empties the configuration. This is a destructive operation and should be used with caution.

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

Edit the `incidents` block in `config.yaml`:

```yaml
incidents:
  auto_create: true
  auto_postmortem: false
  notify_subscribers: true
```

Set `auto_create` to `false` to disable automatic incident creation. When enabled, the sync workflow automatically creates incidents when a component degrades and resolves them when it recovers.

---

## Enable/disable auto-postmortems

In the `incidents` block in `config.yaml`:

```yaml
incidents:
  auto_create: true
  auto_postmortem: false
  notify_subscribers: true
```

Set `auto_postmortem` to `false` to stop auto-generating postmortems after incident resolution. Incidents will still be created and resolved automatically. You can write postmortems manually in the Statuspage UI.

---

## Enable/disable subscriber notifications

In the `incidents` block in `config.yaml`:

```yaml
incidents:
  auto_create: true
  auto_postmortem: false
  notify_subscribers: false
```

Set `notify_subscribers` to `false` to create and resolve incidents silently, without emailing subscribers. Useful for testing.

---

## Change the incident update quiet period

In the `incidents` block in `config.yaml`:

```yaml
incidents:
  auto_create: true
  auto_postmortem: true
  notify_subscribers: true
  quiet_period_minutes: 60
```

The `quiet_period_minutes` setting (default 60) controls how often incident updates are posted during prolonged outages. When a component stays degraded or down, duplicate updates are suppressed during the quiet period. After it elapses, a heartbeat update is posted to reassure subscribers the team is still working on it.

Escalations (e.g. degraded → major outage) always post immediately regardless of the quiet period. Set to `0` to disable suppression entirely (every sync run posts an update).

---

## Trigger reconciliation manually

The Reconcile Infrastructure workflow normally runs automatically after every push to main. To trigger it manually or with deletions enabled:

1. Go to **Actions** → **Reconcile Infrastructure**
2. Click **Run workflow** (top right)
3. Optionally toggle **Enable deletions** to `true` to allow deletion of components and metrics
4. Click the green **Run workflow** button

When deletions are disabled (the default), only additions and updates are applied. This provides safety against accidental deletions. Enable deletions only when you intentionally want to remove infrastructure.

The workflow will:
- Read `config.yaml`
- Provision Grafana synthetic checks for all endpoints
- Create/update Statuspage components and metrics
- Auto-generate `config/checks.json` and `config/statuspage.json`
- Commit the generated files back to the repo

---

## Provision Infrastructure

To manually trigger infrastructure provisioning without running the full reconciliation workflow:

1. Go to **Actions** → **Provision Infrastructure**
2. Click **Run workflow** (top right)
3. Click the green **Run workflow** button

This workflow provisions Grafana synthetic checks and Statuspage components/metrics based on the current `config.yaml`. It's useful if you want to provision infrastructure without triggering a full reconciliation cycle.

---

## Run the monitor manually

The monitor runs every 5 minutes via cron. To trigger it on demand:

1. Go to **Actions** → **Monitor & Deploy Status Page** → **Run workflow**
2. Click **Run workflow** (leave all inputs blank)

This queries Grafana for fresh metrics, updates `status.json`, and deploys the status page.

---

## Run the test suite

```bash
pip install -r requirements.txt
pytest tests/ -v
```

All 216 tests should pass. Tests use mocks and don't require any API keys or network access.

---

## Troubleshooting

**"STATUSPAGE_API_KEY environment variable is required"**
You need to set the environment variable before running any Statuspage command locally. Either export it (`export STATUSPAGE_API_KEY=your-key`) or prepend it to the command (`STATUSPAGE_API_KEY=your-key python -m ...`).

**"No component mappings in config/statuspage.json"**
This can happen if the reconcile workflow hasn't run yet. Push a change to trigger reconciliation, or manually run the Reconcile Infrastructure workflow from the Actions tab.

**"No status data for 'X' — skipping"**
The component name in `config/statuspage.json` doesn't match any endpoint name in `config.yaml`. The endpoint keys and corresponding component names must be consistent.

**Metric creation fails**
Atlassian occasionally disables metric creation on free-tier pages. Check [metastatuspage.com](https://metastatuspage.com) for known issues. The free tier limit is 2 metrics.

**GitHub Actions workflow disabled**
GitHub disables scheduled workflows after 60 days of no repo activity. Go to the Actions tab, find the disabled workflow, and click "Enable workflow".

**Status stuck on major_outage**
If Prometheus returns no data for a check (the job label doesn't exist yet, or the check was just created), the status defaults to `major_outage` as a safety measure. Verify the check exists in Grafana and has been running long enough to produce data.

**Reconcile workflow fails**
Check the GitHub Actions logs for errors. Common issues:
- Missing Grafana SM credentials — the reconcile workflow needs `GRAFANA_SM_TOKEN`, `GRAFANA_STACK_ID`, `GRAFANA_METRICS_INSTANCE_ID`, and `GRAFANA_LOGS_INSTANCE_ID` as repository secrets. See the README "Get your Grafana Cloud credentials" section for where to find each value.
- `GRAFANA_SM_URL` wrong or missing — defaults to `https://synthetic-monitoring-api-us-east-0.grafana.net`. Only set this secret if your Synthetic Monitoring backend address is different (check Grafana → Synthetic Monitoring → Config → Backend address).
- Missing Statuspage API key — add `STATUSPAGE_API_KEY` to repository secrets.
- Malformed `config.yaml` — validate YAML syntax before pushing.
- Invalid probe IDs in the `probes` list.
- Statuspage rate limiting — wait a few minutes and retry.
- Branch protection blocking push — the reconcile workflow uses a GitHub App token (`APP_ID` + `APP_PRIVATE_KEY`) to push generated configs directly to `main`. The app must be listed as a bypass actor in the branch protection rules (Settings → Branches → Edit → "Allow specified actors to bypass required pull requests" → add the app name). If the `APP_ID` or `APP_PRIVATE_KEY` secrets are missing or incorrect, the token generation step will fail.
