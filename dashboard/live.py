import time
import requests
from rich.live import Live
from rich.table import Table
from rich.console import Console
from rich.panel import Panel
from rich import box
from datetime import datetime

API = "http://localhost:8000"
STORE = "STORE_BLR_002"

def fetch_metrics():
    try:
        r = requests.get(f"{API}/stores/{STORE}/metrics", timeout=3)
        return r.json().get("metrics", {})
    except:
        return None

def fetch_anomalies():
    try:
        r = requests.get(f"{API}/stores/{STORE}/anomalies", timeout=3)
        return r.json().get("anomalies", [])
    except:
        return []

def build_table(metrics, anomalies):
    table = Table(title=f"Store Intelligence — {STORE}", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    if not metrics:
        table.add_row("Status", "[red]API Unavailable[/red]")
        return table

    table.add_row("Unique Visitors", str(metrics.get("unique_visitors", 0)))
    table.add_row("Conversion Rate", f"{metrics.get('conversion_rate', 0)*100:.1f}%")
    table.add_row("Queue Depth", str(metrics.get("queue_depth", 0)))
    table.add_row("Abandonment Rate", f"{metrics.get('abandonment_rate', 0)*100:.1f}%")

    for zone, dwell in (metrics.get("avg_dwell_per_zone") or {}).items():
        table.add_row(f"Avg Dwell [{zone}]", f"{dwell/1000:.1f}s")

    if anomalies:
        table.add_section()
        for a in anomalies:
            severity = a.get("severity", "INFO")
            color = "red" if severity == "CRITICAL" else "yellow" if severity == "WARN" else "blue"
            table.add_row(
                f"[{color}]⚠ {a.get('anomaly_type')}[/{color}]",
                f"[{color}]{a.get('suggested_action', '')}[/{color}]"
            )

    table.add_section()
    table.add_row("Last Updated", datetime.now().strftime("%H:%M:%S"))
    return table

def main():
    with Live(build_table(fetch_metrics(), fetch_anomalies()), refresh_per_second=1) as live:
        while True:
            metrics = fetch_metrics()
            anomalies = fetch_anomalies()
            live.update(build_table(metrics, anomalies))
            time.sleep(5)

if __name__ == "__main__":
    main()
