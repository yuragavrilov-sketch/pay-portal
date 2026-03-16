import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api';

export default function ServerForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEdit = !!id;
  const [form, setForm] = useState({ hostname: '', port: 5985, use_ssl: false, credential_id: '', env_ids: [], description: '' });
  const [envs, setEnvs] = useState([]);
  const [creds, setCreds] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    api.envList().then(d => setEnvs(d.environments));
    api.credList().then(d => setCreds(d.credentials));
    if (isEdit) {
      api.serverGet(id).then(d => setForm({
        hostname: d.hostname, port: d.port, use_ssl: d.use_ssl,
        credential_id: String(d.credential_id ?? ''), env_ids: d.env_ids || [], description: d.description || '',
      })).catch(e => setError(e.message));
    }
  }, [id]);

  const toggleEnv = (eid) => {
    setForm(f => ({
      ...f,
      env_ids: f.env_ids.includes(eid) ? f.env_ids.filter(x => x !== eid) : [...f.env_ids, eid],
    }));
  };

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const payload = { ...form, credential_id: parseInt(form.credential_id, 10) };
      if (isEdit) await api.serverUpdate(id, payload);
      else await api.serverCreate(payload);
      navigate('/servers');
    } catch (err) { setError(err.message); }
  };

  return (
    <div style={{ maxWidth: 600 }}>
      <h4 className="mb-3"><i className="bi bi-server me-2"></i>{isEdit ? 'Редактировать' : 'Добавить'} сервер</h4>
      {error && <div className="alert alert-danger">{error}</div>}
      <form onSubmit={submit}>
        <div className="mb-3">
          <label className="form-label">Hostname</label>
          <input className="form-control" value={form.hostname} required
                 onChange={e => setForm({ ...form, hostname: e.target.value })} />
        </div>
        <div className="row mb-3">
          <div className="col">
            <label className="form-label">Порт WinRM</label>
            <input className="form-control" type="number" value={form.port}
                   onChange={e => setForm({ ...form, port: parseInt(e.target.value, 10) || 5985 })} />
          </div>
          <div className="col d-flex align-items-end">
            <div className="form-check">
              <input className="form-check-input" type="checkbox" checked={form.use_ssl}
                     onChange={e => setForm({ ...form, use_ssl: e.target.checked })} id="sslCheck" />
              <label className="form-check-label" htmlFor="sslCheck">SSL</label>
            </div>
          </div>
        </div>
        <div className="mb-3">
          <label className="form-label">Учётная запись</label>
          <select className="form-select" value={form.credential_id} required
                  onChange={e => setForm({ ...form, credential_id: e.target.value })}>
            <option value="">— выберите —</option>
            {creds.map(c => <option key={c.id} value={c.id}>{c.name} ({c.username})</option>)}
          </select>
        </div>
        <div className="mb-3">
          <label className="form-label">Окружения</label>
          <div className="d-flex flex-wrap gap-2">
            {envs.map(env => (
              <div key={env.id} className="form-check">
                <input className="form-check-input" type="checkbox"
                       checked={form.env_ids.includes(env.id)}
                       onChange={() => toggleEnv(env.id)} id={`env-${env.id}`} />
                <label className="form-check-label" htmlFor={`env-${env.id}`}>{env.name}</label>
              </div>
            ))}
          </div>
        </div>
        <div className="mb-3">
          <label className="form-label">Описание</label>
          <input className="form-control" value={form.description}
                 onChange={e => setForm({ ...form, description: e.target.value })} />
        </div>
        <button className="btn btn-primary me-2" type="submit"><i className="bi bi-check-lg me-1"></i>Сохранить</button>
        <button className="btn btn-secondary" type="button" onClick={() => navigate('/servers')}>Отмена</button>
      </form>
    </div>
  );
}
