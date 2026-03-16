import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../api';
import useSSE from '../hooks/useSSE';

const STEPS = [
  { key: 'servers',  label: 'Серверы',       icon: 'bi-server' },
  { key: 'discover', label: 'Обнаружение',   icon: 'bi-search' },
  { key: 'assign',   label: 'Назначение',    icon: 'bi-diagram-3' },
  { key: 'create',   label: 'Создание',      icon: 'bi-check-lg' },
];

export default function InstanceCreate() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);

  // Step 1 — servers
  const [servers, setServers] = useState([]);
  const [selectedServerIds, setSelectedServerIds] = useState(new Set());

  // Step 2 — discover
  const [discoverUrl, setDiscoverUrl] = useState(null);
  const [discovering, setDiscovering] = useState(false);
  const [serverResults, setServerResults] = useState({});  // { serverId: { ok, hostname, services, error } }
  const [selectedWinSvcs, setSelectedWinSvcs] = useState(new Set());  // "serverId:winName"
  const [hideRegistered, setHideRegistered] = useState(true);
  const [searchFilter, setSearchFilter] = useState('');

  // Step 3 — assign service
  const [services, setServices] = useState([]);
  const [assignments, setAssignments] = useState({});  // "serverId:winName" -> serviceId
  const [bulkServiceId, setBulkServiceId] = useState('');

  // Step 4 — create
  const [createUrl, setCreateUrl] = useState(null);
  const [createResults, setCreateResults] = useState([]);
  const [createDone, setCreateDone] = useState(false);

  // Load servers + catalog services on mount
  useEffect(() => {
    api.serverList().then(d => setServers(d.servers || []));
    api.svcList().then(d => setServices(d.services || []));
  }, []);

  // --- Step 1: Server selection ---
  const toggleServer = (id) => {
    setSelectedServerIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleAllServers = () => {
    if (selectedServerIds.size === servers.length) {
      setSelectedServerIds(new Set());
    } else {
      setSelectedServerIds(new Set(servers.map(s => s.id)));
    }
  };

  // --- Step 2: Discover ---
  const startDiscover = async () => {
    setDiscovering(true);
    setServerResults({});
    setSelectedWinSvcs(new Set());
    try {
      const r = await api.serversDiscover({ server_ids: [...selectedServerIds] });
      setDiscoverUrl(api.taskStreamUrl(r.task_id));
    } catch { setDiscovering(false); }
  };

  useEffect(() => {
    if (step === 1 && !discovering && Object.keys(serverResults).length === 0) {
      startDiscover();
    }
  }, [step]);

  const onDiscoverSSE = useCallback((ev, es) => {
    if (ev.type === 'server_done') {
      setServerResults(prev => ({ ...prev, [ev.server_id]: ev }));
    } else if (ev.type === 'done_all') {
      es.close();
      setDiscovering(false);
      setDiscoverUrl(null);
    }
  }, []);
  useSSE(discoverUrl, onDiscoverSSE, () => { setDiscovering(false); setDiscoverUrl(null); });

  // Flattened discovered services list
  const discoveredList = useMemo(() => {
    const list = [];
    for (const [srvId, res] of Object.entries(serverResults)) {
      if (!res.ok) continue;
      for (const svc of (res.services || [])) {
        const key = `${srvId}:${svc.name}`;
        list.push({
          key,
          serverId: Number(srvId),
          hostname: res.hostname,
          name: svc.name,
          displayName: svc.display_name,
          status: svc.status,
          alreadyRegistered: svc.already_registered,
        });
      }
    }
    return list;
  }, [serverResults]);

  const filteredList = useMemo(() => {
    let items = discoveredList;
    if (hideRegistered) items = items.filter(i => !i.alreadyRegistered);
    if (searchFilter) {
      const q = searchFilter.toLowerCase();
      items = items.filter(i =>
        i.name.toLowerCase().includes(q) ||
        i.displayName.toLowerCase().includes(q) ||
        i.hostname.toLowerCase().includes(q)
      );
    }
    return items;
  }, [discoveredList, hideRegistered, searchFilter]);

  const toggleWinSvc = (key) => {
    setSelectedWinSvcs(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const toggleAllVisible = () => {
    const visibleKeys = filteredList.filter(i => !i.alreadyRegistered).map(i => i.key);
    const allSelected = visibleKeys.every(k => selectedWinSvcs.has(k));
    setSelectedWinSvcs(prev => {
      const next = new Set(prev);
      visibleKeys.forEach(k => allSelected ? next.delete(k) : next.add(k));
      return next;
    });
  };

  // --- Step 3: Assign ---
  const selectedItems = useMemo(() =>
    discoveredList.filter(i => selectedWinSvcs.has(i.key)),
    [discoveredList, selectedWinSvcs]
  );

  const applyBulkService = () => {
    if (!bulkServiceId) return;
    const next = { ...assignments };
    selectedItems.forEach(i => { next[i.key] = bulkServiceId; });
    setAssignments(next);
  };

  const allAssigned = selectedItems.every(i => assignments[i.key]);

  // --- Step 4: Create ---
  const startCreate = async () => {
    const items = selectedItems.map(i => ({
      server_id: i.serverId,
      win_service_name: i.name,
      service_id: assignments[i.key],
    }));
    setCreateResults([]);
    setCreateDone(false);
    try {
      const r = await api.instCreate({ items });
      setCreateUrl(api.taskStreamUrl(r.task_id));
    } catch {}
  };

  useEffect(() => {
    if (step === 3 && !createDone && createResults.length === 0 && !createUrl) {
      startCreate();
    }
  }, [step]);

  const onCreateSSE = useCallback((ev, es) => {
    if (ev.type === 'item_done') {
      setCreateResults(prev => [...prev, ev]);
    } else if (ev.type === 'done_all') {
      es.close();
      setCreateDone(true);
      setCreateUrl(null);
    }
  }, []);
  useSSE(createUrl, onCreateSSE);

  // --- Navigation ---
  const canNext = () => {
    if (step === 0) return selectedServerIds.size > 0;
    if (step === 1) return selectedWinSvcs.size > 0 && !discovering;
    if (step === 2) return allAssigned;
    return false;
  };

  const goNext = () => {
    if (step < STEPS.length - 1) setStep(step + 1);
  };
  const goBack = () => {
    if (step > 0) setStep(step - 1);
  };

  // --- Render ---
  const renderStepIndicator = () => (
    <div className="d-flex mb-4">
      {STEPS.map((s, idx) => (
        <div key={s.key} className="d-flex align-items-center flex-grow-1">
          <div className={`d-flex align-items-center gap-2 px-3 py-2 rounded-pill ${
            idx === step ? 'bg-primary text-white' : idx < step ? 'bg-success text-white' : 'bg-light text-muted'
          }`} style={{ fontSize: '.85rem', whiteSpace: 'nowrap' }}>
            <i className={`bi ${idx < step ? 'bi-check-circle-fill' : s.icon}`}></i>
            <span className="fw-semibold">{idx + 1}. {s.label}</span>
          </div>
          {idx < STEPS.length - 1 && <div className="flex-grow-1 border-top mx-2" style={{ height: 0 }}></div>}
        </div>
      ))}
    </div>
  );

  const renderStep0 = () => (
    <div>
      <p className="text-muted mb-3">Выберите серверы, на которых нужно найти Windows-сервисы. Обнаружение будет выполнено параллельно.</p>
      <div className="mb-2">
        <button className="btn btn-sm btn-outline-secondary" onClick={toggleAllServers}>
          <i className={`bi ${selectedServerIds.size === servers.length ? 'bi-check-square' : 'bi-square'} me-1`}></i>
          {selectedServerIds.size === servers.length ? 'Снять все' : 'Выбрать все'}
        </button>
        <span className="ms-3 text-muted small">Выбрано: {selectedServerIds.size} из {servers.length}</span>
      </div>
      <div className="list-group" style={{ maxHeight: 400, overflow: 'auto' }}>
        {servers.map(s => (
          <label key={s.id} className="list-group-item list-group-item-action d-flex align-items-center gap-3 py-2" style={{ cursor: 'pointer' }}>
            <input type="checkbox" className="form-check-input m-0"
                   checked={selectedServerIds.has(s.id)}
                   onChange={() => toggleServer(s.id)} />
            <div>
              <span className="font-monospace fw-semibold">{s.hostname}</span>
              {s.environments?.length > 0 && (
                <span className="ms-2">{s.environments.map(e => <span key={e.id} className="badge bg-secondary me-1">{e.name}</span>)}</span>
              )}
            </div>
            <span className={`ms-auto badge ${s.is_available ? 'bg-success' : 'bg-secondary'}`}>
              {s.is_available ? 'online' : 'offline'}
            </span>
          </label>
        ))}
        {!servers.length && <div className="text-center text-muted py-4">Нет серверов</div>}
      </div>
    </div>
  );

  const renderStep1 = () => {
    const doneCount = Object.keys(serverResults).length;
    const totalCount = selectedServerIds.size;
    const errorServers = Object.values(serverResults).filter(r => !r.ok);

    return (
      <div>
        {/* Progress */}
        {discovering && (
          <div className="mb-3">
            <div className="d-flex align-items-center gap-2 mb-2">
              <div className="spinner-border spinner-border-sm"></div>
              <span>Обнаружение сервисов... {doneCount}/{totalCount}</span>
            </div>
            <div className="progress" style={{ height: 6 }}>
              <div className="progress-bar" style={{ width: `${totalCount ? (doneCount / totalCount) * 100 : 0}%` }}></div>
            </div>
          </div>
        )}

        {/* Errors */}
        {errorServers.length > 0 && (
          <div className="alert alert-warning py-2 mb-3">
            <i className="bi bi-exclamation-triangle me-1"></i>
            Ошибки на {errorServers.length} сервер(ах):
            {errorServers.map(e => (
              <div key={e.server_id} className="small font-monospace ms-3">{e.hostname}: {e.error}</div>
            ))}
          </div>
        )}

        {/* Filters */}
        <div className="d-flex gap-2 mb-3 align-items-center flex-wrap">
          <div className="form-check">
            <input type="checkbox" className="form-check-input" id="hideReg"
                   checked={hideRegistered} onChange={e => setHideRegistered(e.target.checked)} />
            <label className="form-check-label small" htmlFor="hideReg">Скрыть уже зарегистрированные</label>
          </div>
          <input className="form-control form-control-sm" style={{ maxWidth: 250 }}
                 placeholder="Поиск по имени / серверу..." value={searchFilter}
                 onChange={e => setSearchFilter(e.target.value)} />
          <button className="btn btn-sm btn-outline-secondary" onClick={toggleAllVisible}>
            <i className="bi bi-check-all me-1"></i>Выбрать все видимые
          </button>
          <span className="text-muted small ms-auto">
            Найдено: {filteredList.length} | Выбрано: {selectedWinSvcs.size}
          </span>
        </div>

        {/* Table */}
        <div className="table-responsive" style={{ maxHeight: 450, overflow: 'auto' }}>
          <table className="table table-sm table-hover align-middle mb-0">
            <thead className="table-light sticky-top">
              <tr>
                <th style={{ width: 40 }}></th>
                <th>Сервис</th>
                <th>Описание</th>
                <th>Сервер</th>
                <th>Статус</th>
              </tr>
            </thead>
            <tbody>
              {filteredList.map(item => (
                <tr key={item.key} className={item.alreadyRegistered ? 'text-muted' : ''}>
                  <td>
                    <input type="checkbox" className="form-check-input"
                           disabled={item.alreadyRegistered}
                           checked={selectedWinSvcs.has(item.key)}
                           onChange={() => toggleWinSvc(item.key)} />
                  </td>
                  <td>
                    <span className="font-monospace fw-semibold">{item.name}</span>
                    {item.alreadyRegistered && <span className="badge bg-info ms-2" style={{ fontSize: '.6rem' }}>уже добавлен</span>}
                  </td>
                  <td className="small text-muted">{item.displayName}</td>
                  <td className="small">{item.hostname}</td>
                  <td>
                    <span className={`badge ${item.status === 'Running' ? 'bg-success' : item.status === 'Stopped' ? 'bg-secondary' : 'bg-warning text-dark'}`}
                          style={{ fontSize: '.65rem' }}>
                      {item.status}
                    </span>
                  </td>
                </tr>
              ))}
              {filteredList.length === 0 && !discovering && (
                <tr><td colSpan={5} className="text-center text-muted py-4">
                  {discoveredList.length === 0 ? 'Сервисы не найдены' : 'Нет сервисов, соответствующих фильтрам'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  const renderStep2 = () => (
    <div>
      <p className="text-muted mb-3">Назначьте каталожный сервис для каждого выбранного Windows-сервиса.</p>

      {/* Bulk assign */}
      <div className="card bg-light mb-3">
        <div className="card-body py-2 d-flex align-items-center gap-2">
          <span className="text-muted small">Назначить всем:</span>
          <select className="form-select form-select-sm" style={{ maxWidth: 300 }}
                  value={bulkServiceId} onChange={e => setBulkServiceId(e.target.value)}>
            <option value="">-- сервис --</option>
            {services.map(s => <option key={s.id} value={s.id}>{s.display_name || s.name}</option>)}
          </select>
          <button className="btn btn-sm btn-primary" disabled={!bulkServiceId} onClick={applyBulkService}>
            <i className="bi bi-arrow-right me-1"></i>Применить
          </button>
        </div>
      </div>

      {/* Per-item assignment */}
      <div className="table-responsive" style={{ maxHeight: 450, overflow: 'auto' }}>
        <table className="table table-sm align-middle mb-0">
          <thead className="table-light sticky-top">
            <tr><th>Windows-сервис</th><th>Сервер</th><th>Каталожный сервис</th></tr>
          </thead>
          <tbody>
            {selectedItems.map(item => (
              <tr key={item.key}>
                <td className="font-monospace fw-semibold">{item.name}</td>
                <td className="small">{item.hostname}</td>
                <td>
                  <select className={`form-select form-select-sm ${assignments[item.key] ? '' : 'border-danger'}`}
                          style={{ maxWidth: 300 }}
                          value={assignments[item.key] || ''}
                          onChange={e => setAssignments(prev => ({ ...prev, [item.key]: e.target.value }))}>
                    <option value="">-- выберите --</option>
                    {services.map(s => <option key={s.id} value={s.id}>{s.display_name || s.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!allAssigned && (
        <div className="alert alert-warning py-2 mt-3 small">
          <i className="bi bi-exclamation-triangle me-1"></i>
          Назначьте сервис для всех {selectedItems.filter(i => !assignments[i.key]).length} оставшихся элементов
        </div>
      )}
    </div>
  );

  const renderStep3 = () => {
    const okCount = createResults.filter(r => r.ok).length;
    const errCount = createResults.filter(r => !r.ok).length;

    return (
      <div>
        {!createDone && (
          <div className="d-flex align-items-center gap-2 mb-3">
            <div className="spinner-border spinner-border-sm"></div>
            <span>Создание экземпляров... {createResults.length}/{selectedItems.length}</span>
          </div>
        )}

        {!createDone && (
          <div className="progress mb-3" style={{ height: 6 }}>
            <div className="progress-bar" style={{ width: `${selectedItems.length ? (createResults.length / selectedItems.length) * 100 : 0}%` }}></div>
          </div>
        )}

        {createDone && (
          <div className={`alert ${errCount === 0 ? 'alert-success' : 'alert-warning'} py-2 mb-3`}>
            <i className={`bi ${errCount === 0 ? 'bi-check-circle' : 'bi-exclamation-triangle'} me-2`}></i>
            Готово: {okCount} успешно{errCount > 0 ? `, ${errCount} с ошибкой` : ''}
          </div>
        )}

        <div className="list-group">
          {createResults.map((r, i) => (
            <div key={i} className={`list-group-item py-2 d-flex align-items-center gap-2 ${!r.ok ? 'list-group-item-danger' : ''}`}>
              <span className={`badge ${r.ok ? 'bg-success' : 'bg-danger'}`}>{r.ok ? 'OK' : 'ERR'}</span>
              <span className="font-monospace fw-semibold">{r.win_name}</span>
              <span className="text-muted small">@ {r.hostname}</span>
              <span className="text-muted small ms-auto">{r.message}</span>
            </div>
          ))}
        </div>

        {createDone && (
          <div className="mt-3 d-flex gap-2">
            <button className="btn btn-primary" onClick={() => navigate('/instances')}>
              <i className="bi bi-arrow-left me-1"></i>К списку экземпляров
            </button>
            <button className="btn btn-outline-secondary" onClick={() => navigate('/manage')}>
              <i className="bi bi-toggles me-1"></i>Управление
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div>
      <h4 className="mb-3"><i className="bi bi-magic me-2"></i>Мастер добавления экземпляров</h4>

      {renderStepIndicator()}

      <div className="card">
        <div className="card-header d-flex align-items-center gap-2">
          <i className={`bi ${STEPS[step].icon}`}></i>
          <span className="fw-semibold">{STEPS[step].label}</span>
        </div>
        <div className="card-body">
          {step === 0 && renderStep0()}
          {step === 1 && renderStep1()}
          {step === 2 && renderStep2()}
          {step === 3 && renderStep3()}
        </div>
        {step < 3 && (
          <div className="card-footer d-flex justify-content-between">
            <button className="btn btn-outline-secondary" onClick={goBack} disabled={step === 0}>
              <i className="bi bi-arrow-left me-1"></i>Назад
            </button>
            <button className="btn btn-primary" onClick={goNext} disabled={!canNext()}>
              {step === 2 ? 'Создать' : 'Далее'}
              <i className="bi bi-arrow-right ms-1"></i>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
