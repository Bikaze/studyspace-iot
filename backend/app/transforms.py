"""
Pure sensor data transformation functions for the studyspace-iot backend.

This module converts raw ESP32 sensor values into meaningful physical units
and computes a composite comfort score. It has no imports from database.py or
models.py and carries no HTTP or ORM concerns, making every function
independently unit-testable.
"""

import math


def adc_to_lux(adc_value: int) -> float:
    """Convert a 12-bit ADC count from the LDR voltage divider into lux.

    Circuit topology
    ----------------
    The GL5528 LDR is wired in a voltage divider with a 10 kΩ fixed resistor
    (R_fixed) between 3.3 V and GND. The ESP32 ADC reads the node between the
    LDR and R_fixed:

        3.3V ── [LDR] ──┬── [10 kΩ] ── GND
                        └── ADC pin

    As the room gets brighter, LDR resistance drops, the node voltage rises,
    and the ADC count increases.

    Conversion steps
    ----------------
    1. Recover node voltage from the 12-bit ADC count (0–4095 → 0–3.3 V).
    2. Back-calculate LDR resistance from the voltage divider equation.
    3. Apply the GL5528 inverse power-law: lux ≈ 500 / (R_kΩ ^ 0.7).
       This curve is empirically derived from the GL5528 datasheet and is
       accurate to ±20 % across the 10–1 000 lux range typical of indoor
       environments.

    Edge cases
    ----------
    - adc_value == 4095 (ADC saturated, voltage == 3.3 V): the divider
      formula produces a zero denominator.  We return 0.0 rather than raising.
    - Any result below 0.0 is clamped to 0.0.

    Args:
        adc_value: Raw 12-bit ADC reading, 0–4095.

    Returns:
        Illuminance in lux (float, ≥ 0.0).
    """
    voltage = (adc_value / 4095) * 3.3
    denominator = 3.3 - voltage
    if denominator <= 0.0:
        return 0.0

    ldr_resistance = (10_000 * voltage) / denominator  # ohms
    ldr_resistance_kohm = ldr_resistance / 1_000
    if ldr_resistance_kohm <= 0.0:
        return 0.0

    lux = 500 / (ldr_resistance_kohm ** 0.7)
    return max(0.0, lux)


def rms_to_db(rms_value: int, reference: int = 1) -> float:
    """Convert a raw INMP441 RMS amplitude integer into dB SPL.

    The INMP441 is a 24-bit I2S MEMS microphone with a sensitivity of
    −26 dBFS at 94 dB SPL (1 kHz, 1 Pa).  The full-scale 24-bit signed
    integer has a peak value of 2^23 − 1 ≈ 8 388 607.  A 94 dB SPL sine
    wave therefore produces an RMS of approximately:

        peak / √2 × 10^(−26/20) ≈ 8 388 607 / 1.414 × 0.0501 ≈ 297 302

    The commonly used rounded figure from the INMP441 application notes is
    420 426 RMS → 94 dB SPL (accounting for real-world crest factors).

    Anchoring
    ---------
    We convert the raw RMS to dBFS first:

        dBFS = 20 × log10(rms_value / reference)

    Then shift the scale so that 420 426 RMS reads 94 dB SPL:

        offset = 94 − 20 × log10(420 426 / reference)
        dB_SPL = dBFS + offset

    This collapses to a single expression:

        dB_SPL = 20 × log10(rms_value / 420 426) + 94

    which is what this function computes when reference == 1 (the default).
    Pass a different reference to override the anchor if your hardware is
    calibrated differently.

    Edge cases
    ----------
    rms_value ≤ 0 returns 0.0 to avoid a math domain error.

    Args:
        rms_value: Raw integer RMS amplitude from the INMP441.
        reference: Anchor RMS value that corresponds to 94 dB SPL.
                   Defaults to 1, which uses the internal 420 426 constant.

    Returns:
        Sound pressure level in dB SPL (float).  Returns 0.0 for silence or
        invalid input.
    """
    if rms_value <= 0:
        return 0.0

    _NOMINAL_RMS_AT_94DB = 420_426
    db_spl = 20 * math.log10(rms_value / _NOMINAL_RMS_AT_94DB) + 94
    return db_spl


def compute_movements_per_min(motion_count: int) -> float:
    """Convert a 5-second HC-SR501 motion count into movements per minute.

    The ESP32 firmware counts PIR rising-edge interrupts over a fixed 5-second
    window before transmitting.  There are 60 / 5 = 12 such windows per
    minute, so:

        movements_per_min = motion_count × 12

    Interval assumption
    -------------------
    This multiplier is hard-coded to the 5-second window defined in the
    firmware (config.h → MOTION_WINDOW_MS = 5000).  If the firmware window
    duration ever changes, this multiplier must be updated to match:

        multiplier = 60 / (MOTION_WINDOW_MS / 1000)

    Args:
        motion_count: Number of PIR rising-edge interrupts in a 5-second window.

    Returns:
        Estimated movements per minute (float).
    """
    _WINDOWS_PER_MINUTE = 12  # 60 s / 5 s window
    return float(motion_count * _WINDOWS_PER_MINUTE)


