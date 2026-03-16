import React from 'react';

const STATUS_MAP = {
  running:       { cls: 'bg-success',            icon: 'bi-play-fill' },
  stopped:       { cls: 'bg-danger',             icon: 'bi-stop-fill' },
  starting:      { cls: 'bg-warning text-dark',  icon: 'bi-hourglass-split' },
  'stop pending':{ cls: 'bg-warning text-dark',  icon: 'bi-hourglass-split' },
  unknown:       { cls: 'bg-secondary',           icon: 'bi-question' },
};

export default function StatusBadge({ status }) {
  const st = (status || 'unknown').toLowerCase();
  const m = STATUS_MAP[st] || STATUS_MAP.unknown;
  return (
    <span className={`badge ${m.cls}`}>
      <i className={`bi ${m.icon} me-1`}></i>{status || 'unknown'}
    </span>
  );
}

const SYNC_MAP = {
  synced:     { cls: 'bg-success',           label: 'sync',    icon: 'bi-check-lg' },
  overridden: { cls: 'bg-warning text-dark', label: 'изменён', icon: 'bi-pencil' },
  outdated:   { cls: 'bg-danger',            label: 'устарел', icon: 'bi-exclamation-triangle' },
  untracked:  { cls: 'bg-secondary',         label: '—',       icon: 'bi-dash' },
};

export function SyncBadge({ status, size }) {
  const m = SYNC_MAP[status] || SYNC_MAP.untracked;
  const style = size === 'sm' ? { fontSize: '.65rem' } : {};
  return (
    <span className={`badge ${m.cls}`} style={style}>
      <i className={`bi ${m.icon} me-1`}></i>{m.label}
    </span>
  );
}
