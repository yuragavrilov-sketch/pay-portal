import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import Confirm from '../components/Confirm';

export default function EnvironmentList() {
  const [envs, setEnvs] = useState([]);
  const [delId, setDelId] = useState(null);

  const load = () => api.envList().then(d => setEnvs(d.environments)).catch(() => {});
  useEffect(() => { load(); }, []);

  const doDelete = async () => {
    await api.envDelete(delId);
    setDelId(null);
    load();
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0"><i className="bi bi-layers me-2"></i>Окружения</h4>
        <Link to="/environments/create" className="btn btn-primary">
          <i className="bi bi-plus-lg me-1"></i>Создать
        </Link>
      </div>
      <table className="table table-hover">
        <thead><tr><th>Название</th><th>Описание</th><th>Серверов</th><th>Создано</th><th></th></tr></thead>
        <tbody>
          {envs.map(e => (
            <tr key={e.id}>
              <td className="fw-semibold">{e.name}</td>
              <td className="text-muted">{e.description}</td>
              <td>{e.server_count}</td>
              <td className="small">{e.created_at}</td>
              <td className="text-end">
                <Link to={`/environments/${e.id}/edit`} className="btn btn-sm btn-outline-secondary me-1">
                  <i className="bi bi-pencil"></i>
                </Link>
                <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(e.id)}>
                  <i className="bi bi-trash"></i>
                </button>
              </td>
            </tr>
          ))}
          {!envs.length && <tr><td colSpan={5} className="text-center text-muted py-4">Нет окружений</td></tr>}
        </tbody>
      </table>
      <Confirm show={!!delId} title="Удалить окружение?" body="Это действие нельзя отменить."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
