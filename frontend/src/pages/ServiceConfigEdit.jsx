import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../api';

export default function ServiceConfigEdit() {
  const { serviceId, cfgId } = useParams();
  const navigate = useNavigate();
  const isEdit = !!cfgId;
  const [form, setForm] = useState({ filename: '', content: '', description: '', comment: '', env_id: '' });
  const [envs, setEnvs] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    api.envList().then(d => setEnvs(d.environments));
    if (isEdit) {
      api.cfgGet(serviceId, cfgId).then(d => setForm({
        filename: d.filename, content: d.content || '', description: d.description || '',
        comment: '', env_id: d.env_id ?? '',
      })).catch(e => setError(e.message));
    }
  }, [serviceId, cfgId]);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const payload = { ...form, env_id: form.env_id === '' ? null : parseInt(form.env_id) };
      if (isEdit) await api.cfgUpdate(serviceId, cfgId, payload);
      else await api.cfgCreate(serviceId, payload);
      navigate(`/services/${serviceId}/configs`);
    } catch (err) { setError(err.message); }
  };

  return (
    <div>
      <h4 className="mb-3">
        <i className="bi bi-file-earmark-code me-2"></i>
        {isEdit ? 'Редактировать' : 'Создать'} виртуальный конфиг
      </h4>
      {error && <div className="alert alert-danger">{error}</div>}
      <form onSubmit={submit}>
        <div className="row mb-3">
          <div className="col-md-6">
            <label className="form-label">Имя файла</label>
            <input className="form-control font-monospace" value={form.filename} required
                   onChange={e => setForm({ ...form, filename: e.target.value })} />
          </div>
          <div className="col-md-6">
            <label className="form-label">Окружение</label>
            <select className="form-select" value={form.env_id}
                    onChange={e => setForm({ ...form, env_id: e.target.value })}>
              <option value="">Базовый (все env)</option>
              {envs.map(env => <option key={env.id} value={env.id}>{env.name}</option>)}
            </select>
          </div>
        </div>
        <div className="mb-3">
          <label className="form-label">Описание</label>
          <input className="form-control" value={form.description}
                 onChange={e => setForm({ ...form, description: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className="form-label">Содержимое</label>
          <textarea className="form-control font-monospace" rows={16} value={form.content}
                    onChange={e => setForm({ ...form, content: e.target.value })}
                    style={{ whiteSpace: 'pre', tabSize: 2 }} />
        </div>
        <div className="mb-3">
          <label className="form-label">Комментарий к версии</label>
          <input className="form-control" value={form.comment}
                 onChange={e => setForm({ ...form, comment: e.target.value })}
                 placeholder={isEdit ? 'Описание изменений' : 'Первая версия'} />
        </div>
        <button className="btn btn-primary me-2" type="submit"><i className="bi bi-check-lg me-1"></i>Сохранить</button>
        <button className="btn btn-secondary" type="button"
                onClick={() => navigate(`/services/${serviceId}/configs`)}>Отмена</button>
      </form>
    </div>
  );
}
