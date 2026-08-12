import json
import os
import requests
from html import escape
from datetime import datetime, timezone, timedelta

CONFIG_FILE = "config.json"

EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_FROM = os.getenv(
    "EMAIL_FROM",
    "Mooring Weather Watch <weather@alerts.enjoyneering.dk>",
)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
SEND_TEST = os.getenv("SEND_TEST", "false").lower() == "true"
USER_AGENT = os.getenv("USER_AGENT", "mooring-weather-watch/1.0 contact@example.com")


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def nearest_direction_sector(direction):
    sectors = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330]
    return min(sectors, key=lambda x: abs((direction - x + 180) % 360 - 180))


def fetch_yr_forecast(lat, lon):
    url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    results = []
    for item in data["properties"]["timeseries"]:
        details = item["data"]["instant"]["details"]
        if "wind_speed" not in details or "wind_from_direction" not in details:
            continue

        results.append({
            "source": "YR/MET Norway",
            "time": item["time"],
            "wind_speed": float(details["wind_speed"]),
            "wind_direction": float(details["wind_from_direction"])
        })

    return results


def check_limits(config, forecasts):
    now = datetime.now(timezone.utc)
    alert_hours = config["alert_hours"]
    limits = {int(k): v for k, v in config["limits"].items()}

    alerts = []

    for fc in forecasts:
        fc_time = datetime.fromisoformat(fc["time"].replace("Z", "+00:00"))
        hours_ahead = round((fc_time - now).total_seconds() / 3600)

        if hours_ahead not in alert_hours:
            continue

        sector = nearest_direction_sector(fc["wind_direction"])
        limit = limits[sector]

        if fc["wind_speed"] > limit:
            alerts.append({
                "source": fc["source"],
                "time": fc["time"],
                "hours_ahead": hours_ahead,
                "wind_speed": fc["wind_speed"],
                "wind_direction": fc["wind_direction"],
                "sector": sector,
                "limit": limit
            })

    return alerts


def print_alerts(config, alerts):
    if not alerts:
        print("No wind limit exceedance found.")
        return

    print("MOORING WIND ALERT")
    print(f"Location: {config['location_name']}")
    print("")

    for a in alerts:
        print(
            f"{a['hours_ahead']}h warning | {a['source']} | "
            f"{a['time']} | Wind {a['wind_speed']} m/s from {a['wind_direction']}° | "
            f"Sector {a['sector']}° limit {a['limit']} m/s"
        )


def build_alert_email(config, alerts):
    subject = f"Mooring wind alert – {config['location_name']}"
    lines = [
        "MOORING WIND ALERT",
        f"Location: {config['location_name']}",
        "",
    ]

    rows = []
    for a in alerts:
        direction = f"{a['wind_direction']:.0f}°"
        lines.append(
            f"{a['hours_ahead']}h warning | {a['time']} | "
            f"{a['wind_speed']:.1f} m/s from {direction} | "
            f"Sector {a['sector']}° limit {a['limit']:.1f} m/s"
        )
        rows.append(
            "<tr>"
            f"<td>{a['hours_ahead']} h</td>"
            f"<td>{escape(a['time'])}</td>"
            f"<td>{a['wind_speed']:.1f} m/s</td>"
            f"<td>{direction}</td>"
            f"<td>{a['sector']}°</td>"
            f"<td>{a['limit']:.1f} m/s</td>"
            "</tr>"
        )

    html = f"""
    <h2>Mooring Wind Alert</h2>
    <p><strong>Location:</strong> {escape(config['location_name'])}</p>
    <table style="border-collapse:collapse" border="1" cellpadding="6">
      <thead>
        <tr><th>Warning</th><th>Forecast time (UTC)</th><th>Wind</th><th>From</th><th>Sector</th><th>Limit</th></tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p>Forecast source: YR / MET Norway.</p>
    """
    return subject, "\n".join(lines), html


def send_email(subject, text_body, html_body):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")
    if not EMAIL_TO:
        raise RuntimeError("EMAIL_TO is not configured.")

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": EMAIL_FROM,
            "to": [EMAIL_TO],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def send_test_email(config):
    subject = f"Mooring Weather Watch test – {config['location_name']}"
    text_body = (
        "Test successful. Mooring Weather Watch can send email notifications.\n"
        f"Location: {config['location_name']}"
    )
    html_body = (
        "<h2>Test successful</h2>"
        "<p>Mooring Weather Watch can send email notifications.</p>"
        f"<p><strong>Location:</strong> {escape(config['location_name'])}</p>"
    )
    send_email(subject, text_body, html_body)
    print(f"Test email sent to {EMAIL_TO}.")


def main():
    config = load_config()

    if SEND_TEST:
        send_test_email(config)
        return

    forecasts = []
    forecasts.extend(fetch_yr_forecast(config["latitude"], config["longitude"]))

    alerts = check_limits(config, forecasts)
    print_alerts(config, alerts)

    if alerts:
        subject, text_body, html_body = build_alert_email(config, alerts)
        send_email(subject, text_body, html_body)
        print(f"Alert email sent to {EMAIL_TO}.")


if __name__ == "__main__":
    main()
