import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getLatestReading, getRoomSummary, getThresholds } from '../api/client';
import SingleMetricChart from '../components/SingleMetricChart';

const STYLES = `
  .md-back {
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
  .md-back:hover { color: var(--text); }
  .md-header { margin-bottom: 28px; }
  .md-title { font-size: 1.5rem; font-weight: 700; }
  .md-subtitle { color: var(--muted); font-size: 0.9rem; margin-top: 4px; }
  .md-current {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 32px;
  }
  .md-value {
    font-size: 3.5rem;
    font-weight: 700;
    line-height: 1;
  }
  .md-unit {
    font-size: 1.25rem;
    color: var(--muted);
  }
  .md-badge {
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    align-self: center;
  }
  .badge-good    { background: rgba(34,197,94,0.15);  color: var(--green); }
  .badge-warning { background: rgba(234,179,8,0.15);  color: var(--yellow); }
  .badge-bad     { background: rgba(239,68,68,0.15);  color: var(--red); }
  .md-summary {
    display: flex;
    gap: 24px;
    margin-top: 24px;
    padding: 16px 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  .md-stat { display: flex; flex-direction: column; gap: 4px; }
  .md-stat-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .md-stat-value { font-size: 1.1rem; font-weight: 600; }
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
`;

const METRIC_META = {
  temperature: { label: 'Temperature', unit: '°C',     color: '#f97316' },
  humidity:    { label: 'Humidity',    unit: '%',      color: '#3b82f6' },
  sound_db:    { label: 'Sound',       unit: 'dB',     color: '#a855f7' },
  light_lux:   { label: 'Light',       unit: 'lux',    color: '#eab308' },
  movements_per_min: { label: 'Motion', unit: 'mov/min', color: '#22c55e' },
  comfort_score:     { label: 'Comfort Score', unit: '/100', color: '#6366f1' },
};

function getStatus(metric, value, thresholds) {
  if (!thresholds || value == null) return 'good';
  const within = (v, lo, hi) => v >= lo && v <= hi;
  const borderline = (v, lo, hi) => {
    const margin = (hi - lo) * 0.1;
    return v >= lo - margin && v <= hi + margin;
  };
  const map = {
    temperature: [thresholds.temp_min, thresholds.temp_max],
    humidity:    [thresholds.humidity_min, thresholds.humidity_max],
    sound_db:    [0, thresholds.sound_max_db],
    light_lux:   [thresholds.light_min_lux, thresholds.light_max_lux],
  };
  const range = map[metric];
  if (!range) return 'good';
  const [lo, hi] = range;
  if (within(value, lo, hi))     return 'good';
  if (borderline(value, lo, hi)) return 'warning';
  return 'bad';
}

export default function MetricDetail() {
  const { room_id, metric } = useParams();
  const navigate = useNavigate();
  const meta = METRIC_META[metric] ?? { label: metric, unit: '', color: '#6366f1' };

  const [readings,   setReadings]   = useState([]);
  const [summary,    setSummary]    = useState(null);
  const [thresholds, setThresholds] = useState(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [s, t] = await Promise.all([getRoomSummary(room_id), getThresholds()]);
        setSummary(s);
        setThresholds(t);
      } catch (err) {
        setError('Failed to load data.');
      } finally {
        setLoading(false);
      }
    })();
  }, [room_id]);

  const fetchLatest = useCallback(async () => {
    try {
      const reading = await getLatestReading(room_id);
      setReadings(prev => {
        if (prev.length > 0 && prev[prev.length - 1].id === reading.id) return prev;
        const updated = [...prev, reading];
        return updated.length > 60 ? updated.slice(-60) : updated;
      });
    } catch { /* no readings yet */ }
  }, [room_id]);

  useEffect(() => {
    fetchLatest();
    const interval = setInterval(fetchLatest, 3000);
    return () => clearInterval(interval);
  }, [fetchLatest]);

  if (loading) return <><style>{STYLES}</style><div className="spinner" /></>;
  if (error)   return <><style>{STYLES}</style><div className="page-error">{error}</div></>;

  const latest = readings[readings.length - 1] ?? null;
  const currentValue = latest?.[metric] ?? null;
  const status = getStatus(metric, currentValue, thresholds);
  const badgeLabel = status === 'good' ? 'Normal' : status === 'warning' ? 'Borderline' : 'Out of range';
  const metricSummary = summary?.[metric] ?? null;

  return (
    <>
      <style>{STYLES}</style>

      <button className="md-back" onClick={() => navigate(`/rooms/${room_id}`)}>
        ← Back to room
      </button>

      <div className="md-header">
        <h1 className="md-title">{meta.label}</h1>
        <p className="md-subtitle">Live 5-minute rolling window · {room_id}</p>
      </div>

      <div className="md-current">
        <span className="md-value" style={{ color: meta.color }}>
          {currentValue != null ? currentValue.toFixed(1) : '—'}
        </span>
        <span className="md-unit">{meta.unit}</span>
        <span className={`md-badge badge-${status}`}>{badgeLabel}</span>
      </div>

      {readings.length === 0 ? (
        <div style={{ color: 'var(--muted)', textAlign: 'center', padding: '32px' }}>
          Waiting for readings…
        </div>
      ) : (
        <SingleMetricChart
          data={readings}
          metric={metric}
          unit={meta.unit}
          color={meta.color}
        />
      )}

      {metricSummary && (
        <div className="md-summary">
          {[['Avg', metricSummary.avg], ['Min', metricSummary.min], ['Max', metricSummary.max]].map(([label, val]) => (
            <div className="md-stat" key={label}>
              <span className="md-stat-label">{label} (24h)</span>
              <span className="md-stat-value">
                {val != null ? `${val.toFixed(1)} ${meta.unit}` : '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
