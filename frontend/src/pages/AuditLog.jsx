import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '../api';

/* ---------- dictionaries ---------- */

const ACTION_LABELS = {
  create:          'Создание',
  update:          'Изменение',
  delete:          'Удаление',
  test_connection: 'Тест связи',
  refresh_status:  'Обн. статуса',
  refresh_configs: 'Обн. конфигов',
  start:           'Запуск',
  stop:            'Остановка',
  restart:         'Рестарт',
  snapshot:        'Снэпшот',
  push_config:     'Деплой конфига',
  rollback_config: 'Откат конфига',
};

const ENTITY_LABELS = {
  environment: 'Окружение',
  credential:  'Учётная запись',
  server:      'Сервер',
  service:     'Сервис',
  instance:    'Экземпляр',
  config:      'Конфиг',
  snapshot:    'Снэпшот',
};

const ACTION_COLORS = {
  create:          'bg-success',
  update:          'bg-info text-dark',
  delete:          'bg-danger',
  test_connection: 'bg-secondary',
  refresh_status:  'bg-secondary',
  refresh_configs: 'bg-secondary',
  start:           'bg-success',
  stop:            'bg-danger',
  restart:         'bg-warning text-dark',
  snapshot:        'bg-dark',
  push_config:     'bg-primary',
  rollback_config: 'bg-warning text-dark',
};

const ACTIONS  = Object.keys(ACTION_LABELS);
const ENTITIES = Object.keys(ENTITY_LABELS);
const RESULTS  = ['ok', 'warning', 'error'];

/* ---------- component ---------- */

