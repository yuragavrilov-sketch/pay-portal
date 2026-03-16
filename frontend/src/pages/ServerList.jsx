import React, { useEffect, useState } from 'react';
import api from '../api';
import { useEnv } from '../context/EnvContext';
import Confirm from '../components/Confirm';
import FormModal from '../components/FormModal';

const emptyForm = { hostname: '', port: 5985, use_ssl: false, credential_id: '', env_ids: [], description: '' };

export default function ServerList() {
  const { currentEnv } = useEnv();
  const [servers, setServers] = useState([]);
  const [delId, setDelId] = useState(null);
  const [testing, setTesting] = useState({});

  // Form modal
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState('');
  const [envs, setEnvs] = useState([]);
  const [creds, setCreds] = useState([]);

  const load = () => api.serverList().then(d => setServers(d.servers)).catch(() => {});
  useEffect(() => { load(); }, [currentEnv]);

  const loadFormData = () => {
    api.envList().then(d => setEnvs(d.environments));
    api.credList().then(d => setCreds(d.credentials));
  };

  const openCreate = () => {
    loadFormData();
    setForm({ ...emptyForm });
    setFormError('');
    setEditId('new');
  };
  const openEdit = async (id) => {
    loadFormData();
    setFormError('');
    try {
      const d = await api.serverGet(id);
      setForm({
        hostname: d.hostname, port: d.port, use_ssl: d.use_ssl,
        credential_id: String(d.credential_id ?? ''), env_ids: d.env_ids || [], description: d.description || '',
      });
      setEditId(id);
    } catch (e) { setFormError(e.message); }
  };

  const saveForm = async () => {
    setFormError('');
    try {
      const payload = { ...form, credential_id: parseInt(form.credential_id, 10) };
      if (editId === 'new') await api.serverCreate(payload);
      else await api.serverUpdate(editId, payload);
      setEditId(null);
      load();
    } catch (e) { setFormError(e.message); }
  };

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

  const toggleEnv = (eid) => {
    setForm(f => ({
      ...f,
      env_ids: f.env_ids.includes(eid) ? f.env_ids.filter(x => x !== eid) : [...f.env_ids, eid],
    }));
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-server me-2"></i>Серверы
          {currentEnv && <span className="badge bg-primary fs-6 ms-2">{currentEnv.name}</span>}
        </h4>
        <button className="btn btn-primary" onClick={openCreate}>
          <i className="bi bi-plus-lg me-1"></i>Добавить
        </button>
      </div>

      <div className="card">
        <div className="table-responsive">
          <table className="table table-hover align-middle mb-0">
            <thead className="table-light">
              <tr><th>Хост</th><th>Окружения</th><th>Учётная запись</th><th>Порт</th><th>WinRM</th><th>Экземпляров</th><th style={{width:100}}></th></tr>
            </thead>
            <tbody>
              {servers.map(s => (
                <tr key={s.id}>
                  <td className="fw-semibold font-monospace">{s.hostname}</td>
                  <td>{s.environments?.map(e => <span key={e.id} className="badge bg-secondary me-1">{e.name}</span>)}</td>
                  <td className="small">{s.credential_name}</td>
                  <td className="small">{s.port}{s.use_ssl && <span className="badge bg-info ms-1">SSL</span>}</td>
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
                    <button className="btn btn-sm btn-outline-secondary me-1" onClick={() => openEdit(s.id)}>
                      <i className="bi bi-pencil"></i>
                    </button>
                    <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(s.id)}>
                      <i className="bi bi-trash"></i>
                    </button>
                  </td>
                </tr>
              ))}
              {!servers.length && <tr><td colSpan={7} className="text-center text-muted py-4">Нет серверов</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <FormModal show={editId !== null} title={editId === 'new' ? 'Добавить сервер' : 'Редактировать сервер'}
                 icon="bi-server" onClose={() => setEditId(null)} onSubmit={saveForm} error={formError}>
        <div className="mb-3">
          <label className="form-label">Hostname</label>
          <input className="form-control" value={form.hostname} required autoFocus
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
            <option value="">-- выберите --</option>
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
      </FormModal>

      <Confirm show={!!delId} title="Удалить сервер?" body="Все экземпляры на сервере будут удалены."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
