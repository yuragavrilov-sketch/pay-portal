import React, { useEffect, useState, useCallback } from 'react';
import api from '../api';
import { useEnv } from '../context/EnvContext';
import StatusBadge, { SyncBadge } from '../components/StatusBadge';
import DiffViewer from '../components/DiffViewer';
import useSSE from '../hooks/useSSE';

export default function Manage() {
  const { currentEnv } = useEnv();
  const [groups, setGroups] = useState([]);
  const [scanUrl, setScanUrl] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [scanBadges, setScanBadges] = useState({});
  const [cfgSummaries, setCfgSummaries] = useState({});
  const [controlTaskUrl, setControlTaskUrl] = useState(null);
  const [controlResults, setControlResults] = useState({});

  // Diff modal state
  const [diffData, setDiffData] = useState(null);
  const [diffLoading, setDiffLoading] = useState(false);

  // Config mgmt modal state
  const [cfgMgmt, setCfgMgmt] = useState(null);
  const [deployUrl, setDeployUrl] = useState(null);
  const [deployResults, setDeployResults] = useState([]);
  const [deployDone, setDeployDone] = useState(false);

  // Snapshot modal
  const [snapshots, setSnapshots] = useState(null);
  const [snapDetail, setSnapDetail] = useState(null);

  const load = () => api.manageData().then(d => setGroups(d.service_groups)).catch(() => {});
  useEffect(() => { load(); }, [currentEnv]);

  // Auto-scan on load
  useEffect(() => {
    if (groups.length > 0) startScan();
  }, [groups.length]);

  // Load config summaries
  useEffect(() => {
    groups.forEach(g => {
      if (g.service.config_count > 0) {
        api.cfgSummary(g.service.id).then(d => {
          setCfgSummaries(prev => ({ ...prev, [g.service.id]: d }));
        }).catch(() => {});
      }
    });
  }, [groups]);

  // --- Scan ---
  const startScan = async () => {
    setScanning(true);
    setScanBadges({});
    try {
      const r = await api.scanConfigs(currentEnv ? { env_id: currentEnv.id } : {});
      setScanUrl(api.taskStreamUrl(r.task_id));
    } catch { setScanning(false); }
  };

  const onScanSSE = useCallback((ev, es) => {
    if (ev.type === 'scan_done') {
      setScanBadges(prev => ({ ...prev, [ev.instance_id]: ev }));
    } else if (ev.type === 'done_all') {
      es.close(); setScanning(false); setScanUrl(null);
    }
  }, []);
  useSSE(scanUrl, onScanSSE, () => { setScanning(false); setScanUrl(null); });

  // --- Control (single / service) ---
  const controlInstance = async (instId, action) => {
    if (!confirm(`${action} этот экземпляр?`)) return;
    try {
      const r = await api.manageControl(instId, { action });
      setControlTaskUrl(api.taskStreamUrl(r.task_id));
    } catch {}
  };

  const controlService = async (svcId, action) => {
    if (!confirm(`${action} все экземпляры сервиса?`)) return;
    try {
      const r = await api.manageServiceControl(svcId, { action });
      setControlTaskUrl(api.taskStreamUrl(r.task_id));
    } catch {}
  };

  const onControlSSE = useCallback((ev, es) => {
    if (ev.type === 'done' || ev.type === 'instance_done') {
      setControlResults(prev => ({ ...prev, [ev.instance_id]: ev }));
      if (ev.status) {
        setGroups(prev => prev.map(g => ({
          ...g,
          instances: g.instances.map(i => i.id === ev.instance_id ? { ...i, status: ev.status } : i),
        })));
      }
    }
    if (ev.type === 'done' || ev.type === 'done_all') {
      es.close(); setControlTaskUrl(null);
    }
  }, []);
  useSSE(controlTaskUrl, onControlSSE);

  // --- Diff ---
  const showDiff = async (instId, filename) => {
    setDiffLoading(true);
    setDiffData(null);
    try {
      const d = await api.manageConfigDiff(instId, filename);
      setDiffData(d);
    } catch (e) { setDiffData({ ok: false, error: e.message }); }
    setDiffLoading(false);
  };

  // --- Config Mgmt ---
  const openCfgMgmt = (svcId) => {
    const summary = cfgSummaries[svcId];
    setCfgMgmt({ svcId, summary });
    setDeployResults([]);
    setDeployDone(false);
  };

  const deployAll = async (cfgId, verId, doRestart) => {
    if (!cfgMgmt) return;
    if (!confirm(`Deploy${doRestart ? ' + рестарт' : ''} ко всем экземплярам?`)) return;
    setDeployResults([]);
    setDeployDone(false);
    try {
      const r = await api.manageServiceDeploy(cfgMgmt.svcId, {
        cfg_id: cfgId, ver_id: verId, restart: doRestart, force: true,
      });
      setDeployUrl(api.taskStreamUrl(r.task_id));
    } catch {}
  };

  const deployOne = async (instId, cfgId, verId, doRestart) => {
    if (!confirm(`Deploy${doRestart ? ' + рестарт' : ''}?`)) return;
    setDeployResults([]);
    setDeployDone(false);
    try {
      const r = await api.manageInstanceDeploy(instId, {
        cfg_id: cfgId, ver_id: verId, restart: doRestart,
      });
      setDeployUrl(api.taskStreamUrl(r.task_id));
    } catch {}
  };

  const onDeploySSE = useCallback((ev, es) => {
    if (ev.type === 'instance_done' || ev.type === 'done') {
      setDeployResults(prev => [...prev, ev]);
    }
    if (ev.type === 'done' || ev.type === 'done_all') {
      es.close(); setDeployUrl(null); setDeployDone(true);
      // Reload summaries
      if (cfgMgmt) {
        api.cfgSummary(cfgMgmt.svcId).then(d => {
          setCfgSummaries(prev => ({ ...prev, [cfgMgmt.svcId]: d }));
          setCfgMgmt(prev => prev ? { ...prev, summary: d } : null);
        }).catch(() => {});
      }
    }
  }, [cfgMgmt]);
  useSSE(deployUrl, onDeploySSE);

  // --- Snapshots ---
  const showSnapshots = async (instId) => {
    const d = await api.manageSnapshots(instId);
    setSnapshots({ instId, ...d });
  };
  const showSnapDetail = async (snapId) => {
    const d = await api.manageSnapshotDetail(snapId);
    setSnapDetail(d);
  };

  // --- Render scan badge for instance ---
  const renderScanBadge = (instId) => {
    const scan = scanBadges[instId];
    if (scanning && !scan) return <span className="spinner-border spinner-border-sm" style={{ width: '.6rem', height: '.6rem', borderWidth: '1px' }}></span>;
    if (!scan) return <span className="text-muted small">—</span>;
    if (!scan.ok) return <span className="badge bg-secondary" style={{ fontSize: '.65rem' }} title={scan.message}><i className="bi bi-x-circle"></i></span>;
    const diffs = scan.diffs || [];
    if (!diffs.length) return <span className="text-muted small">—</span>;
    const changed = diffs.filter(d => d.status !== 'ok');
    return (
      <span className="d-flex gap-1 flex-wrap align-items-center">
        {changed.length === 0
          ? <span className="badge bg-success" style={{ fontSize: '.65rem' }}><i className="bi bi-check-lg"></i> {diffs.length}</span>
          : <span className="badge bg-warning text-dark" style={{ fontSize: '.65rem' }}><i className="bi bi-exclamation-triangle"></i> {changed.length}/{diffs.length}</span>
        }
        {diffs.map(d => {
          const cls = d.status === 'ok' ? 'bg-success text-white' : d.status === 'changed' ? 'bg-warning text-dark' : d.status === 'new_on_server' ? 'bg-info text-white' : 'bg-danger text-white';
          return <span key={d.file} className={`cfg-ver-badge ${cls}`} title={d.file}>{d.file.split('.')[0]}</span>;
        })}
      </span>
    );
  };

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-toggles me-2"></i>Управление сервисами
          {currentEnv && <span className="badge bg-primary fs-6 ms-2">{currentEnv.name}</span>}
        </h4>
        <div className="d-flex gap-2">
          <button className="btn btn-sm btn-outline-info" onClick={startScan} disabled={scanning}>
            <i className={`bi bi-arrow-clockwise me-1 ${scanning ? 'spin' : ''}`}></i>Конфиги
          </button>
          <button className="btn btn-sm btn-outline-secondary" onClick={load}>
            <i className="bi bi-arrow-clockwise me-1"></i>Обновить
          </button>
        </div>
      </div>

      {groups.length === 0 && (
        <div className="text-center py-5 text-muted">
          <i className="bi bi-toggles fs-1 d-block mb-2"></i>Нет экземпляров сервисов.
        </div>
      )}

      {groups.map(g => (
        <div key={g.service.id} className="mb-3">
          <div className="svc-tree-header d-flex align-items-center gap-2"
               data-bs-toggle="collapse" data-bs-target={`#svc-body-${g.service.id}`}>
            <i className="bi bi-chevron-down me-1"></i>
            <span className="fw-semibold">{g.service.display_name || g.service.name}</span>
            <span className="badge bg-secondary">{g.service.name}</span>
            <span className="badge bg-dark">{g.instances.length} экз.</span>
            <span className="ms-auto d-flex gap-1">
              {g.service.config_count > 0 && (
                <button className="btn btn-sm btn-outline-info" onClick={(e) => { e.stopPropagation(); openCfgMgmt(g.service.id); }}>
                  <i className="bi bi-file-earmark-code me-1"></i>Конфиги
                </button>
              )}
              <button className="btn btn-sm btn-outline-success" onClick={(e) => { e.stopPropagation(); controlService(g.service.id, 'start'); }}>
                <i className="bi bi-play-fill me-1"></i>Запустить
              </button>
              <button className="btn btn-sm btn-outline-danger" onClick={(e) => { e.stopPropagation(); controlService(g.service.id, 'stop'); }}>
                <i className="bi bi-stop-fill me-1"></i>Стоп
              </button>
              <button className="btn btn-sm btn-outline-warning" onClick={(e) => { e.stopPropagation(); controlService(g.service.id, 'restart'); }}>
                <i className="bi bi-arrow-repeat me-1"></i>Рестарт
              </button>
            </span>
          </div>
          <div className="collapse show" id={`svc-body-${g.service.id}`} style={{ paddingLeft: '1.5rem' }}>
            {g.instances.map(inst => (
              <div key={inst.id} className="inst-row d-flex align-items-center gap-2 py-2 mt-1 rounded flex-wrap">
                <i className="bi bi-server text-muted"></i>
                <span className="font-monospace fw-semibold">{inst.win_service_name}</span>
                <span className="text-muted small">@{inst.hostname}</span>
                {inst.environments?.map(e => <span key={e} className="badge bg-secondary">{e}</span>)}
                <StatusBadge status={inst.status} />
                {renderScanBadge(inst.id)}
                <span className="ms-auto d-flex gap-1">
                  <button className="btn btn-sm btn-outline-success" onClick={() => controlInstance(inst.id, 'start')} title="Запустить">
                    <i className="bi bi-play-fill"></i>
                  </button>
                  <button className="btn btn-sm btn-outline-danger" onClick={() => controlInstance(inst.id, 'stop')} title="Стоп">
                    <i className="bi bi-stop-fill"></i>
                  </button>
                  <button className="btn btn-sm btn-outline-warning" onClick={() => controlInstance(inst.id, 'restart')} title="Рестарт">
                    <i className="bi bi-arrow-repeat"></i>
                  </button>
                  <button className="btn btn-sm btn-outline-secondary" onClick={() => showSnapshots(inst.id)} title="Снэпшоты">
                    <i className="bi bi-camera"></i>
                  </button>
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}

      {/* Diff Modal */}
      {(diffLoading || diffData) && (
        <div className="modal show d-block" style={{ background: 'rgba(0,0,0,.5)' }}>
          <div className="modal-dialog modal-xl">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title">
                  <i className="bi bi-file-diff me-2"></i>
                  {diffData?.filename || 'Diff'}
                  {diffData?.hostname && <span className="text-muted ms-2">@ {diffData.hostname}</span>}
                </h5>
                <button className="btn-close" onClick={() => { setDiffData(null); setDiffLoading(false); }}></button>
              </div>
              <div className="modal-body">
                {diffLoading && <div className="text-center py-3"><div className="spinner-border"></div></div>}
                {diffData && !diffData.ok && <div className="alert alert-danger">{diffData.error}</div>}
                {diffData?.ok && diffData.identical && <div className="alert alert-success">Файлы идентичны</div>}
                {diffData?.ok && !diffData.identical && <DiffViewer stored={diffData.stored} live={diffData.live} />}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Config Mgmt Modal */}
      {cfgMgmt && (
        <div className="modal show d-block" style={{ background: 'rgba(0,0,0,.5)' }}>
          <div className="modal-dialog modal-xl">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title"><i className="bi bi-gear me-2"></i>Управление конфигами</h5>
                <button className="btn-close" onClick={() => setCfgMgmt(null)}></button>
              </div>
              <div className="modal-body">
                {cfgMgmt.summary?.configs?.map(cfg => (
                  <div key={cfg.id} className="card mb-3">
                    <div className="card-header d-flex align-items-center gap-2 flex-wrap">
                      <i className="bi bi-file-earmark-code text-info"></i>
                      <span className="fw-semibold font-monospace">{cfg.filename}</span>
                      {cfg.env_label && <span className="badge bg-primary">{cfg.env_label}</span>}
                      <span className="ms-auto d-flex gap-2 align-items-center">
                        <select id={`ver-sel-${cfg.id}`} className="form-select form-select-sm" style={{ width: 'auto', minWidth: 260 }}
                                defaultValue={cfg.current_version_id}>
                          {cfg.versions.map(v => (
                            <option key={v.id} value={v.id}>v{v.version}{v.is_current ? ' *' : ''} — {v.comment || v.created_at}</option>
                          ))}
                        </select>
                        <button className="btn btn-sm btn-outline-primary"
                                onClick={() => deployAll(cfg.id, parseInt(document.getElementById(`ver-sel-${cfg.id}`).value), false)}>
                          Deploy all
                        </button>
                        <button className="btn btn-sm btn-warning"
                                onClick={() => deployAll(cfg.id, parseInt(document.getElementById(`ver-sel-${cfg.id}`).value), true)}>
                          Deploy+Restart all
                        </button>
                      </span>
                    </div>
                    <div className="card-body py-2">
                      {cfg.instances.map(inst => (
                        <div key={inst.instance_id} className="d-flex align-items-center gap-2 py-1">
                          <span className="font-monospace" style={{ minWidth: 180 }}>{inst.win_name}</span>
                          <span className="text-muted small">@{inst.hostname}</span>
                          <SyncBadge status={inst.status} size="sm" />
                          {inst.version != null && <span className="badge bg-secondary" style={{ fontSize: '.65rem' }}>v{inst.version}</span>}
                          <span className="ms-auto d-flex gap-1">
                            <button className="btn btn-outline-info" style={{ fontSize: '.72rem', padding: '1px 7px' }}
                                    onClick={() => showDiff(inst.instance_id, cfg.filename)}>
                              <i className="bi bi-file-diff"></i> Diff
                            </button>
                            <button className="btn btn-outline-primary" style={{ fontSize: '.72rem', padding: '1px 7px' }}
                                    onClick={() => deployOne(inst.instance_id, cfg.id, parseInt(document.getElementById(`ver-sel-${cfg.id}`).value), false)}>
                              Deploy
                            </button>
                            <button className="btn btn-warning" style={{ fontSize: '.72rem', padding: '1px 7px' }}
                                    onClick={() => deployOne(inst.instance_id, cfg.id, parseInt(document.getElementById(`ver-sel-${cfg.id}`).value), true)}>
                              Deploy+Restart
                            </button>
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}

                {deployResults.length > 0 && (
                  <div className="mt-3">
                    <h6>Результаты</h6>
                    <div className="list-group">
                      {deployResults.map((r, i) => (
                        <div key={i} className="list-group-item py-1 d-flex gap-2">
                          <span className={`badge ${r.ok ? 'bg-success' : r.skipped ? 'bg-secondary' : 'bg-danger'}`}>
                            {r.ok ? 'ok' : r.skipped ? 'skip' : 'err'}
                          </span>
                          <span className="font-monospace">{r.win_name}</span>
                          <span className="text-muted small">{r.hostname}</span>
                          <span className="text-muted small ms-auto">{r.message}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Snapshots Modal */}
      {snapshots && (
        <div className="modal show d-block" style={{ background: 'rgba(0,0,0,.5)' }}>
          <div className="modal-dialog modal-lg">
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title"><i className="bi bi-camera me-2"></i>Снэпшоты</h5>
                <button className="btn-close" onClick={() => { setSnapshots(null); setSnapDetail(null); }}></button>
              </div>
              <div className="modal-body">
                {snapDetail ? (
                  <div>
                    <button className="btn btn-sm btn-outline-secondary mb-2" onClick={() => setSnapDetail(null)}>
                      <i className="bi bi-arrow-left me-1"></i>Назад
                    </button>
                    <p className="small text-muted">Snap #{snapDetail.id} — {snapDetail.created_at}</p>
                    {snapDetail.configs?.map((c, i) => (
                      <div key={i} className="mb-2">
                        <strong className="font-monospace">{c.filename}</strong>
                        <pre className="mt-1 p-2 border rounded" style={{ maxHeight: 300, overflow: 'auto', fontSize: '.8rem' }}>{c.content}</pre>
                      </div>
                    ))}
                  </div>
                ) : (
                  <table className="table table-sm">
                    <thead><tr><th>#</th><th>Операция</th><th>Дата</th><th>Файлов</th><th></th></tr></thead>
                    <tbody>
                      {snapshots.snapshots?.map(s => (
                        <tr key={s.id}>
                          <td>{s.id}</td>
                          <td><span className={`badge ${s.trigger === 'start' ? 'bg-success' : s.trigger === 'stop' ? 'bg-danger' : 'bg-warning text-dark'}`}>{s.trigger}</span></td>
                          <td>{s.created_at}</td>
                          <td>{s.files}</td>
                          <td><button className="btn btn-sm btn-outline-secondary" onClick={() => showSnapDetail(s.id)}>Просмотр</button></td>
                        </tr>
                      ))}
                      {!snapshots.snapshots?.length && <tr><td colSpan={5} className="text-muted text-center">Нет снэпшотов</td></tr>}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
