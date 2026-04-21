# Usage
# ─────
# Make sure the backend is running first:
#   docker compose up
#
# Then in a separate terminal, from the backend/ directory:
#   pip install requests numpy        # if running outside Docker
#   python scripts/generate_data.py
#
# To simulate multiple rooms, register them in the dashboard first,
# then add their slugs to the ROOMS list at the top of this file.
#
# Stop with Ctrl+C.

"""
Standalone sensor data generator for studyspace-iot development.

Mimics one or more ESP32 devices by generating physically plausible sensor
readings and posting them to /api/ingest every SEND_INTERVAL seconds.
Values drift sinusoidally over time using a tick counter so the output is
deterministic for a given number of cycles regardless of wall-clock speed.
"""

import signal
import sys
import time
from datetime import datetime, timezone

import numpy as np
import requests

# ─── Configuration ────────────────────────────────────────────────────────────
BACKEND_URL   = "http://localhost:8000/api/ingest"
SEND_INTERVAL = 5          # seconds between posts

ROOMS = [
    "muhabura_1r01",     # add as many room slugs as you need
]

# ─── Sinusoidal Period Constants (in ticks, 1 tick = SEND_INTERVAL seconds) ──
_TEMP_PERIOD    = int(10 * 60 / SEND_INTERVAL)   # 10-minute HVAC cycle → 120 ticks
_HUMID_PERIOD   = int(15 * 60 / SEND_INTERVAL)   # 15-minute humidity swing → 180 ticks
_LIGHT_PERIOD   = int( 8 * 60 / SEND_INTERVAL)   # 8-minute lighting cycle  →  96 ticks


def _now_cat() -> str:
    """Return current CAT wall-clock time as HH:MM:SS for terminal display."""
    from zoneinfo import ZoneInfo  # stdlib in Python 3.9+
    return datetime.now(ZoneInfo("Africa/Kigali")).strftime("%H:%M:%S")


def _utc_iso() -> str:
    """Return current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _generate(room_index: int, tick: int) -> dict:
    """
    Generate one set of sensor readings for a room.

    Args:
        room_index: Index of the room in ROOMS — used as a phase offset so
                    rooms drift out of phase with each other.
        tick:       Monotonically increasing cycle counter since script start.

    Returns:
        Dict with all raw sensor fields expected by SensorPayload.
    """
    # Phase offset separates each room's drift by 2π/3 (120°) so they never
    # all peak or trough simultaneously — more realistic for independent rooms.
    phase = room_index * (2 * np.pi / 3)

    # ── Temperature ───────────────────────────────────────────────────────────
    temp = (
        22.0
        + 3.0 * np.sin(2 * np.pi * tick / _TEMP_PERIOD + phase)
        + np.random.normal(0, 0.3)
    )
    temp = float(np.clip(temp, 15.0, 35.0))

    # ── Humidity ─────────────────────────────────────────────────────────────
    humidity = (
        50.0
        + 15.0 * np.sin(2 * np.pi * tick / _HUMID_PERIOD + phase)
        + np.random.normal(0, 1.0)
    )
    humidity = float(np.clip(humidity, 20.0, 90.0))

    # ── Motion count ─────────────────────────────────────────────────────────
    # Regime probabilities: 65% silence, 30% light activity, 5% burst
    regime = np.random.choice(["silent", "light", "burst"], p=[0.65, 0.30, 0.05])
    if regime == "silent":
        motion_count = 0
    elif regime == "light":
        motion_count = int(np.random.randint(1, 4))   # 1–3 events
    else:
        motion_count = int(np.random.randint(5, 13))  # 5–12 burst

    # ── Light (ADC 0–4095) ────────────────────────────────────────────────────
    # Target lux range 300–500. Working the GL5528 formula backwards:
    #   lux = 500 / (R_kΩ^0.7)  →  R_kΩ = (500/lux)^(1/0.7)
    #   voltage divider: V = R_fixed*3.3 / (R_ldr+R_fixed), ADC = (V/3.3)*4095
    # ADC ≈ 400 → ~470 lux,  ADC ≈ 550 → ~360 lux,  ADC ≈ 700 → ~300 lux
    # Base 530 with drift ±130 keeps values centred in the 300–500 lux band.
    light_raw = (
        530
        + 130 * np.sin(2 * np.pi * tick / _LIGHT_PERIOD + phase)
        + np.random.normal(0, 20)
    )
    light_raw = int(np.clip(light_raw, 0, 4095))

    # ── Sound RMS ────────────────────────────────────────────────────────────
    # Target dB range: quiet ~32 dB, moderate ~50 dB, loud ~68 dB.
    # Working the INMP441 formula backwards: rms = 420426 * 10^((dB-94)/20)
    #   32 dB → rms ≈  420    (library silence, below 40 dB threshold)
    #   50 dB → rms ≈ 2_650   (conversation-level, above threshold)
    #   68 dB → rms ≈ 16_750  (loud activity spike)
    sound_regime = np.random.choice(["quiet", "moderate", "loud"], p=[0.70, 0.20, 0.10])
    if sound_regime == "quiet":
        sound_rms = int(np.random.normal(420, 100))
    elif sound_regime == "moderate":
        sound_rms = int(np.random.normal(2_650, 500))
    else:
        sound_rms = int(np.random.normal(16_750, 3_000))
    # Clamp to 24-bit INMP441 scale; negative RMS is physically impossible
    sound_rms = int(np.clip(sound_rms, 0, 400_000))

    return {
        "room_id":      None,          # filled in by caller
        "timestamp":    _utc_iso(),
        "temperature":  round(temp, 2),
        "humidity":     round(humidity, 2),
        "motion_count": motion_count,
        "light_raw":    light_raw,
        "sound_rms":    sound_rms,
    }


def generate_and_post(room_id: str, room_index: int, tick: int) -> None:
    """Generate sensor values for one room and POST them to the backend."""
    payload = _generate(room_index, tick)
    payload["room_id"] = room_id

    ts = _now_cat()

    try:
        response = requests.post(BACKEND_URL, json=payload, timeout=5)

        if response.status_code == 201:
            data = response.json()
            print(
                f"[{ts}] {room_id:<22} →  {response.status_code}"
                f"  |  temp={data['temperature']:.1f}°C"
                f"  hum={data['humidity']:.1f}%"
                f"  motion={data['motion_count']}"
                f"  light={data['light_raw']}"
                f"  rms={data['sound_rms']}"
            )
        else:
            # Print the raw response body — useful for diagnosing 404 (room not
            # registered) or 422 (validation error from out-of-range values)
            print(
                f"[{ts}] {room_id:<22} →  {response.status_code}"
                f"  |  {response.text.strip()}"
            )

    except requests.exceptions.ConnectionError:
        print("[WARN] Could not connect to backend. Is docker compose up running?")
    except requests.exceptions.Timeout:
        print(f"[WARN] Request timed out for room {room_id!r} — backend may be overloaded")
    except Exception as exc:
        print(f"[ERROR] Unexpected error for room {room_id!r}: {exc}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def handle_exit(sig, frame):
    print("\n[INFO] Generator stopped.")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_exit)

print("[INFO] StudySpace IoT — Sensor Data Generator")
print(f"[INFO] Backend: {BACKEND_URL}")
print(f"[INFO] Rooms:   {', '.join(ROOMS)}")
print(f"[INFO] Interval: {SEND_INTERVAL}s   |   Press Ctrl+C to stop\n")

tick = 0
while True:
    for room_index, room_id in enumerate(ROOMS):
        generate_and_post(room_id, room_index, tick)
    tick += 1
    time.sleep(SEND_INTERVAL)
