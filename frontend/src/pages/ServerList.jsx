import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import { useEnv } from '../context/EnvContext';
import Confirm from '../components/Confirm';

export default function ServerList() {
  const { currentEnv } = useEnv();
  const [servers, setServers] = useState([]);
  const [delId, setDelId] = useState(null);
  const [testing, setTesting] = useState({});

  const load = () => api.serverList().then(d => setServers(d.servers)).catch(() => {});
  useEffect(() => { load(); }, [currentEnv]);

  const doDelete = async () => {
    await api.serverDelete(delId);
    setDelId(null);
    load();
  };

  const testConn = async (id) => {
    setTesting(p => ({ ...p, [id]: true }));
    try {
      const r = await api.serverTest(id);
      setServers(prev => prev.map(s => s.id === id ? { ...s, is_available: r.ok, test_msg: r.message } : s));
    } catch {}
    setTesting(p => ({ ...p, [id]: false }));
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-server me-2"></i>Серверы
          {currentEnv && <span className="badge bg-primary fs-6 ms-2">{currentEnv.name}</span>}
        </h4>
        <Link to="/servers/create" className="btn btn-primary">
          <i className="bi bi-plus-lg me-1"></i>Добавить
        </Link>
      </div>
      <table className="table table-hover">
        <thead><tr><th>Хост</th><th>Окружения</th><th>Учётная запись</th><th>Порт</th><th>WinRM</th><th>Экземпляров</th><th></th></tr></thead>
        <tbody>
          {servers.map(s => (
            <tr key={s.id}>
              <td className="fw-semibold font-monospace">{s.hostname}</td>
              <td>{s.environments?.map(e => <span key={e.id} className="badge bg-secondary me-1">{e.name}</span>)}</td>
              <td>{s.credential_name}</td>
              <td>{s.port}{s.use_ssl && <span className="badge bg-info ms-1">SSL</span>}</td>
              <td>
                {s.is_available === true && <span className="badge bg-success">ok</span>}
                {s.is_available === false && <span className="badge bg-danger" title={s.test_msg}>fail</span>}
                {s.is_available === null && <span className="badge bg-secondary">?</span>}
                <button className="btn btn-sm btn-link p-0 ms-1" onClick={() => testConn(s.id)} disabled={testing[s.id]}>
                  <i className={`bi bi-arrow-clockwise ${testing[s.id] ? 'spin' : ''}`}></i>
                </button>
              </td>
              <td>{s.instance_count}</td>
              <td className="text-end">
                <Link to={`/servers/${s.id}/edit`} className="btn btn-sm btn-outline-secondary me-1">
                  <i className="bi bi-pencil"></i>
                </Link>
                <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(s.id)}>
                  <i className="bi bi-trash"></i>
                </button>
              </td>
            </tr>
          ))}
          {!servers.length && <tr><td colSpan={7} className="text-center text-muted py-4">Нет серверов</td></tr>}
        </tbody>
      </table>
      <Confirm show={!!delId} title="Удалить сервер?" body="Все экземпляры на сервере будут удалены."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
