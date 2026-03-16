import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import api from '../api';
import useSSE from '../hooks/useSSE';
import { SyncBadge } from '../components/StatusBadge';

export default function ServiceConfigPush() {
  const { serviceId, cfgId } = useParams();
  const [data, setData] = useState(null);
  const [pushing, setPushing] = useState(false);
  const [taskUrl, setTaskUrl] = useState(null);
  const [results, setResults] = useState([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api.cfgPushData(serviceId, cfgId).then(setData).catch(() => {});
  }, [serviceId, cfgId]);

  const doPush = async (force) => {
    setPushing(true);
    setResults([]);
    setDone(false);
    try {
      const r = await api.cfgPush(serviceId, cfgId, { force });
      setTaskUrl(api.taskStreamUrl(r.task_id));
    } catch { setPushing(false); }
  };

  const onSSE = useCallback((ev, es) => {
    if (ev.type === 'instance_done') {
      setResults(prev => [...prev, ev]);
    } else if (ev.type === 'done_all') {
      es.close();
      setPushing(false);
      setDone(true);
    }
  }, []);

  useSSE(taskUrl, onSSE);

  if (!data) return <div className="text-center py-5"><div className="spinner-border"></div></div>;

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-cloud-upload me-2"></i>
          Push: <span className="font-monospace">{data.filename}</span>
          {data.env_label && <span className="badge bg-primary ms-2">{data.env_label}</span>}
        </h4>
        <Link to={`/services/${serviceId}/configs`} className="btn btn-outline-secondary">
          <i className="bi bi-arrow-left me-1"></i>Назад
        </Link>
      </div>

      {data.current_version && (
        <div className="alert alert-info py-2">
          Текущая версия: <strong>v{data.current_version}</strong>
        </div>
      )}

      <table className="table table-sm mb-3">
        <thead><tr><th>Экземпляр</th><th>Сервер</th><th>Статус</th></tr></thead>
        <tbody>
          {data.instances?.map(inst => (
            <tr key={inst.id}>
              <td className="font-monospace">{inst.win_name}</td>
              <td>{inst.hostname}</td>
              <td><SyncBadge status={inst.status} size="sm" /></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="d-flex gap-2 mb-3">
        <button className="btn btn-success" onClick={() => doPush(false)} disabled={pushing}>
          <i className="bi bi-cloud-upload me-1"></i>Push
        </button>
        <button className="btn btn-warning" onClick={() => doPush(true)} disabled={pushing}>
          <i className="bi bi-cloud-upload me-1"></i>Force Push
        </button>
        {pushing && <div className="spinner-border spinner-border-sm text-info mt-2"></div>}
      </div>

      {results.length > 0 && (
        <div className="list-group">
          {results.map((r, i) => (
            <div key={i} className={`list-group-item py-1 d-flex gap-2 align-items-center`}>
              <span className={`badge ${r.ok ? 'bg-success' : r.skipped ? 'bg-secondary' : 'bg-danger'}`}>
                {r.ok ? 'ok' : r.skipped ? 'skip' : 'err'}
              </span>
              <span className="font-monospace">{r.win_name}</span>
              <span className="text-muted small">{r.hostname}</span>
              <span className="text-muted small ms-auto">{r.message}</span>
            </div>
          ))}
        </div>
      )}

      {done && (
        <div className={`alert mt-2 py-2 ${results.every(r => r.ok) ? 'alert-success' : 'alert-warning'}`}>
          Готово: {results.filter(r => r.ok).length} успешно / {results.length} всего
        </div>
      )}
    </div>
  );
}
