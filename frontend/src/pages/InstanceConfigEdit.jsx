import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../api';

export default function InstanceConfigEdit() {
  const { instanceId, configId } = useParams();
  const navigate = useNavigate();
  const [cfg, setCfg] = useState(null);
  const [content, setContent] = useState('');
  const [error, setError] = useState('');
  const [wrap, setWrap] = useState(false);

  useEffect(() => {
    api.instCfgGet(instanceId, configId).then(d => {
      setCfg(d);
      setContent(d.content || '');
    }).catch(e => setError(e.message));
  }, [instanceId, configId]);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    try {
      await api.instCfgUpdate(instanceId, configId, { content });
      navigate(`/instances/${instanceId}`);
    } catch (err) { setError(err.message); }
  };

  if (!cfg) return <div className="text-center py-5"><div className="spinner-border"></div></div>;

  return (
    <div>
      <h4 className="mb-3">
        <i className="bi bi-file-earmark-text me-2"></i>
        Редактирование: <span className="font-monospace">{cfg.filename}</span>
      </h4>
      <p className="text-muted small">Путь: {cfg.filepath}</p>
      {error && <div className="alert alert-danger">{error}</div>}
      <form onSubmit={submit}>
        <div className="mb-2 d-flex gap-2">
          <div className="form-check form-switch">
            <input className="form-check-input" type="checkbox" checked={wrap}
                   onChange={e => setWrap(e.target.checked)} id="wrapToggle" />
            <label className="form-check-label" htmlFor="wrapToggle">Перенос строк</label>
          </div>
        </div>
        <textarea className="form-control font-monospace" rows={20} value={content}
                  onChange={e => setContent(e.target.value)}
                  style={{ whiteSpace: wrap ? 'pre-wrap' : 'pre', tabSize: 2 }} />
        <div className="mt-2">
          <button className="btn btn-primary me-2" type="submit"><i className="bi bi-check-lg me-1"></i>Сохранить</button>
          <button className="btn btn-secondary" type="button" onClick={() => navigate(`/instances/${instanceId}`)}>Отмена</button>
        </div>
      </form>
    </div>
  );
}
