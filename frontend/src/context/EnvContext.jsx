import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api from '../api';

const EnvContext = createContext();

export function EnvProvider({ children }) {
  const [currentEnv, setCurrentEnv] = useState(null);
  const [environments, setEnvironments] = useState([]);

  const reload = useCallback(async () => {
    try {
      const data = await api.currentEnv();
      setCurrentEnv(data.current_env);
      setEnvironments(data.environments);
    } catch {}
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const selectEnv = useCallback(async (id) => {
    await api.selectEnv(id);
    await reload();
  }, [reload]);

  const clearEnv = useCallback(async () => {
    await api.clearEnv();
    await reload();
  }, [reload]);

  return (
    <EnvContext.Provider value={{ currentEnv, environments, selectEnv, clearEnv, reload }}>
      {children}
    </EnvContext.Provider>
  );
}

export function useEnv() {
  return useContext(EnvContext);
}
