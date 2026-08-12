import base64
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests

CONFIG_FILE = "config.json"
STATE_FILE = os.getenv("STATE_FILE", ".weather-state.json")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Mooring Weather Watch <weather@alerts.enjoyneering.dk>")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
SEND_TEST = os.getenv("SEND_TEST", "false").lower() == "true"
USER_AGENT = os.getenv("USER_AGENT", "mooring-weather-watch/1.0 contact@example.com")


def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as file:
        return json.load(file)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def nearest_direction_sector(direction):
    sectors = list(range(0, 360, 30))
    return min(sectors, key=lambda value: abs((direction - value + 180) % 360 - 180))


def fetch_yr_forecast(lat, lon):
    url = f"https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={lat}&lon={lon}"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    data = response.json()
    results = []
    for item in data["properties"]["timeseries"]:
        details = item["data"]["instant"]["details"]
        if "wind_speed" not in details or "wind_from_direction" not in details:
            continue
        results.append({
            "source": "YR / MET Norway", "time": item["time"],
            "wind_speed": float(details["wind_speed"]),
            "wind_gust": float(details["wind_speed_of_gust"]) if details.get("wind_speed_of_gust") is not None else None,
            "wind_direction": float(details["wind_from_direction"]),
        })
    return results


def classify_trend(delta, threshold):
    if delta > threshold:
        return "Increasing", "↑"
    if delta < -threshold:
        return "Decreasing", "↓"
    return "Stable", "→"


def assess_forecasts(config, forecasts, now=None):
    now = now or datetime.now(timezone.utc)
    limits = {int(key): float(value) for key, value in config["limits"].items()}
    advisory = float(config.get("advisory_percentage", 80))
    trend_threshold = float(config.get("trend_threshold_ms", 0.5))
    parsed = [(datetime.fromisoformat(item["time"].replace("Z", "+00:00")), item) for item in forecasts]
    assessed = []
    for horizon in sorted(config["alert_hours"]):
        target = now.timestamp() + horizon * 3600
        index, (forecast_time, forecast) = min(enumerate(parsed), key=lambda pair: abs(pair[1][0].timestamp() - target))
        if abs(forecast_time.timestamp() - target) > 3600:
            continue
        sector = nearest_direction_sector(forecast["wind_direction"])
        limit = limits[sector]
        utilisation = forecast["wind_speed"] / limit * 100
        previous_speed = parsed[index - 1][1]["wind_speed"] if index else forecast["wind_speed"]
        delta = forecast["wind_speed"] - previous_speed
        trend, arrow = classify_trend(delta, trend_threshold)
        level = "Warning" if utilisation >= 100 else "Advisory" if utilisation >= advisory else "Normal"
        assessed.append({**forecast, "hours_ahead": horizon, "sector": sector, "limit": limit,
                         "utilisation": utilisation, "margin": limit - forecast["wind_speed"],
                         "trend": trend, "trend_arrow": arrow, "trend_delta": delta, "level": level})
    return assessed


def highest_level(items):
    if any(item["level"] == "Warning" for item in items):
        return "Warning"
    if any(item["level"] == "Advisory" for item in items):
        return "Advisory"
    return "Normal"


def should_notify(state, assessed, increase_threshold=5):
    active = [item for item in assessed if item["level"] != "Normal"]
    if not active:
        return (("all_clear", []) if state.get("active") else (None, []))
    level = highest_level(active)
    maximum = max(item["utilisation"] for item in active)
    severity = {"Advisory": 1, "Warning": 2}
    if not state.get("active"):
        return "new", active
    if severity[level] > severity.get(state.get("notified_level"), 0):
        return "escalation", active
    if maximum >= float(state.get("notified_max_utilisation", 0)) + increase_threshold:
        return "increase", active
    return None, active


def update_state(state, assessed, reason):
    active = [item for item in assessed if item["level"] != "Normal"]
    if not active:
        return {"active": False, "updated_at": datetime.now(timezone.utc).isoformat()}
    updated = dict(state)
    updated.update({"active": True, "updated_at": datetime.now(timezone.utc).isoformat()})
    if reason:
        updated.update({"notified_level": highest_level(active),
                        "notified_max_utilisation": max(item["utilisation"] for item in active)})
    return updated


def local_time(iso_time, timezone_name):
    value = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%d %b %Y, %H:%M %Z")


