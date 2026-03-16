import React from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useEnv } from '../context/EnvContext';
import { useAuth } from '../context/AuthContext';

const links = [
  { to: '/',             icon: 'bi-speedometer2',     label: 'Дашборд' },
  { to: '/manage',       icon: 'bi-toggles',          label: 'Управление' },
  { to: '/instances',    icon: 'bi-hdd-rack',         label: 'Экземпляры' },
  { to: '/services',     icon: 'bi-diagram-3',        label: 'Сервисы' },
  { to: '/servers',      icon: 'bi-server',           label: 'Серверы' },
  { to: '/environments', icon: 'bi-layers',           label: 'Окружения' },
  { to: '/credentials',  icon: 'bi-key',              label: 'Учётные записи' },
  { to: '/audit',        icon: 'bi-journal-text',     label: 'Журнал' },
];

export default function Layout() {
  const { currentEnv, environments, selectEnv, clearEnv } = useEnv();
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <div className="d-flex">
      {/* Sidebar */}
      <div className="sidebar d-flex flex-column py-3">
        <div className="px-3 mb-3">
          <h6 className="text-uppercase text-secondary small mb-2">
            <i className="bi bi-gear-wide-connected me-1"></i> SvcMgr
          </h6>

          {/* Env selector */}
          <div className="dropdown">
            <button
              className={`btn btn-sm w-100 text-start ${currentEnv ? 'btn-primary' : 'btn-outline-secondary'}`}
              data-bs-toggle="dropdown"
            >
              <i className="bi bi-layers me-1"></i>
              {currentEnv ? currentEnv.name : 'Все окружения'}
              <i className="bi bi-chevron-down float-end mt-1" style={{ fontSize: '.7rem' }}></i>
            </button>
            <ul className="dropdown-menu dropdown-menu-dark">
              <li>
                <button className="dropdown-item" onClick={() => { clearEnv(); navigate('/'); }}>
                  Все окружения
                </button>
              </li>
              <li><hr className="dropdown-divider" /></li>
              {environments.map(e => (
                <li key={e.id}>
                  <button
                    className={`dropdown-item ${currentEnv?.id === e.id ? 'active' : ''}`}
                    onClick={() => { selectEnv(e.id); navigate('/'); }}
                  >
                    {e.name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <nav className="nav flex-column flex-grow-1">
          {links.map(l => (
            <NavLink key={l.to} to={l.to} end={l.to === '/'} className="nav-link">
              <i className={`bi ${l.icon} me-2`}></i>{l.label}
            </NavLink>
          ))}
        </nav>

        {/* User info & logout */}
        <div className="px-3 pt-3 mt-auto border-top border-secondary">
          <div className="d-flex align-items-center">
            <div className="flex-grow-1 overflow-hidden">
              <div className="small text-light text-truncate">
                <i className="bi bi-person-circle me-1"></i>
                {user?.name || user?.username || 'User'}
              </div>
              {user?.email && (
                <div className="text-secondary" style={{ fontSize: '.7rem' }}>
                  {user.email}
                </div>
              )}
            </div>
            <button
              className="btn btn-sm btn-outline-secondary ms-2"
              onClick={handleLogout}
              title="Выйти"
            >
              <i className="bi bi-box-arrow-right"></i>
            </button>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="main-content p-4">
        <Outlet />
      </div>
    </div>
  );
}
