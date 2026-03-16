import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api';

export default function CredentialForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEdit = !!id;
  const [form, setForm] = useState({ name: '', username: '', password: '', description: '' });
  const [error, setError] = useState('');

  useEffect(() => {
    if (isEdit) api.credGet(id).then(d => setForm({ ...d, password: '' })).catch(e => setError(e.message));
  }, [id]);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    try {
      if (isEdit) await api.credUpdate(id, form);
      else await api.credCreate(form);
      navigate('/credentials');
    } catch (err) { setError(err.message); }
  };

  return (
    <div style={{ maxWidth: 600 }}>
      <h4 className="mb-3">
        <i className="bi bi-key me-2"></i>{isEdit ? 'Редактировать' : 'Создать'} учётную запись
      </h4>
      {error && <div className="alert alert-danger">{error}</div>}
      <form onSubmit={submit}>
        <div className="mb-3">
          <label className="form-label">Название</label>
          <input className="form-control" value={form.name} required
                 onChange={e => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Имя пользователя</label>
          <input className="form-control" value={form.username} required
                 onChange={e => setForm({ ...form, username: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Пароль{isEdit && ' (оставьте пустым, чтобы не менять)'}</label>
          <input className="form-control" type="password" value={form.password}
                 required={!isEdit}
                 onChange={e => setForm({ ...form, password: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Описание</label>
          <input className="form-control" value={form.description || ''}
                 onChange={e => setForm({ ...form, description: e.target.value })} />
        </div>
        <button className="btn btn-primary me-2" type="submit">
          <i className="bi bi-check-lg me-1"></i>Сохранить
        </button>
        <button className="btn btn-secondary" type="button" onClick={() => navigate('/credentials')}>Отмена</button>
      </form>
    </div>
  );
}
