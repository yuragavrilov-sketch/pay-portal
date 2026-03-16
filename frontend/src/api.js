const BASE = '/api';

function getToken() {
  return localStorage.getItem('svcmgr_access_token');
}

async function request(method, path, body) {
  const opts = { method, headers: {} };

  // Attach JWT token
  const token = getToken();
  if (token) {
    opts.headers['Authorization'] = `Bearer ${token}`;
  }

  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  const r = await fetch(BASE + path, opts);

  // On 401 — redirect to login
  if (r.status === 401) {
    localStorage.removeItem('svcmgr_access_token');
    localStorage.removeItem('svcmgr_refresh_token');
    localStorage.removeItem('svcmgr_user');
    window.location.href = '/login';
    throw new Error('Session expired');
  }

  if (!r.ok) {
    const text = await r.text();
    let msg;
    try { msg = JSON.parse(text).error; } catch { msg = text; }
    throw new Error(msg || `HTTP ${r.status}`);
  }
  return r.json();
}

const api = {
  get:    (p)    => request('GET', p),
  post:   (p, b) => request('POST', p, b),
  put:    (p, b) => request('PUT', p, b),
  del:    (p)    => request('DELETE', p),

  // Dashboard
  dashboard: ()          => api.get('/dashboard'),

  // Env selection
  currentEnv: ()         => api.get('/env/current'),
  selectEnv:  (id)       => api.post(`/env/select/${id}`),
  clearEnv:   ()         => api.post('/env/clear'),

  // Environments
  envList:    ()         => api.get('/environments'),
  envGet:     (id)       => api.get(`/environments/${id}`),
  envCreate:  (d)        => api.post('/environments', d),
  envUpdate:  (id, d)    => api.put(`/environments/${id}`, d),
  envDelete:  (id)       => api.del(`/environments/${id}`),

  // Credentials
  credList:   ()         => api.get('/credentials'),
  credGet:    (id)       => api.get(`/credentials/${id}`),
  credCreate: (d)        => api.post('/credentials', d),
  credUpdate: (id, d)    => api.put(`/credentials/${id}`, d),
  credDelete: (id)       => api.del(`/credentials/${id}`),

  // Servers
  serverList:     ()         => api.get('/servers'),
  serverGet:      (id)       => api.get(`/servers/${id}`),
  serverCreate:   (d)        => api.post('/servers', d),
  serverUpdate:   (id, d)    => api.put(`/servers/${id}`, d),
  serverDelete:   (id)       => api.del(`/servers/${id}`),
  serverTest:     (id)       => api.post(`/servers/${id}/test`),
  serverServices: (id)       => api.get(`/servers/${id}/services`),

  // Services
  svcList:    ()         => api.get('/services'),
  svcGet:     (id)       => api.get(`/services/${id}`),
  svcCreate:  (d)        => api.post('/services', d),
  svcUpdate:  (id, d)    => api.put(`/services/${id}`, d),
  svcDelete:  (id)       => api.del(`/services/${id}`),

  // Service configs
  cfgList:        (svcId)             => api.get(`/services/${svcId}/configs`),
  cfgGet:         (svcId, cfgId)      => api.get(`/services/${svcId}/configs/${cfgId}`),
  cfgCreate:      (svcId, d)          => api.post(`/services/${svcId}/configs`, d),
  cfgUpdate:      (svcId, cfgId, d)   => api.put(`/services/${svcId}/configs/${cfgId}`, d),
  cfgDelete:      (svcId, cfgId)      => api.del(`/services/${svcId}/configs/${cfgId}`),
  cfgVersions:    (svcId, cfgId)      => api.get(`/services/${svcId}/configs/${cfgId}/versions`),
  cfgActivateVer: (svcId, cfgId, vId) => api.post(`/services/${svcId}/configs/${cfgId}/versions/${vId}/activate`),
  cfgPushData:    (svcId, cfgId)      => api.get(`/services/${svcId}/configs/${cfgId}/push`),
  cfgPush:        (svcId, cfgId, d)   => api.post(`/services/${svcId}/configs/${cfgId}/push`, d),
  cfgSummary:     (svcId)             => api.get(`/services/${svcId}/config-summary`),

  // Instances
  instList:           ()     => api.get('/instances'),
  instGet:            (id)   => api.get(`/instances/${id}`),
  instCreate:         (d)    => api.post('/instances', d),
  instDelete:         (id)   => api.del(`/instances/${id}`),
  instRefreshStatus:  (id)   => api.post(`/instances/${id}/refresh-status`),
  instRefreshConfigs: (id)   => api.post(`/instances/${id}/refresh-configs`),
  instCfgGet:         (iId, cId) => api.get(`/instances/${iId}/configs/${cId}`),
  instCfgUpdate:      (iId, cId, d) => api.put(`/instances/${iId}/configs/${cId}`, d),
  instCfgDelete:      (iId, cId)    => api.del(`/instances/${iId}/configs/${cId}`),
  scanConfigs:        (d)    => api.post('/instances/scan-configs', d),

  // Manage
  manageData:             ()     => api.get('/manage'),
  manageControl:          (id, d) => api.post(`/manage/instances/${id}/control`, d),
  manageServiceControl:   (id, d) => api.post(`/manage/services/${id}/control`, d),
  manageInstanceDeploy:   (id, d) => api.post(`/manage/instances/${id}/config-deploy`, d),
  manageServiceDeploy:    (id, d) => api.post(`/manage/services/${id}/config-deploy`, d),
  manageSnapshots:        (id)    => api.get(`/manage/instances/${id}/snapshots`),
  manageSnapshotDetail:   (id)    => api.get(`/manage/snapshots/${id}`),
  manageSnapshotRestore:  (id)    => api.post(`/manage/snapshots/${id}/restore`),
  manageConfigDiff:       (id, fn) => api.get(`/manage/instances/${id}/config-diff?filename=${encodeURIComponent(fn)}`),

  // Audit
  auditList: (params) => api.get('/audit?' + new URLSearchParams(params).toString()),

  // SSE stream URL (not a fetch call — token appended as query param)
  taskStreamUrl: (taskId) => {
    const token = getToken();
    const base = `/api/manage/tasks/${taskId}/stream`;
    return token ? `${base}?token=${encodeURIComponent(token)}` : base;
  },
};

export default api;
