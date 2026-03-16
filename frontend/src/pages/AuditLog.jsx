import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '../api';

const ACTIONS = [
  'create', 'update', 'delete', 'test_connection', 'refresh_status',
  'refresh_configs', 'start', 'stop', 'restart', 'snapshot', 'push_config', 'rollback_config',
];
const ENTITIES = ['environment', 'credential', 'server', 'service', 'instance', 'config', 'snapshot'];
const RESULTS = ['ok', 'warning', 'error'];

export default function AuditLog() {
  const [sp, setSp] = useSearchParams();
  const [data, setData] = useState(null);

  const page = parseInt(sp.get('page') || '1');
  const action = sp.get('action') || '';
  const entity = sp.get('entity') || '';
  const result = sp.get('result') || '';
  const search = sp.get('q') || '';

  const load = () => {
    api.auditList({ page, action, entity, result, q: search }).then(setData).catch(() => {});
  };
  useEffect(() => { load(); }, [page, action, entity, result, search]);

  const setFilter = (key, val) => {
    if (val) sp.set(key, val); else sp.delete(key);
    sp.set('page', '1');
    setSp(sp);
  };

  const resultBadge = (r) => {
    if (r === 'ok') return <span className="badge bg-success">ok</span>;
    if (r === 'warning') return <span className="badge bg-warning text-dark">warning</span>;
    return <span className="badge bg-danger">error</span>;
  };

  return (
    <div>
      <h4 className="mb-3"><i className="bi bi-journal-text me-2"></i>Журнал аудита</h4>

      <div className="row g-2 mb-3">
        <div className="col-auto">
          <select className="form-select form-select-sm" value={action} onChange={e => setFilter('action', e.target.value)}>
            <option value="">Все действия</option>
            {ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <select className="form-select form-select-sm" value={entity} onChange={e => setFilter('entity', e.target.value)}>
            <option value="">Все сущности</option>
            {ENTITIES.map(e => <option key={e} value={e}>{e}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <select className="form-select form-select-sm" value={result} onChange={e => setFilter('result', e.target.value)}>
            <option value="">Все результаты</option>
            {RESULTS.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <input className="form-control form-control-sm" placeholder="Поиск..." value={search}
                 onChange={e => setFilter('q', e.target.value)} />
        </div>
      </div>

      {data === null ? (
        <div className="text-center py-5"><div className="spinner-border"></div></div>
      ) : (
        <>
          <table className="table table-sm table-hover">
            <thead><tr><th>Время</th><th>Действие</th><th>Сущность</th><th>Имя</th><th>Результат</th><th>IP</th><th>Детали</th></tr></thead>
            <tbody>
              {data.items?.map(row => (
                <tr key={row.id}>
                  <td className="small text-nowrap">{row.created_at}</td>
                  <td><span className="badge bg-secondary">{row.action}</span></td>
                  <td>{row.entity_type}</td>
                  <td className="font-monospace small">{row.entity_name}</td>
                  <td>{resultBadge(row.result)}</td>
                  <td className="small">{row.ip_address}</td>
                  <td className="small text-truncate" style={{ maxWidth: 300 }} title={row.details}>{row.details}</td>
                </tr>
              ))}
              {!data.items?.length && <tr><td colSpan={7} className="text-center text-muted py-4">Нет записей</td></tr>}
            </tbody>
          </table>

          {/* Pagination */}
          {data.pages > 1 && (
            <nav>
              <ul className="pagination pagination-sm">
                {Array.from({ length: data.pages }, (_, i) => i + 1).map(p => (
                  <li key={p} className={`page-item ${p === page ? 'active' : ''}`}>
                    <button className="page-link" onClick={() => { sp.set('page', p); setSp(sp); }}>{p}</button>
                  </li>
                ))}
              </ul>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