def make_wind_rose(config, assessed):
    sectors = list(range(0, 360, 30))
    limits = [float(config["limits"][str(sector)]) for sector in sectors]
    figure, axis = plt.subplots(figsize=(7.2, 7.2), subplot_kw={"projection": "polar"})
    axis.set_theta_zero_location("N")
    axis.set_theta_direction(-1)
    axis.plot(np.deg2rad(sectors + [360]), limits + [limits[0]], color="#1769aa", linewidth=2)
    axis.fill(np.deg2rad(sectors + [360]), limits + [limits[0]], color="#1769aa", alpha=0.05)
    colors = {"Normal": "#64748b", "Advisory": "#d97706", "Warning": "#dc2626"}
    for item in assessed:
        theta, color = np.deg2rad(item["wind_direction"]), colors[item["level"]]
        axis.scatter(theta, item["wind_speed"], color=color, s=55, zorder=4)
        if item["wind_gust"] is not None:
            axis.scatter(theta, item["wind_gust"], facecolors="none", edgecolors=color, linewidths=1.5, s=65, zorder=4)
        if item["level"] != "Normal":
            axis.annotate(f"{item['hours_ahead']}h · {item['utilisation']:.0f}% {item['trend_arrow']}",
                          (theta, item["wind_speed"]), xytext=(5, 6), textcoords="offset points", fontsize=8, color=color)
    maximum = max([40.0, *limits, *[(item["wind_gust"] or item["wind_speed"]) for item in assessed]])
    axis.set_ylim(0, np.ceil(maximum / 5) * 5)
    axis.set_yticks(np.arange(5, axis.get_ylim()[1] + 0.1, 5))
    axis.set_title("Directional Wind Forecast", pad=20, fontsize=14, fontweight="bold")
    axis.grid(color="#cbd5e1", linewidth=0.7)
    axis.text(0.5, -0.10, "Filled marker: 10-min mean · Open marker: 3-sec gust",
              transform=axis.transAxes, ha="center", fontsize=9, color="#475569")
    figure.tight_layout()
    output = io.BytesIO()
    figure.savefig(output, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return output.getvalue()


def build_alert_email(config, assessed, reason, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    timezone_name = config.get("timezone", "Europe/Copenhagen")
    generated = generated_at.astimezone(ZoneInfo(timezone_name)).strftime("%d %b %Y, %H:%M %Z")
    active = [item for item in assessed if item["level"] != "Normal"]
    level = highest_level(active)
    title = "Mooring Wind Warning" if level == "Warning" else "Mooring Wind Advisory"
    subject = f"{level}: Mooring wind – {config['location_name']}"
    forecast_url = config.get("forecast_url", "https://www.yr.no/")
    lines = [title.upper(), f"Location: {config['location_name']}", f"Generated: {generated}", ""]
    rows = []
    for item in assessed:
        gust = f"{item['wind_gust']:.1f}" if item["wind_gust"] is not None else "N/A"
        forecast_time = local_time(item["time"], timezone_name)
        lines.append(f"{item['hours_ahead']}h | {forecast_time} | {item['level']} | Mean {item['wind_speed']:.1f} m/s | "
                     f"Gust {gust} m/s | {item['wind_direction']:.0f}° | {item['utilisation']:.0f}% of "
                     f"{item['limit']:.1f} m/s | {item['trend']} {item['trend_delta']:+.1f} m/s")
        color = {"Normal": "#64748b", "Advisory": "#b45309", "Warning": "#b91c1c"}[item["level"]]
        rows.append("<tr>" + f"<td>{item['hours_ahead']} h</td><td>{escape(forecast_time)}</td>"
                    f"<td style='color:{color};font-weight:700'>{item['level']}</td>"
                    f"<td>{item['wind_speed']:.1f} m/s</td><td>{gust} m/s</td><td>{item['wind_direction']:.0f}°</td>"
                    f"<td>{item['limit']:.1f} m/s</td><td><strong>{item['utilisation']:.0f}%</strong></td>"
                    f"<td>{item['margin']:+.1f} m/s</td><td>{item['trend_arrow']} {item['trend']} ({item['trend_delta']:+.1f})</td></tr>")
    notice = ("Limits are assessed against forecast 10-minute mean wind at 10 m. Gusts are displayed for awareness "
              "and are not assessed against the configured mean-wind limits.")
    disclaimer = ("Mooring Weather Watch is an automated information service provided by Enjoyneering ApS. It is provided "
                  "for operational awareness only and does not replace official forecasts, approved mooring analyses, "
                  "procedures, risk assessments, competent engineering judgement or monitoring of actual site conditions. "
                  "Forecasts and notifications may be delayed, incomplete, unavailable or change without notice. "
                  "Enjoyneering ApS accepts no liability for decisions, actions, losses or consequences arising from reliance "
                  "on this notification. Always verify current conditions and applicable operational limits before taking action.")
    html = f"""<div style="font-family:Arial,sans-serif;color:#172033;max-width:1050px">
    <h2 style="margin-bottom:6px">{title}</h2><p><strong>Location:</strong> {escape(config['location_name'])}<br>
    <strong>Alert generated:</strong> {escape(generated)}</p>
    <img src="cid:wind-rose" alt="Directional wind forecast" style="max-width:680px;width:100%;height:auto">
    <p style="background:#f1f5f9;padding:10px;border-left:4px solid #1769aa"><strong>Operational notice:</strong> {escape(notice)}</p>
    <div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:13px" border="1" cellpadding="6">
    <thead style="background:#eaf1f8"><tr><th>Horizon</th><th>Forecast time</th><th>Level</th><th>Mean</th><th>Gust</th>
    <th>From</th><th>Limit</th><th>Use</th><th>Margin</th><th>Trend</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    <p><a href="{escape(forecast_url)}" style="display:inline-block;background:#1769aa;color:white;padding:10px 16px;text-decoration:none;border-radius:4px">View full weather forecast</a></p>
    <p style="font-size:12px;color:#475569"><strong>About this service</strong><br>{escape(disclaimer)}</p>
    <p style="font-size:11px;color:#64748b">Enjoyneering ApS · Forecast source: YR / MET Norway · Generated {escape(generated)}</p></div>"""
    lines.extend(["", notice, "", forecast_url, "", disclaimer])
    return subject, "\n".join(lines), html


def build_all_clear_email(config, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc)
    timezone_name = config.get("timezone", "Europe/Copenhagen")
    generated = generated_at.astimezone(ZoneInfo(timezone_name)).strftime("%d %b %Y, %H:%M %Z")
    subject = f"All clear: Mooring wind – {config['location_name']}"
    message = f"All monitored forecast horizons are now below {config.get('advisory_percentage', 80)}% of their directional limits."
    text = f"MOORING WIND ALL CLEAR\nLocation: {config['location_name']}\nGenerated: {generated}\n{message}"
    html = f"<h2>Mooring Wind All Clear</h2><p><strong>Location:</strong> {escape(config['location_name'])}</p><p>{message}</p><p style='font-size:12px;color:#64748b'>Enjoyneering ApS · Generated {escape(generated)}</p>"
    return subject, text, html


def send_email(subject, text_body, html_body, chart=None, idempotency_key=None):
    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is not configured.")
    if not EMAIL_TO:
        raise RuntimeError("EMAIL_TO is not configured.")
    payload = {"from": EMAIL_FROM, "to": [EMAIL_TO], "subject": subject, "text": text_body, "html": html_body}
    if chart:
        payload["attachments"] = [{"content": base64.b64encode(chart).decode("ascii"),
                                   "filename": "wind-forecast.png", "content_id": "wind-rose"}]
    headers = {"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:256]
    response = requests.post("https://api.resend.com/emails", headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def send_test_email(config):
    subject = f"Mooring Weather Watch test – {config['location_name']}"
    text = f"Test successful. Mooring Weather Watch can send email notifications.\nLocation: {config['location_name']}"
    html = f"<h2>Test successful</h2><p>Mooring Weather Watch can send email notifications.</p><p><strong>Location:</strong> {escape(config['location_name'])}</p>"
    send_email(subject, text, html)
    print(f"Test email sent to {EMAIL_TO}.")


def main():
    config = load_config()
    if SEND_TEST:
        send_test_email(config)
        return
    assessed = assess_forecasts(config, fetch_yr_forecast(config["latitude"], config["longitude"]))
    state = load_state()
    reason, active = should_notify(state, assessed, float(config.get("renotify_increase_percentage", 5)))
    for item in assessed:
        print(f"{item['hours_ahead']}h | {item['level']} | mean {item['wind_speed']:.1f} m/s | "
              f"gust {item['wind_gust'] if item['wind_gust'] is not None else 'N/A'} | {item['utilisation']:.0f}% | {item['trend']}")
    if reason == "all_clear":
        subject, text, html = build_all_clear_email(config)
        clear_key = datetime.now(timezone.utc).strftime("mooring-weather/all-clear/%Y%m%d%H")
        send_email(subject, text, html, idempotency_key=clear_key)
        print(f"All-clear email sent to {EMAIL_TO}.")
    elif reason:
        subject, text, html = build_alert_email(config, assessed, reason)
        chart = make_wind_rose(config, assessed)
        signature = hashlib.sha256(f"{highest_level(active)}|{max(item['utilisation'] for item in active):.0f}|{active[0]['time']}".encode()).hexdigest()[:20]
        send_email(subject, text, html, chart, f"mooring-weather/{signature}")
        print(f"{highest_level(active)} email sent to {EMAIL_TO} ({reason}).")
    else:
        print("No new notification required.")
    save_state(update_state(state, assessed, reason))


if __name__ == "__main__":
    main()
