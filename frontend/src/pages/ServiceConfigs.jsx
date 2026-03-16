import React, { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import api from '../api';
import { SyncBadge } from '../components/StatusBadge';
import Confirm from '../components/Confirm';

export default function ServiceConfigs() {
  const { serviceId } = useParams();
  const [sp, setSp] = useSearchParams();
  const envFilter = sp.get('env_id');
  const [data, setData] = useState(null);
  const [delId, setDelId] = useState(null);

  const load = () => {
    const params = envFilter != null ? `?env_id=${envFilter}` : '';
    api.get(`/services/${serviceId}/configs${params}`).then(setData).catch(() => {});
  };
  useEffect(() => { load(); }, [serviceId, envFilter]);

  if (!data) return <div className="text-center py-5"><div className="spinner-border"></div></div>;

  const doDelete = async () => {
    await api.cfgDelete(serviceId, delId);
    setDelId(null);
    load();
  };

  const setEnvFilter = (val) => {
    if (val === null) sp.delete('env_id');
    else sp.set('env_id', val);
    setSp(sp);
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-file-earmark-code me-2"></i>
          Конфиги: {data.service_name}
        </h4>
        <Link to={`/services/${serviceId}/configs/create`} className="btn btn-primary">
          <i className="bi bi-plus-lg me-1"></i>Создать
        </Link>
      </div>

      {/* Env tabs */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button className={`nav-link ${envFilter == null ? 'active' : ''}`}
                  onClick={() => setEnvFilter(null)}>Все</button>
        </li>
        <li className="nav-item">
          <button className={`nav-link ${envFilter === '0' ? 'active' : ''}`}
                  onClick={() => setEnvFilter('0')}>Базовые</button>
        </li>
        {data.environments?.filter(e => data.used_env_ids?.includes(e.id)).map(e => (
          <li className="nav-item" key={e.id}>
            <button className={`nav-link ${envFilter === String(e.id) ? 'active' : ''}`}
                    onClick={() => setEnvFilter(String(e.id))}>{e.name}</button>
          </li>
        ))}
      </ul>

      {data.configs.map(cfg => {
        const ss = data.sync_summaries[cfg.id] || {};
        return (
          <div key={cfg.id} className="card mb-3">
            <div className="card-header d-flex align-items-center gap-2">
              <i className="bi bi-file-earmark-code text-info"></i>
              <span className="fw-semibold font-monospace">{cfg.filename}</span>
              {cfg.env_label && <span className="badge bg-primary">{cfg.env_label}</span>}
              {cfg.description && <span className="text-muted small ms-2">{cfg.description}</span>}
              <span className="ms-auto d-flex gap-1">
                {ss.version && <span className="badge bg-info">v{ss.version}</span>}
                <span className="badge bg-success">{ss.synced || 0}</span>
                {ss.outdated > 0 && <span className="badge bg-danger">{ss.outdated}</span>}
                {ss.overridden > 0 && <span className="badge bg-warning text-dark">{ss.overridden}</span>}
                {ss.untracked > 0 && <span className="badge bg-secondary">{ss.untracked}</span>}
                <span className="text-muted small ms-1">/ {ss.total || 0}</span>
              </span>
            </div>
            <div className="card-body py-2 d-flex gap-2">
              <Link to={`/services/${serviceId}/configs/${cfg.id}/edit`} className="btn btn-sm btn-outline-secondary">
                <i className="bi bi-pencil me-1"></i>Редактировать
              </Link>
              <Link to={`/services/${serviceId}/configs/${cfg.id}/versions`} className="btn btn-sm btn-outline-info">
                <i className="bi bi-clock-history me-1"></i>Версии
              </Link>
              <Link to={`/services/${serviceId}/configs/${cfg.id}/push`} className="btn btn-sm btn-outline-success">
                <i className="bi bi-cloud-upload me-1"></i>Push
              </Link>
              <button className="btn btn-sm btn-outline-danger ms-auto" onClick={() => setDelId(cfg.id)}>
                <i className="bi bi-trash me-1"></i>Удалить
              </button>
            </div>
          </div>
        );
      })}
      {!data.configs.length && <p className="text-muted text-center py-4">Нет конфигов</p>}
      <Confirm show={!!delId} title="Удалить конфиг?" body="Это действие нельзя отменить."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
