import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api';
import useSSE from '../hooks/useSSE';

export default function InstanceCreate() {
  const navigate = useNavigate();
  const [services, setServices] = useState([]);
  const [servers, setServers] = useState([]);
  const [selectedServer, setSelectedServer] = useState('');
  const [selectedService, setSelectedService] = useState('');
  const [winServices, setWinServices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState([]);
  const [taskUrl, setTaskUrl] = useState(null);
  const [results, setResults] = useState([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api.svcList().then(d => setServices(d.services));
    api.serverList().then(d => setServers(d.servers));
  }, []);

  const loadWinServices = async (serverId) => {
    setSelectedServer(serverId);
    if (!serverId) { setWinServices([]); return; }
    setLoading(true);
    try {
      const r = await api.serverServices(serverId);
      setWinServices(r.services || []);
    } catch { setWinServices([]); }
    setLoading(false);
  };

  const addItem = (winName) => {
    if (!selectedService || !selectedServer) return;
    if (items.some(i => i.server_id === selectedServer && i.win_service_name === winName)) return;
    const srv = servers.find(s => String(s.id) === String(selectedServer));
    setItems(prev => [...prev, {
      server_id: selectedServer, win_service_name: winName,
      service_id: selectedService, hostname: srv?.hostname || '',
    }]);
  };

  const removeItem = (idx) => setItems(prev => prev.filter((_, i) => i !== idx));

  const submit = async () => {
    if (!items.length) return;
    setResults([]);
    setDone(false);
    try {
      const r = await api.instCreate({ items });
      setTaskUrl(api.taskStreamUrl(r.task_id));
    } catch {}
  };

  const onSSE = useCallback((ev, es) => {
    if (ev.type === 'item_done') {
      setResults(prev => [...prev, ev]);
    } else if (ev.type === 'done_all') {
      es.close();
      setDone(true);
      setTaskUrl(null);
    }
  }, []);

  useSSE(taskUrl, onSSE);

  return (
    <div>
      <h4 className="mb-3"><i className="bi bi-plus-lg me-2"></i>Добавить экземпляры</h4>

      <div className="row g-3 mb-3">
        <div className="col-md-4">
          <label className="form-label">Сервис</label>
          <select className="form-select" value={selectedService}
                  onChange={e => setSelectedService(e.target.value)}>
            <option value="">-- выберите --</option>
            {services.map(s => <option key={s.id} value={s.id}>{s.display_name || s.name}</option>)}
          </select>
        </div>
        <div className="col-md-4">
          <label className="form-label">Сервер</label>
          <select className="form-select" value={selectedServer}
                  onChange={e => loadWinServices(e.target.value)}>
            <option value="">-- выберите --</option>
            {servers.map(s => <option key={s.id} value={s.id}>{s.hostname}</option>)}
          </select>
        </div>
      </div>

      {loading && <div className="spinner-border spinner-border-sm me-2"></div>}

      {winServices.length > 0 && (
        <div className="mb-3">
          <label className="form-label">Windows-сервисы на сервере</label>
          <div className="list-group" style={{ maxHeight: 300, overflow: 'auto' }}>
            {winServices.map(ws => (
              <button key={ws.name} className="list-group-item list-group-item-action d-flex justify-content-between align-items-center py-1"
                      onClick={() => addItem(ws.name)}>
                <span className="font-monospace">{ws.name}</span>
                <span className="text-muted small">{ws.display_name}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="mb-3">
          <h5>Выбранные ({items.length})</h5>
          <div className="list-group">
            {items.map((item, idx) => (
              <div key={idx} className="list-group-item d-flex align-items-center gap-2 py-1">
                <span className="font-monospace">{item.win_service_name}</span>
                <span className="text-muted">@ {item.hostname}</span>
                <button className="btn btn-sm btn-outline-danger ms-auto" onClick={() => removeItem(idx)}>
                  <i className="bi bi-x"></i>
                </button>
              </div>
            ))}
          </div>
          <button className="btn btn-primary mt-2" onClick={submit} disabled={!!taskUrl}>
            <i className="bi bi-check-lg me-1"></i>Создать ({items.length})
          </button>
        </div>
      )}

      {results.length > 0 && (
        <div className="mt-3">
          <h5>Результаты</h5>
          <div className="list-group">
            {results.map((r, i) => (
              <div key={i} className={`list-group-item py-1 d-flex gap-2`}>
                <span className={`badge ${r.ok ? 'bg-success' : 'bg-danger'}`}>{r.ok ? 'ok' : 'err'}</span>
                <span className="font-monospace">{r.win_name}</span>
                <span className="text-muted">{r.hostname}</span>
                <span className="text-muted small ms-auto">{r.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {done && (
        <div className="mt-3">
          <button className="btn btn-outline-secondary" onClick={() => navigate('/instances')}>
            <i className="bi bi-arrow-left me-1"></i>К списку экземпляров
          </button>
        </div>
      )}
    </div>
  );
}
