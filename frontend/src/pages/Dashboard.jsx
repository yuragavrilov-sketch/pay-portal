import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import { useEnv } from '../context/EnvContext';

const ACTION_LABELS = {
  create: 'Создание', update: 'Изменение', delete: 'Удаление',
  test_connection: 'Тест связи', refresh_status: 'Обн. статуса',
  refresh_configs: 'Обн. конфигов', start: 'Запуск', stop: 'Остановка',
  restart: 'Рестарт', snapshot: 'Снэпшот', push_config: 'Деплой',
  rollback_config: 'Откат',
};

const ACTION_COLORS = {
  create: 'bg-success', update: 'bg-info text-dark', delete: 'bg-danger',
  start: 'bg-success', stop: 'bg-danger', restart: 'bg-warning text-dark',
  push_config: 'bg-primary', rollback_config: 'bg-warning text-dark',
};

export default function Dashboard() {
  const { currentEnv } = useEnv();
  const [stats, setStats] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setError('');
    api.dashboard().then(setStats).catch(e => setError(e.message));
  }, [currentEnv]);

  if (error) return <div className="alert alert-danger mt-3"><i className="bi bi-exclamation-triangle me-2"></i>{error}</div>;
  if (!stats) return <div className="text-center py-5"><div className="spinner-border"></div></div>;

  const cards = [
    { label: 'Окружения',      value: stats.env_count,      icon: 'bi-layers',        to: '/environments', color: 'primary' },
    { label: 'Серверы',         value: stats.server_count,   icon: 'bi-server',        to: '/servers',      color: 'success' },
    { label: 'Экземпляры',     value: stats.instance_count, icon: 'bi-hdd-rack',      to: '/instances',    color: 'info' },
    { label: 'Сервисы',        value: stats.service_count,  icon: 'bi-diagram-3',     to: '/services',     color: 'warning' },
    { label: 'Учётные записи', value: stats.cred_count,     icon: 'bi-key',           to: '/credentials',  color: 'secondary' },
  ];

  return (
    <div>
      <h4 className="mb-3">
        <i className="bi bi-speedometer2 me-2"></i>Дашборд
        {currentEnv && <span className="badge bg-primary fs-6 ms-2">{currentEnv.name}</span>}
      </h4>

      <div className="row g-3 mb-4">
        {cards.map(c => (
          <div className="col-md-4 col-lg" key={c.label}>
            <Link to={c.to} className="text-decoration-none">
              <div className={`card border-${c.color} h-100`}>
                <div className="card-body d-flex align-items-center gap-3">
                  <i className={`bi ${c.icon} fs-2 text-${c.color}`}></i>
                  <div>
                    <div className="fs-3 fw-bold">{c.value}</div>
                    <div className="text-muted small">{c.label}</div>
                  </div>
                </div>
              </div>
            </Link>
          </div>
        ))}
      </div>

      <div className="row g-3 mb-4">
        <div className="col-md-6">
          <Link to="/manage" className="btn btn-lg btn-outline-primary w-100 py-3">
            <i className="bi bi-toggles me-2"></i>Управление сервисами
          </Link>
        </div>
        <div className="col-md-6">
          <Link to="/audit" className="btn btn-lg btn-outline-secondary w-100 py-3">
            <i className="bi bi-journal-text me-2"></i>Журнал аудита
          </Link>
        </div>
      </div>

      {/* Recent activity */}
      {stats.recent_audit?.length > 0 && (
        <div className="card">
          <div className="card-header d-flex align-items-center justify-content-between">
            <span><i className="bi bi-clock-history me-2"></i>Последние действия</span>
            <Link to="/audit" className="btn btn-sm btn-outline-secondary">Все записи</Link>
          </div>
          <div className="card-body p-0">
            <table className="table table-sm table-hover mb-0">
              <tbody>
                {stats.recent_audit.map(row => (
                  <tr key={row.id}>
                    <td className="small text-muted text-nowrap" style={{ width: 130 }}>{row.created_at}</td>
                    <td style={{ width: 120 }}>
                      {row.username
                        ? <span className="small"><i className="bi bi-person me-1"></i>{row.username}</span>
                        : <span className="text-muted small">—</span>
                      }
                    </td>
                    <td style={{ width: 130 }}>
                      <span className={`badge ${ACTION_COLORS[row.action] || 'bg-secondary'}`} style={{ fontSize: '.7rem' }}>
                        {ACTION_LABELS[row.action] || row.action}
                      </span>
                    </td>
                    <td className="font-monospace small">{row.entity_name}</td>
                    <td style={{ width: 60 }}>
                      {row.result === 'ok'
                        ? <span className="badge bg-success" style={{ fontSize: '.65rem' }}>OK</span>
                        : row.result === 'warning'
                          ? <span className="badge bg-warning text-dark" style={{ fontSize: '.65rem' }}>!</span>
                          : <span className="badge bg-danger" style={{ fontSize: '.65rem' }}>ERR</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
