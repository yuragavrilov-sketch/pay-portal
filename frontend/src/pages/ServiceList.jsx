import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import Confirm from '../components/Confirm';
import FormModal from '../components/FormModal';
import useFetch from '../hooks/useFetch';

const emptyForm = { name: '', display_name: '', description: '' };

export default function ServiceList() {
  const { data, error, loading, reload } = useFetch(() => api.svcList());
  const services = data?.services || [];
  const [delId, setDelId] = useState(null);
  const [delError, setDelError] = useState('');
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState('');

  const openCreate = () => { setForm({ ...emptyForm }); setFormError(''); setEditId('new'); };
  const openEdit = async (id) => {
    setFormError('');
    try {
      const d = await api.svcGet(id);
      setForm({ name: d.name, display_name: d.display_name || '', description: d.description || '' });
      setEditId(id);
    } catch (e) { setFormError(e.message); }
  };

  const saveForm = async () => {
    setFormError('');
    try {
      if (editId === 'new') await api.svcCreate(form);
      else await api.svcUpdate(editId, form);
      setEditId(null);
      reload();
    } catch (e) { setFormError(e.message); }
  };

  const doDelete = async () => {
    setDelError('');
    try {
      await api.svcDelete(delId);
      setDelId(null);
      reload();
    } catch (e) { setDelError(e.message); }
  };

  if (loading) return <div className="text-center py-5"><div className="spinner-border"></div></div>;
  if (error) return <div className="alert alert-danger"><i className="bi bi-exclamation-triangle me-2"></i>{error}</div>;

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0"><i className="bi bi-diagram-3 me-2"></i>Каталог сервисов</h4>
        <button className="btn btn-primary" onClick={openCreate}>
          <i className="bi bi-plus-lg me-1"></i>Создать
        </button>
      </div>

      {delError && <div className="alert alert-danger">{delError}</div>}

      <div className="card">
        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0">
            <thead className="table-light">
              <tr><th>Системное имя</th><th>Отображаемое имя</th><th>Описание</th><th>Экземпляров</th><th>Конфигов</th><th style={{width:140}}></th></tr>
            </thead>
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
                    <button className="btn btn-sm btn-outline-secondary me-1" onClick={() => openEdit(s.id)}>
                      <i className="bi bi-pencil"></i>
                    </button>
                    <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(s.id)}>
                      <i className="bi bi-trash"></i>
                    </button>
                  </td>
                </tr>
              ))}
              {!services.length && <tr><td colSpan={6} className="text-center text-muted py-4">Нет сервисов</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <FormModal show={editId !== null} title={editId === 'new' ? 'Создать сервис' : 'Редактировать сервис'}
                 icon="bi-diagram-3" onClose={() => setEditId(null)} onSubmit={saveForm} error={formError}>
        <div className="mb-3">
          <label className="form-label">Системное имя</label>
          <input className="form-control" value={form.name} required autoFocus
                 onChange={e => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Отображаемое имя</label>
          <input className="form-control" value={form.display_name}
                 onChange={e => setForm({ ...form, display_name: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Описание</label>
          <textarea className="form-control" rows={3} value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })} />
        </div>
      </FormModal>

      <Confirm show={!!delId} title="Удалить сервис?" body="Все экземпляры и конфиги сервиса будут удалены."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
