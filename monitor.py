import json
import os
import requests
from datetime import datetime, timezone, timedelta

CONFIG_FILE = "config.json"

EMAIL_TO = os.getenv("EMAIL_TO")
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


def main():
    config = load_config()
    forecasts = []
    forecasts.extend(fetch_yr_forecast(config["latitude"], config["longitude"]))

    alerts = check_limits(config, forecasts)
    print_alerts(config, alerts)

    if alerts:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
