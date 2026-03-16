import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import api from '../api';
import StatusBadge, { SyncBadge } from '../components/StatusBadge';

export default function InstanceDetail() {
  const { id } = useParams();
  const [inst, setInst] = useState(null);

  const load = () => api.instGet(id).then(setInst).catch(() => {});
  useEffect(() => { load(); }, [id]);

  const refreshStatus = async () => {
    const r = await api.instRefreshStatus(id);
    setInst(prev => ({ ...prev, status: r.status }));
  };

  const refreshConfigs = async () => {
    await api.instRefreshConfigs(id);
    load();
  };

  if (!inst) return <div className="text-center py-5"><div className="spinner-border"></div></div>;

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-hdd-rack me-2"></i>
          <span className="font-monospace">{inst.win_service_name}</span>
          <span className="text-muted ms-2">@ {inst.hostname}</span>
        </h4>
        <Link to="/instances" className="btn btn-outline-secondary">
          <i className="bi bi-arrow-left me-1"></i>Назад
        </Link>
      </div>

      <div className="card mb-3">
        <div className="card-body">
          <div className="row">
            <div className="col-md-6">
              <p><strong>Сервис:</strong> {inst.service_name}</p>
              <p><strong>Exe:</strong> <code>{inst.exe_path || '—'}</code></p>
              <p><strong>Config dir:</strong> <code>{inst.config_dir || '—'}</code></p>
            </div>
            <div className="col-md-6">
              <p>
                <strong>Статус:</strong> <StatusBadge status={inst.status} />
                <button className="btn btn-sm btn-link" onClick={refreshStatus}>
                  <i className="bi bi-arrow-clockwise"></i>
                </button>
              </p>
              <p><strong>Проверен:</strong> {inst.last_status_check || '—'}</p>
            </div>
          </div>
        </div>
      </div>

      <div className="d-flex justify-content-between align-items-center mb-2">
        <h5>Конфиги ({inst.configs?.length || 0})</h5>
        <button className="btn btn-sm btn-outline-info" onClick={refreshConfigs}>
          <i className="bi bi-arrow-clockwise me-1"></i>Обновить с сервера
        </button>
      </div>

      {inst.configs?.length > 0 ? (
        <table className="table table-sm">
          <thead><tr><th>Файл</th><th>Путь</th><th>Sync</th><th></th></tr></thead>
          <tbody>
            {inst.configs.map(c => (
              <tr key={c.id}>
                <td className="font-monospace">{c.filename}</td>
                <td className="text-muted small">{c.filepath}</td>
                <td><SyncBadge status={c.sync_status} size="sm" /></td>
                <td className="text-end">
                  <Link to={`/instances/${id}/configs/${c.id}`} className="btn btn-sm btn-outline-secondary">
                    <i className="bi bi-pencil"></i>
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="text-muted">Нет конфигов</p>}

      {inst.virtual_configs?.length > 0 && (
        <>
          <h5 className="mt-3">Виртуальные конфиги сервиса</h5>
          <div className="list-group">
            {inst.virtual_configs.map(vc => (
              <div key={vc.id} className="list-group-item d-flex align-items-center gap-2 py-1">
                <i className="bi bi-file-earmark-code text-info"></i>
                <span className="font-monospace">{vc.filename}</span>
                {vc.env_label && <span className="badge bg-primary">{vc.env_label}</span>}
                <SyncBadge status={vc.sync_status} size="sm" />
                {vc.version && <span className="badge bg-info">v{vc.version}</span>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
