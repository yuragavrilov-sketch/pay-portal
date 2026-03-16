import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import api from '../api';

export default function ServiceConfigVersions() {
  const { serviceId, cfgId } = useParams();
  const [data, setData] = useState(null);

  const load = () => api.cfgVersions(serviceId, cfgId).then(setData).catch(() => {});
  useEffect(() => { load(); }, [serviceId, cfgId]);

  const activate = async (verId) => {
    await api.cfgActivateVer(serviceId, cfgId, verId);
    load();
  };

  if (!data) return <div className="text-center py-5"><div className="spinner-border"></div></div>;

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-clock-history me-2"></i>
          Версии: <span className="font-monospace">{data.filename}</span>
        </h4>
        <Link to={`/services/${serviceId}/configs`} className="btn btn-outline-secondary">
          <i className="bi bi-arrow-left me-1"></i>Назад
        </Link>
      </div>
      <div className="list-group">
        {data.versions.map(v => (
          <div key={v.id} className={`list-group-item d-flex align-items-center gap-2 ${v.is_current ? 'list-group-item-success' : ''}`}>
            <span className="badge bg-primary">v{v.version}</span>
            {v.is_current && <span className="badge bg-success">активная</span>}
            <span className="flex-grow-1">{v.comment || ''}</span>
            <span className="text-muted small">{v.created_at}</span>
            <span className="text-muted small">{v.created_by}</span>
            {!v.is_current && (
              <button className="btn btn-sm btn-outline-warning" onClick={() => activate(v.id)}>
                <i className="bi bi-arrow-counterclockwise me-1"></i>Активировать
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
