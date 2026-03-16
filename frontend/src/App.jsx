import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { EnvProvider } from './context/EnvContext';
import { useAuth } from './context/AuthContext';
import Layout from './components/Layout';
import Login from './pages/Login';

import Dashboard from './pages/Dashboard';
import EnvironmentList from './pages/EnvironmentList';
import CredentialList from './pages/CredentialList';
import ServerList from './pages/ServerList';
import ServiceList from './pages/ServiceList';
import ServiceConfigs from './pages/ServiceConfigs';
import ServiceConfigEdit from './pages/ServiceConfigEdit';
import ServiceConfigVersions from './pages/ServiceConfigVersions';
import ServiceConfigPush from './pages/ServiceConfigPush';
import InstanceList from './pages/InstanceList';
import InstanceCreate from './pages/InstanceCreate';
import InstanceDetail from './pages/InstanceDetail';
import InstanceConfigEdit from './pages/InstanceConfigEdit';
import Manage from './pages/Manage';
import AuditLog from './pages/AuditLog';

function ProtectedRoutes() {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="d-flex align-items-center justify-content-center" style={{ minHeight: '100vh' }}>
        <div className="text-center text-secondary">
          <div className="spinner-border mb-3" role="status"></div>
          <div>Loading...</div>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) return <Navigate to="/login" replace />;

  return (
    <EnvProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/environments" element={<EnvironmentList />} />
          <Route path="/credentials" element={<CredentialList />} />
          <Route path="/servers" element={<ServerList />} />
          <Route path="/services" element={<ServiceList />} />
          <Route path="/services/:serviceId/configs" element={<ServiceConfigs />} />
          <Route path="/services/:serviceId/configs/create" element={<ServiceConfigEdit />} />
          <Route path="/services/:serviceId/configs/:cfgId/edit" element={<ServiceConfigEdit />} />
          <Route path="/services/:serviceId/configs/:cfgId/versions" element={<ServiceConfigVersions />} />
          <Route path="/services/:serviceId/configs/:cfgId/push" element={<ServiceConfigPush />} />
          <Route path="/instances" element={<InstanceList />} />
          <Route path="/instances/create" element={<InstanceCreate />} />
          <Route path="/instances/:id" element={<InstanceDetail />} />
          <Route path="/instances/:instanceId/configs/:configId" element={<InstanceConfigEdit />} />
          <Route path="/manage" element={<Manage />} />
          <Route path="/audit" element={<AuditLog />} />
        </Route>
      </Routes>
    </EnvProvider>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/*" element={<ProtectedRoutes />} />
    </Routes>
  );
}