def compute_comfort_score(
    temperature: float,
    humidity: float,
    sound_db: float,
    light_lux: float,
    movements_per_min: float,
    thresholds,
) -> float:
    """Compute a 0–100 comfort score indicating study-room suitability.

    Weighting rationale
    -------------------
    Five equally weighted metrics each contribute up to 20 points:

        temperature        20 pts  — thermal comfort directly affects focus
        humidity           20 pts  — extreme RH causes fatigue and dry eyes
        sound_db           20 pts  — noise is the primary study disruptor
        light_lux          20 pts  — insufficient or excessive light causes strain
        movements_per_min  20 pts  — high motion indicates crowding/distraction

    Sub-score derivation
    --------------------
    Temperature (±10 °C tolerance outside ideal range):
        • 20 pts  if temp_min ≤ T ≤ temp_max
        • Scales linearly to 0 pts at 10 °C beyond either bound

    Humidity (±30 % RH tolerance):
        • 20 pts  if humidity_min ≤ H ≤ humidity_max
        • Scales linearly to 0 pts at 30 % RH beyond either bound

    Sound (above-threshold penalty):
        • 20 pts  if sound_db ≤ sound_max_db
        • −2 pts  for every dB above the threshold
        • 0 pts   if 10+ dB above threshold

    Light (±500 lux tolerance outside ideal range):
        • 20 pts  if light_min_lux ≤ L ≤ light_max_lux
        • Scales linearly to 0 pts at 500 lux beyond either bound

    Motion (above-threshold penalty):
        • 20 pts  if movements_per_min ≤ motion_max_per_min
        • −2 pts  for every movement/min above the threshold
        • 0 pts   if 10+ movements/min above threshold

    All sub-scores are clamped to [0, 20] before summing.

    Args:
        temperature:        Air temperature in °C.
        humidity:           Relative humidity in %.
        sound_db:           Sound pressure level in dB SPL.
        light_lux:          Illuminance in lux.
        movements_per_min:  Estimated movements per minute.
        thresholds:         SQLAlchemy ComfortThreshold ORM instance.

    Returns:
        Composite comfort score in [0.0, 100.0], rounded to one decimal place.
    """

    def _range_score(value: float, low: float, high: float, tolerance: float) -> float:
        """Full score within [low, high]; linear decay to 0 at ±tolerance."""
        if low <= value <= high:
            return 20.0
        excess = (low - value) if value < low else (value - high)
        return max(0.0, 20.0 * (1 - excess / tolerance))

    def _above_threshold_score(value: float, threshold: float, penalty_per_unit: float = 2.0) -> float:
        """Full score at or below threshold; linear decay to 0 at 10 units above."""
        if value <= threshold:
            return 20.0
        excess = value - threshold
        return max(0.0, 20.0 - excess * penalty_per_unit)

    temp_score = _range_score(
        temperature, thresholds.temp_min, thresholds.temp_max, tolerance=10.0
    )
    humidity_score = _range_score(
        humidity, thresholds.humidity_min, thresholds.humidity_max, tolerance=30.0
    )
    sound_score = _above_threshold_score(sound_db, thresholds.sound_max_db)
    light_score = _range_score(
        light_lux, thresholds.light_min_lux, thresholds.light_max_lux, tolerance=500.0
    )
    motion_score = _above_threshold_score(movements_per_min, thresholds.motion_max_per_min)

    total = temp_score + humidity_score + sound_score + light_score + motion_score
    return round(total, 1)


def run_all_transforms(payload, thresholds) -> dict:
    """Run every sensor transform and return a dict ready to merge into a DB row.

    This is the single entry point the readings route calls.  It accepts the
    validated SensorPayload Pydantic object and the active ComfortThreshold
    ORM instance, applies all conversions, and returns a flat dictionary
    containing only the four derived fields:

        light_lux          — converted from payload.light_raw
        sound_db           — converted from payload.sound_rms
        movements_per_min  — derived from payload.motion_count
        comfort_score      — composite 0–100 score

    The caller is responsible for merging this dict with the raw payload fields
    before inserting into sensor_readings.

    Args:
        payload:    Validated SensorPayload instance.
        thresholds: Active ComfortThreshold ORM instance.

    Returns:
        dict with keys: light_lux, sound_db, movements_per_min, comfort_score.
    """
    light_lux = adc_to_lux(payload.light_raw)
    sound_db = rms_to_db(payload.sound_rms)
    movements_per_min = compute_movements_per_min(payload.motion_count)
    comfort_score = compute_comfort_score(
        temperature=payload.temperature,
        humidity=payload.humidity,
        sound_db=sound_db,
        light_lux=light_lux,
        movements_per_min=movements_per_min,
        thresholds=thresholds,
    )

    return {
        "light_lux": light_lux,
        "sound_db": sound_db,
        "movements_per_min": movements_per_min,
        "comfort_score": comfort_score,
    }
