import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getRoom, getLatestReading, getRoomReadings, getRoomSummary, getThresholds } from '../api/client';
import ComfortScore    from '../components/ComfortScore';
import MultiLineChart  from '../components/MultiLineChart';
import MetricCard      from '../components/MetricCard';

const STYLES = `
  .rd-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 28px;
  }
  .rd-title { font-size: 1.5rem; font-weight: 700; }
  .live-dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.4; transform: scale(0.8); }
  }
  .rd-back {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.85rem;
    color: var(--muted);
    margin-bottom: 20px;
    cursor: pointer;
    background: none;
    border: none;
    padding: 0;
    transition: color 0.15s;
  }
  .rd-back:hover { color: var(--text); }
  .rd-section { margin-bottom: 32px; }
  .rd-metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
    margin-top: 16px;
  }
  .rd-no-data {
    padding: 40px;
    text-align: center;
    color: var(--muted);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
  }
  .spinner {
    width: 36px; height: 36px;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    margin: 64px auto;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .page-error {
    padding: 14px 18px;
    background: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.3);
    border-radius: 8px;
    color: var(--red);
  }
  .rd-info {
    margin-top: 28px;
    padding: 18px 22px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    font-size: 0.82rem;
    color: var(--muted);
    line-height: 1.7;
  }
  .rd-info h3 {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 10px;
  }
  .rd-info-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px 24px;
    margin-bottom: 12px;
  }
  .rd-info-row { display: flex; flex-direction: column; gap: 1px; }
  .rd-info-label { font-size: 0.75rem; color: var(--accent); font-weight: 500; }
`;

const METRIC_CONFIGS = [
  { key: 'temperature',       label: 'Temperature', unit: '°C'      },
  { key: 'humidity',          label: 'Humidity',    unit: '%'       },
  { key: 'sound_db',          label: 'Sound',       unit: 'dB'      },
  { key: 'light_lux',         label: 'Light',       unit: 'lux'     },
  { key: 'movements_per_min', label: 'Motion',      unit: 'mov/min' },
];

function getStatus(metric, value, thresholds) {
  if (!thresholds || value == null) return 'good';
  const within = (v, lo, hi) => v >= lo && v <= hi;
  const borderline = (v, lo, hi) => {
    const margin = (hi - lo) * 0.1;
    return v >= lo - margin && v <= hi + margin;
  };
  const map = {
    temperature: [thresholds.temp_min,     thresholds.temp_max],
    humidity:    [thresholds.humidity_min,  thresholds.humidity_max],
    sound_db:    [0,                         thresholds.sound_max_db],
    light_lux:   [thresholds.light_min_lux, thresholds.light_max_lux],
  };
  const range = map[metric];
  if (!range) return 'good';
  const [lo, hi] = range;
  if (within(value, lo, hi))     return 'good';
  if (borderline(value, lo, hi)) return 'warning';
  return 'bad';
}

export default function RoomDetail() {
  const { room_id } = useParams();
  const navigate    = useNavigate();

  const [room,       setRoom]       = useState(null);
  const [summary,    setSummary]    = useState(null);
  const [thresholds, setThresholds] = useState(null);
  const [readings,   setReadings]   = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState(null);

  // Initial load — fetch room meta, summary, thresholds, and seed the chart
  // with the last 60 readings from the DB so the graph is never empty on mount
  useEffect(() => {
    (async () => {
      try {
        const [r, s, t, history] = await Promise.all([
          getRoom(room_id),
          getRoomSummary(room_id),
          getThresholds(),
          getRoomReadings(room_id, 60),
        ]);
        setRoom(r);
        setSummary(s);
        setThresholds(t);
        // API returns newest-first; reverse so the chart flows left→right
        setReadings(history.slice().reverse());
      } catch (err) {
        setError(err.response?.status === 404 ? 'Room not found.' : 'Failed to load room data.');
      } finally {
        setLoading(false);
      }
    })();
  }, [room_id]);

  // Rolling poll
  const fetchLatest = useCallback(async () => {
    try {
      const reading = await getLatestReading(room_id);
      setReadings(prev => {
        if (prev.length > 0 && prev[prev.length - 1].id === reading.id) return prev;
        const updated = [...prev, reading];
        return updated.length > 60 ? updated.slice(-60) : updated;
      });
    } catch {
      // 404 = no readings yet; ignore silently
    }
  }, [room_id]);

  useEffect(() => {
    fetchLatest();
    const interval = setInterval(fetchLatest, 3000);
    return () => clearInterval(interval);
  }, [fetchLatest]);

  if (loading) return <><style>{STYLES}</style><div className="spinner" /></>;
  if (error)   return <><style>{STYLES}</style><div className="page-error">{error}</div></>;

  const latest = readings[readings.length - 1] ?? null;

  return (
    <>
      <style>{STYLES}</style>

      <button className="rd-back" onClick={() => navigate('/')}>← All Rooms</button>

      <div className="rd-header">
        <div className="live-dot" />
        <h1 className="rd-title">{room.name}</h1>
      </div>

      <div className="rd-section">
        <ComfortScore score={latest?.comfort_score ?? null} />
      </div>

      {readings.length === 0 ? (
        <div className="rd-no-data">No readings yet. Waiting for the ESP32 to connect…</div>
      ) : (
        <>
          <div className="rd-section">
            <MultiLineChart data={readings} />
          </div>

          <div className="rd-metrics-grid">
            {METRIC_CONFIGS.map(({ key, label, unit }) => (
              <MetricCard
                key={key}
                metric={label}
                value={latest?.[key] ?? null}
                unit={unit}
                summary={summary?.[key] ?? null}
                status={getStatus(key, latest?.[key], thresholds)}
                onClick={() => navigate(`/rooms/${room_id}/metrics/${key}`)}
              />
            ))}
          </div>
        </>
      )}

      <div className="rd-info">
        <h3>How the comfort score is calculated</h3>
        <p style={{ marginBottom: 10 }}>
          Each reading produces a score from 0–100. Five metrics contribute equally at 20 points each.
          A metric scores full points when inside its ideal range and loses points proportionally as it drifts outside.
        </p>
        <div className="rd-info-grid">
          <div className="rd-info-row">
            <span className="rd-info-label">Temperature</span>
            <span>Ideal {thresholds?.temp_min ?? 18}–{thresholds?.temp_max ?? 26} °C. Zero at ±10 °C outside range.</span>
          </div>
          <div className="rd-info-row">
            <span className="rd-info-label">Humidity</span>
            <span>Ideal {thresholds?.humidity_min ?? 30}–{thresholds?.humidity_max ?? 60} % RH. Zero at ±30 % RH outside range.</span>
          </div>
          <div className="rd-info-row">
            <span className="rd-info-label">Sound</span>
            <span>Ideal ≤ {thresholds?.sound_max_db ?? 40} dB. −2 pts per dB above threshold, zero at +10 dB over.</span>
          </div>
          <div className="rd-info-row">
            <span className="rd-info-label">Light</span>
            <span>Ideal {thresholds?.light_min_lux ?? 300}–{thresholds?.light_max_lux ?? 500} lux. Zero at ±500 lux outside range.</span>
          </div>
          <div className="rd-info-row">
            <span className="rd-info-label">Motion</span>
            <span>Ideal ≤ {thresholds?.motion_max_per_min ?? 10} mov/min. −2 pts per mov/min above threshold, zero at +10 over.</span>
          </div>
        </div>
        <p>
          Thresholds are configurable in Settings and apply immediately to the next reading.
          Avg / Min / Max on each card are computed over the last 24 hours of stored readings.
        </p>
      </div>
    </>
  );
}
