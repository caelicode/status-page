# Status Page

Automated service status page powered by Grafana Cloud (free tier), GitHub Actions, and GitHub Pages — with optional Atlassian Statuspage integration. Zero hosting cost.

**Live status pages:** [GitHub Pages](https://caelicode.github.io/status-page/) · [Atlassian Statuspage](https://caelicode.statuspage.io/)

## How it works

```
Grafana Synthetic Monitoring        GitHub Actions (every 5 min)       GitHub Pages
 probes your endpoints        ──►   queries Prometheus metrics    ──►  serves static
 stores metrics in Prometheus       determines status                  status page
                                    deploys to Pages
                                         │
                                         └──►  Atlassian Statuspage (optional)
                                               syncs component status + metrics
```

1. **Grafana Cloud** runs synthetic HTTP checks against your endpoints from multiple geographic locations and stores the results as Prometheus metrics.
2. **GitHub Actions** runs `monitor.py` every 5 minutes. It queries Grafana's Prometheus API for reachability and latency, determines each component's status against configurable thresholds, and writes `github-pages/status.json`.
3. **GitHub Pages** serves a static HTML page that reads `status.json` and renders the current status. It auto-refreshes every 60 seconds in the browser.
4. **Atlassian Statuspage** *(optional)* — an independent workflow reads the generated `status.json` and syncs component statuses and latency metrics to your Statuspage via their REST API.

No commit spam — the workflow deploys directly via the Pages API without committing status updates to the repo.

## Prerequisites

1. A [Grafana Cloud](https://grafana.com/auth/sign-up/create-user?pg=pricing&plcmt=free-tier) free account (includes Prometheus, synthetic monitoring, and dashboards).
2. A GitHub repository (public for unlimited Actions minutes, or private with 2,000 free min/month).
3. GitHub Pages enabled on the repository (Settings → Pages → Source: GitHub Actions).

## Setup

### 1. Create a Grafana Cloud account

Sign up at [grafana.com](https://grafana.com/auth/sign-up/create-user?pg=pricing&plcmt=free-tier). The free tier includes 10k Prometheus series, 50 GB logs, and synthetic monitoring.

### 2. Get your Grafana Cloud credentials

From your Grafana Cloud portal:

- **Prometheus URL**: Your Grafana Cloud → Prometheus → Details → Remote Write/Query Endpoint (the `/api/prom/api/v1/query` URL)
- **User ID / Instance ID**: Shown on the Prometheus details page
- **API Key**: Grafana Cloud → API Keys → Create with `MetricsPublisher` role

For provisioning (optional, you can create checks in the UI instead):

- **Synthetic Monitoring Token**: Grafana Cloud → Synthetic Monitoring → Config → Generate API token
- **Stack ID, Metrics Instance ID, Logs Instance ID**: Grafana Cloud → your stack details

### 3. Add GitHub secrets

Go to your repo → Settings → Secrets and variables → Actions, and add:

| Secret | Description |
|--------|-------------|
| `GRAFANA_PROMETHEUS_URL` | Prometheus query endpoint |
| `GRAFANA_PROMETHEUS_USER_ID` | Prometheus instance/user ID |
| `GRAFANA_API_KEY` | Grafana Cloud API key |
| `GRAFANA_SM_TOKEN` | *(setup only)* Synthetic monitoring token |
| `GRAFANA_STACK_ID` | *(setup only)* Stack ID |
| `GRAFANA_METRICS_INSTANCE_ID` | *(setup only)* Metrics instance ID |
| `GRAFANA_LOGS_INSTANCE_ID` | *(setup only)* Logs instance ID |

### 4. Define your checks

Edit `config/checks.json`:

```json
{
  "settings": {
    "reachability_query_window": "15m",
    "latency_query_window": "5m",
    "thresholds": {
      "reachability": { "operational": 95, "degraded": 75 },
      "latency_ms": { "operational": 200, "degraded": 1000 }
    }
  },
  "checks": [
    {
      "name": "My API",
      "job_label": "my-api",
      "url": "https://api.example.com/health",
      "description": "Primary API health endpoint"
    }
  ]
}
```

### 5. Provision checks (optional)

If you want to create synthetic monitoring checks programmatically rather than through the Grafana UI, run the **Provision Grafana Checks** workflow manually from the Actions tab.

### 6. Enable GitHub Pages

Go to Settings → Pages → Build and deployment → Source: **GitHub Actions**.

### 7. Done

The monitor workflow runs every 5 minutes automatically. Your status page will be live at `https://<username>.github.io/<repo-name>/`.

## Project structure

```
├── monitoring/                  Python monitoring package
│   ├── config.py                Config loading (env vars + checks.json)
│   ├── grafana_client.py        Grafana Cloud API client
│   └── status_engine.py         Status determination logic
├── atlassian_statuspage/        Atlassian Statuspage integration
│   ├── client.py                Statuspage REST API client
│   └── sync.py                  Reads status.json → syncs to Statuspage
├── setup/
│   └── provision.py             One-time Grafana check provisioning
├── github-pages/                Static site (deployed to GitHub Pages)
│   ├── index.html               Status page UI
│   └── status.json              Auto-generated by monitor
├── config/
│   ├── checks.json              Check definitions and thresholds
│   └── statuspage.json          Statuspage component/metric ID mappings
├── tests/
│   ├── test_status_engine.py    Monitoring unit tests
│   └── test_statuspage.py       Statuspage integration tests
├── .github/workflows/
│   ├── monitor.yml              Cron monitor + Pages deployment
│   ├── statuspage.yml           Cron sync to Atlassian Statuspage
│   └── setup.yml                One-time provisioning
├── monitor.py                   Main entry point
├── requirements.txt
└── README.md
```

## Debouncing strategy

Rather than tracking consecutive failures (which needs persistent state between ephemeral GitHub runners), the system uses **wide Prometheus query windows**. The default 15-minute reachability window means a single failed probe among ~15 readings won't flip the status — the average naturally smooths transient blips.

## Status thresholds

| Metric | Operational | Degraded | Major Outage |
|--------|-------------|----------|--------------|
| Reachability | >= 95% | >= 75% | < 75% |
| Latency | <= 200ms | <= 1000ms | > 1000ms |

The worst of reachability and latency determines the component status. The worst component determines overall status.

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Atlassian Statuspage integration (optional)

An independent workflow syncs your monitoring data to an [Atlassian Statuspage](https://www.atlassian.com/software/statuspage) (free tier: 25 components, 100 subscribers, 2 metrics).

### 1. Create a Statuspage account

Sign up at [statuspage.io](https://www.atlassian.com/software/statuspage). Create a page and add your components.

### 2. Get your Statuspage credentials

From your Statuspage management dashboard:

- **API Key**: Click your avatar (bottom left) → API info
- **Page ID**: Visible in the URL when managing your page (`manage.statuspage.io/pages/<page_id>`)
- **Component IDs**: Listed in the URL when editing a component, or via `GET https://api.statuspage.io/v1/pages/<page_id>/components`

### 3. Add the GitHub secret

| Secret | Description |
|--------|-------------|
| `STATUSPAGE_API_KEY` | Your Statuspage API key |

### 4. Configure component mappings

Edit `config/statuspage.json` to map your Grafana check job labels to Statuspage component and metric IDs:

```json
{
  "page_id": "your-page-id",
  "component_mapping": {
    "example-api": {
      "name": "Example API",
      "component_id": "your-component-id",
      "metric_id": "your-metric-id-or-empty"
    }
  }
}
```

The `name` field must match the component name in `config/checks.json`. The `metric_id` is optional — leave it empty (`""`) to skip latency metric submission for that component. The free tier allows 2 metrics.

### 5. Create metrics in Statuspage (optional)

To display latency graphs on your Statuspage, create system metrics manually: Your Page → System Metrics → Add a Metric → "I'll submit my own data." Copy the Metric ID from the Advanced Options tab and add it to `config/statuspage.json`.

### 6. Enable the workflow

The `statuspage.yml` workflow runs on the same 5-minute cron as the monitor. It first runs `monitor.py` to generate fresh metrics, then syncs to Statuspage. This makes it fully independent from the GitHub Pages workflow.

## Important notes

- **60-day inactivity timeout**: GitHub disables scheduled workflows after 60 days of no repo activity. Keep the repo active or re-enable manually.
- **Public repos recommended**: GitHub Actions is free and unlimited for public repos. Private repos get 2,000 minutes/month (a 5-min cron burns ~4,300 min/month).
- **Grafana Cloud free tier limits**: 10k active series, 100k synthetic monitoring executions/month.

