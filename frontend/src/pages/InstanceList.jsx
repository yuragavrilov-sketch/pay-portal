import React, { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';
import { useEnv } from '../context/EnvContext';
import StatusBadge from '../components/StatusBadge';
import useSSE from '../hooks/useSSE';
import Confirm from '../components/Confirm';

export default function InstanceList() {
  const { currentEnv } = useEnv();
  const [instances, setInstances] = useState([]);
  const [delId, setDelId] = useState(null);
  const [scanUrl, setScanUrl] = useState(null);
  const [scanResults, setScanResults] = useState({});
  const [scanning, setScanning] = useState(false);

  const load = () => api.instList().then(d => setInstances(d.instances)).catch(() => {});
  useEffect(() => { load(); }, [currentEnv]);

  const doDelete = async () => {
    await api.instDelete(delId);
    setDelId(null);
    load();
  };

  const refreshStatus = async (id) => {
    const r = await api.instRefreshStatus(id);
    setInstances(prev => prev.map(i => i.id === id ? { ...i, status: r.status } : i));
  };

  const startScan = async () => {
    setScanning(true);
    setScanResults({});
    try {
      const r = await api.scanConfigs(currentEnv ? { env_id: currentEnv.id } : {});
      setScanUrl(api.taskStreamUrl(r.task_id));
    } catch { setScanning(false); }
  };

  const onScanSSE = useCallback((ev, es) => {
    if (ev.type === 'scan_done') {
      setScanResults(prev => ({ ...prev, [ev.instance_id]: ev }));
    } else if (ev.type === 'done_all') {
      es.close();
      setScanning(false);
      setScanUrl(null);
    }
  }, []);

  useSSE(scanUrl, onScanSSE, () => { setScanning(false); setScanUrl(null); });

  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h4 className="mb-0">
          <i className="bi bi-hdd-rack me-2"></i>Экземпляры
          {currentEnv && <span className="badge bg-primary fs-6 ms-2">{currentEnv.name}</span>}
        </h4>
        <div className="d-flex gap-2">
          <button className="btn btn-sm btn-outline-info" onClick={startScan} disabled={scanning}>
            <i className={`bi bi-search me-1 ${scanning ? 'spin' : ''}`}></i>Сканировать
          </button>
          <Link to="/instances/create" className="btn btn-primary">
            <i className="bi bi-plus-lg me-1"></i>Добавить
          </Link>
        </div>
      </div>
      <div className="card"><div className="table-responsive">
      <table className="table table-hover align-middle mb-0">
        <thead className="table-light"><tr><th>Сервис</th><th>Имя (Windows)</th><th>Сервер</th><th>Окружение</th><th>Статус</th><th>Скан</th><th style={{width:100}}></th></tr></thead>
        <tbody>
          {instances.map(inst => {
            const scan = scanResults[inst.id];
            return (
              <tr key={inst.id}>
                <td><Link to={`/instances/${inst.id}`}>{inst.service_name}</Link></td>
                <td className="font-monospace fw-semibold">{inst.win_service_name}</td>
                <td>{inst.hostname}</td>
                <td>{inst.environments?.map(e => <span key={e} className="badge bg-secondary me-1">{e}</span>)}</td>
                <td>
                  <StatusBadge status={inst.status} />
                  <button className="btn btn-link btn-sm p-0 ms-1" onClick={() => refreshStatus(inst.id)}>
                    <i className="bi bi-arrow-clockwise"></i>
                  </button>
                </td>
                <td>
                  {scanning && !scan && <span className="spinner-border spinner-border-sm" style={{ width: '.6rem', height: '.6rem' }}></span>}
                  {scan && (
                    scan.ok
                      ? scan.diffs?.filter(d => d.status !== 'ok').length === 0
                        ? <span className="badge bg-success" style={{ fontSize: '.65rem' }}><i className="bi bi-check-lg"></i> {scan.diffs?.length}</span>
                        : <span className="badge bg-warning text-dark" style={{ fontSize: '.65rem' }}><i className="bi bi-exclamation-triangle"></i> {scan.diffs?.filter(d => d.status !== 'ok').length}/{scan.diffs?.length}</span>
                      : <span className="badge bg-secondary" style={{ fontSize: '.65rem' }} title={scan.message}><i className="bi bi-x-circle"></i></span>
                  )}
                </td>
                <td className="text-end">
                  <Link to={`/instances/${inst.id}`} className="btn btn-sm btn-outline-secondary me-1">
                    <i className="bi bi-eye"></i>
                  </Link>
                  <button className="btn btn-sm btn-outline-danger" onClick={() => setDelId(inst.id)}>
                    <i className="bi bi-trash"></i>
                  </button>
                </td>
              </tr>
            );
          })}
          {!instances.length && <tr><td colSpan={7} className="text-center text-muted py-4">Нет экземпляров</td></tr>}
        </tbody>
      </table>
      </div></div>
      <Confirm show={!!delId} title="Удалить экземпляр?" body="Это действие нельзя отменить."
               onConfirm={doDelete} onCancel={() => setDelId(null)} />
    </div>
  );
}
