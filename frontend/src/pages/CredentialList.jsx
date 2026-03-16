import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import Confirm from '../components/Confirm';

export default function CredentialList() {
  const [creds, setCreds] = useState([]);
  const [delId, setDelId] = useState(null);

  const load = () => api.credList().then(d => setCreds(d.credentials)).catch(() => {});
  useEffect(() => { load(); }, []);

  const doDelete = async () => {
    try { await api.credDelete(delId); } catch {}
    setDelId(null);
    load();
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0"><i className="bi bi-key me-2"></i>Учётные записи</h4>
        <Link to="/credentials/create" className="btn btn-primary">
          <i className="bi bi-plus-lg me-1"></i>Создать
        </Link>
      </div>
      <table className="table table-hover">
        <thead><tr><th>Название</th><th>Имя пользователя</th><th>Серверов</th><th>Обновлено</th><th></th></tr></thead>
        <tbody>
          {creds.map(c => (
            <tr key={c.id}>
              <td className="fw-semibold">{c.name}</td>
              <td><code>{c.username}</code></td>
              <td>{c.server_count}</td>
              <td className="small">{c.updated_at}</td>
              <td className="text-end">
                <Link to={`/credentials/${c.id}/edit`} className="btn btn-sm btn-outline-secondary me-1">
                  <i className="bi bi-pencil"></i>
                </Link>
                <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(c.id)}
                        disabled={c.server_count > 0}>
                  <i className="bi bi-trash"></i>
                </button>
              </td>
            </tr>
          ))}
          {!creds.length && <tr><td colSpan={5} className="text-center text-muted py-4">Нет учётных записей</td></tr>}
        </tbody>
      </table>
      <Confirm show={!!delId} title="Удалить учётную запись?" body="Это действие нельзя отменить."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