export default function AuditLog() {
  const [sp, setSp] = useSearchParams();
  const [data, setData] = useState(null);

  const page     = parseInt(sp.get('page') || '1');
  const action   = sp.get('action') || '';
  const entity   = sp.get('entity') || '';
  const result   = sp.get('result') || '';
  const username = sp.get('username') || '';
  const search   = sp.get('q') || '';

  const load = () => {
    api.auditList({ page, action, entity, result, username, q: search })
      .then(setData).catch(() => {});
  };
  useEffect(() => { load(); }, [page, action, entity, result, username, search]);

  const setFilter = (key, val) => {
    if (val) sp.set(key, val); else sp.delete(key);
    sp.set('page', '1');
    setSp(sp);
  };

  /* badges */
  const resultBadge = (r) => {
    if (r === 'ok')      return <span className="badge bg-success">OK</span>;
    if (r === 'warning') return <span className="badge bg-warning text-dark">Warning</span>;
    return <span className="badge bg-danger">Error</span>;
  };

  const actionBadge = (a) => (
    <span className={`badge ${ACTION_COLORS[a] || 'bg-secondary'}`}>
      {ACTION_LABELS[a] || a}
    </span>
  );

  /* pagination with ellipsis */
  const renderPagination = () => {
    if (!data || data.pages <= 1) return null;
    const pages = [];
    const total = data.pages;
    const cur = data.page;

    const addPage = (p) => {
      if (p < 1 || p > total) return;
      if (pages.length && pages[pages.length - 1] === '...') {
        if (pages[pages.length - 2] === p) return;
      }
      if (pages.includes(p)) return;
      pages.push(p);
    };

    addPage(1);
    if (cur > 3) pages.push('...');
    for (let i = Math.max(2, cur - 1); i <= Math.min(total - 1, cur + 1); i++) addPage(i);
    if (cur < total - 2) pages.push('...');
    if (total > 1) addPage(total);

    return (
      <nav className="d-flex align-items-center gap-3">
        <ul className="pagination pagination-sm mb-0">
          <li className={`page-item ${cur === 1 ? 'disabled' : ''}`}>
            <button className="page-link" onClick={() => { sp.set('page', cur - 1); setSp(sp); }}>&laquo;</button>
          </li>
          {pages.map((p, i) =>
            p === '...'
              ? <li key={`e${i}`} className="page-item disabled"><span className="page-link">...</span></li>
              : <li key={p} className={`page-item ${p === cur ? 'active' : ''}`}>
                  <button className="page-link" onClick={() => { sp.set('page', p); setSp(sp); }}>{p}</button>
                </li>
          )}
          <li className={`page-item ${cur === total ? 'disabled' : ''}`}>
            <button className="page-link" onClick={() => { sp.set('page', cur + 1); setSp(sp); }}>&raquo;</button>
          </li>
        </ul>
        <span className="text-muted small">Всего: {data.total}</span>
      </nav>
    );
  };

  return (
    <div>
      <h4 className="mb-3"><i className="bi bi-journal-text me-2"></i>Журнал аудита</h4>

      {/* Filters */}
      <div className="row g-2 mb-3">
        <div className="col-auto">
          <select className="form-select form-select-sm" value={action} onChange={e => setFilter('action', e.target.value)}>
            <option value="">Все действия</option>
            {ACTIONS.map(a => <option key={a} value={a}>{ACTION_LABELS[a]}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <select className="form-select form-select-sm" value={entity} onChange={e => setFilter('entity', e.target.value)}>
            <option value="">Все сущности</option>
            {ENTITIES.map(e => <option key={e} value={e}>{ENTITY_LABELS[e]}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <select className="form-select form-select-sm" value={result} onChange={e => setFilter('result', e.target.value)}>
            <option value="">Все результаты</option>
            {RESULTS.map(r => <option key={r} value={r}>{r === 'ok' ? 'OK' : r === 'warning' ? 'Warning' : 'Error'}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <select className="form-select form-select-sm" value={username} onChange={e => setFilter('username', e.target.value)}>
            <option value="">Все пользователи</option>
            {(data?.usernames || []).map(u => <option key={u} value={u}>{u}</option>)}
          </select>
        </div>
        <div className="col-auto">
          <input className="form-control form-control-sm" placeholder="Поиск..." value={search}
                 onChange={e => setFilter('q', e.target.value)} />
        </div>
        {(action || entity || result || username || search) && (
          <div className="col-auto">
            <button className="btn btn-sm btn-outline-secondary" onClick={() => setSp({})}>
              <i className="bi bi-x-lg me-1"></i>Сбросить
            </button>
          </div>
        )}
      </div>

      {data === null ? (
        <div className="text-center py-5"><div className="spinner-border"></div></div>
      ) : (
        <>
          <div className="table-responsive">
            <table className="table table-sm table-hover align-middle">
              <thead className="table-light">
                <tr>
                  <th>Время</th>
                  <th>Пользователь</th>
                  <th>Действие</th>
                  <th>Сущность</th>
                  <th>Имя</th>
                  <th>Результат</th>
                  <th>IP</th>
                  <th>Детали</th>
                </tr>
              </thead>
              <tbody>
                {data.items?.map(row => (
                  <tr key={row.id}>
                    <td className="small text-nowrap">{row.created_at}</td>
                    <td>
                      {row.username
                        ? <span className="badge bg-light text-dark border"><i className="bi bi-person me-1"></i>{row.username}</span>
                        : <span className="text-muted small">—</span>
                      }
                    </td>
                    <td>{actionBadge(row.action)}</td>
                    <td><span className="small">{ENTITY_LABELS[row.entity_type] || row.entity_type}</span></td>
                    <td className="font-monospace small">{row.entity_name}</td>
                    <td>{resultBadge(row.result)}</td>
                    <td className="small">{row.ip_address}</td>
                    <td className="small text-truncate" style={{ maxWidth: 300 }} title={row.details}>{row.details}</td>
                  </tr>
                ))}
                {!data.items?.length && (
                  <tr><td colSpan={8} className="text-center text-muted py-4">Нет записей</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {renderPagination()}
        </>
      )}
    </div>
  );
}
