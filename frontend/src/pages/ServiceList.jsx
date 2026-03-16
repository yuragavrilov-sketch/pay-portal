import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import Confirm from '../components/Confirm';

export default function ServiceList() {
  const [services, setServices] = useState([]);
  const [delId, setDelId] = useState(null);

  const load = () => api.svcList().then(d => setServices(d.services)).catch(() => {});
  useEffect(() => { load(); }, []);

  const doDelete = async () => {
    await api.svcDelete(delId);
    setDelId(null);
    load();
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0"><i className="bi bi-diagram-3 me-2"></i>Каталог сервисов</h4>
        <Link to="/services/create" className="btn btn-primary">
          <i className="bi bi-plus-lg me-1"></i>Создать
        </Link>
      </div>
      <table className="table table-hover">
        <thead><tr><th>Системное имя</th><th>Отображаемое имя</th><th>Описание</th><th>Экземпляров</th><th>Конфигов</th><th></th></tr></thead>
        <tbody>
          {services.map(s => (
            <tr key={s.id}>
              <td className="fw-semibold"><code>{s.name}</code></td>
              <td>{s.display_name}</td>
              <td className="text-muted small">{s.description}</td>
              <td>
                <Link to="/instances" className="text-decoration-none">{s.instance_count}</Link>
              </td>
              <td>
                <Link to={`/services/${s.id}/configs`} className="btn btn-sm btn-outline-info">
                  <i className="bi bi-file-earmark-code me-1"></i>{s.config_count}
                </Link>
              </td>
              <td className="text-end">
                <Link to={`/services/${s.id}/edit`} className="btn btn-sm btn-outline-secondary me-1">
                  <i className="bi bi-pencil"></i>
                </Link>
                <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(s.id)}>
                  <i className="bi bi-trash"></i>
                </button>
              </td>
            </tr>
          ))}
          {!services.length && <tr><td colSpan={6} className="text-center text-muted py-4">Нет сервисов</td></tr>}
        </tbody>
      </table>
      <Confirm show={!!delId} title="Удалить сервис?" body="Все экземпляры и конфиги сервиса будут удалены."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
