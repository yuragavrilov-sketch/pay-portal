import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import { useEnv } from '../context/EnvContext';

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

      <div className="row g-3">
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
    </div>
  );
}
