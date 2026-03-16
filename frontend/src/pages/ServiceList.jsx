import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import Confirm from '../components/Confirm';
import useFetch from '../hooks/useFetch';

export default function ServiceList() {
  const { data, error, loading, reload } = useFetch(() => api.svcList());
  const services = data?.services || [];
  const [delId, setDelId] = useState(null);
  const [delError, setDelError] = useState('');

  const doDelete = async () => {
    setDelError('');
    try {
      await api.svcDelete(delId);
      setDelId(null);
      reload();
    } catch (e) {
      setDelError(e.message);
    }
  };

  if (loading) return <div className="text-center py-5"><div className="spinner-border"></div></div>;
  if (error) return <div className="alert alert-danger"><i className="bi bi-exclamation-triangle me-2"></i>{error}</div>;

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0"><i className="bi bi-diagram-3 me-2"></i>Каталог сервисов</h4>
        <Link to="/services/create" className="btn btn-primary">
          <i className="bi bi-plus-lg me-1"></i>Создать
        </Link>
      </div>
      {delError && <div className="alert alert-danger">{delError}</div>}
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
                <Link to={`/instances/create?serviceId=${s.id}`} className="btn btn-sm btn-outline-success me-1" title="Добавить экземпляры">
                  <i className="bi bi-plus-lg"></i>
                </Link>
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
