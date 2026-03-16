import React, { useState } from 'react';
import api from '../api';
import Confirm from '../components/Confirm';
import FormModal from '../components/FormModal';
import useFetch from '../hooks/useFetch';

const emptyForm = { name: '', description: '' };

export default function EnvironmentList() {
  const { data, error, loading, reload } = useFetch(() => api.envList());
  const envs = data?.environments || [];
  const [delId, setDelId] = useState(null);
  const [delError, setDelError] = useState('');
  const [editId, setEditId] = useState(null);   // null=closed, 'new'=create, number=edit
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState('');

  const openCreate = () => { setForm({ ...emptyForm }); setFormError(''); setEditId('new'); };
  const openEdit = async (id) => {
    setFormError('');
    try {
      const d = await api.envGet(id);
      setForm({ name: d.name, description: d.description || '' });
      setEditId(id);
    } catch (e) { setFormError(e.message); }
  };

  const saveForm = async () => {
    setFormError('');
    try {
      if (editId === 'new') await api.envCreate(form);
      else await api.envUpdate(editId, form);
      setEditId(null);
      reload();
    } catch (e) { setFormError(e.message); }
  };

  const doDelete = async () => {
    setDelError('');
    try {
      await api.envDelete(delId);
      setDelId(null);
      reload();
    } catch (e) { setDelError(e.message); }
  };

  if (loading) return <div className="text-center py-5"><div className="spinner-border"></div></div>;
  if (error) return <div className="alert alert-danger"><i className="bi bi-exclamation-triangle me-2"></i>{error}</div>;

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0"><i className="bi bi-layers me-2"></i>Окружения</h4>
        <button className="btn btn-primary" onClick={openCreate}>
          <i className="bi bi-plus-lg me-1"></i>Создать
        </button>
      </div>

      {delError && <div className="alert alert-danger">{delError}</div>}

      <div className="card">
        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0">
            <thead className="table-light">
              <tr><th>Название</th><th>Описание</th><th>Серверов</th><th>Создано</th><th style={{width:100}}></th></tr>
            </thead>
            <tbody>
              {envs.map(e => (
                <tr key={e.id}>
                  <td className="fw-semibold">{e.name}</td>
                  <td className="text-muted small">{e.description}</td>
                  <td>{e.server_count}</td>
                  <td className="small text-muted">{e.created_at}</td>
                  <td className="text-end">
                    <button className="btn btn-sm btn-outline-secondary me-1" onClick={() => openEdit(e.id)}>
                      <i className="bi bi-pencil"></i>
                    </button>
                    <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(e.id)}>
                      <i className="bi bi-trash"></i>
                    </button>
                  </td>
                </tr>
              ))}
              {!envs.length && <tr><td colSpan={5} className="text-center text-muted py-4">Нет окружений</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <FormModal show={editId !== null} title={editId === 'new' ? 'Создать окружение' : 'Редактировать окружение'}
                 icon="bi-layers" onClose={() => setEditId(null)} onSubmit={saveForm} error={formError}>
        <div className="mb-3">
          <label className="form-label">Название</label>
          <input className="form-control" value={form.name} required autoFocus
                 onChange={e => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Описание</label>
          <input className="form-control" value={form.description}
                 onChange={e => setForm({ ...form, description: e.target.value })} />
        </div>
      </FormModal>

      <Confirm show={!!delId} title="Удалить окружение?" body="Это действие нельзя отменить."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
