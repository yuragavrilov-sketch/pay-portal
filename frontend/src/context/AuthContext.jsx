import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';

const AuthContext = createContext();

const TOKEN_KEY = 'svcmgr_access_token';
const REFRESH_KEY = 'svcmgr_refresh_token';
const USER_KEY = 'svcmgr_user';

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
  });
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [loading, setLoading] = useState(true);
  const refreshTimer = useRef(null);

  const clearAuth = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
  }, []);

  const scheduleRefresh = useCallback((expiresIn) => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    // Refresh 60 seconds before expiry
    const ms = Math.max((expiresIn - 60) * 1000, 10000);
    refreshTimer.current = setTimeout(async () => {
      const rt = localStorage.getItem(REFRESH_KEY);
      if (!rt) { clearAuth(); return; }
      try {
        const resp = await fetch('/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: rt }),
        });
        if (!resp.ok) { clearAuth(); return; }
        const data = await resp.json();
        localStorage.setItem(TOKEN_KEY, data.access_token);
        localStorage.setItem(REFRESH_KEY, data.refresh_token);
        setToken(data.access_token);
        scheduleRefresh(data.expires_in);
      } catch {
        clearAuth();
      }
    }, ms);
  }, [clearAuth]);

  const login = useCallback(async (username, password) => {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || 'Login failed');

    localStorage.setItem(TOKEN_KEY, data.access_token);
    localStorage.setItem(REFRESH_KEY, data.refresh_token);
    setToken(data.access_token);

    // Fetch user info
    const meResp = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (meResp.ok) {
      const userData = await meResp.json();
      localStorage.setItem(USER_KEY, JSON.stringify(userData));
      setUser(userData);
    }

    scheduleRefresh(data.expires_in);
    return data;
  }, [scheduleRefresh]);

  const logout = useCallback(async () => {
    const rt = localStorage.getItem(REFRESH_KEY);
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      });
    } catch { /* ignore */ }
    clearAuth();
  }, [clearAuth]);

  // On mount: verify stored token
  useEffect(() => {
    const storedToken = localStorage.getItem(TOKEN_KEY);
    if (!storedToken) { setLoading(false); return; }

    fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${storedToken}` },
    })
      .then(resp => {
        if (resp.ok) return resp.json();
        throw new Error('Invalid token');
      })
      .then(userData => {
        setUser(userData);
        localStorage.setItem(USER_KEY, JSON.stringify(userData));
        setToken(storedToken);
        // Schedule refresh based on remaining token time (fallback 4 min)
        scheduleRefresh(240);
      })
      .catch(() => {
        // Try refresh
        const rt = localStorage.getItem(REFRESH_KEY);
        if (!rt) { clearAuth(); setLoading(false); return; }

        fetch('/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: rt }),
        })
          .then(resp => {
            if (!resp.ok) throw new Error('Refresh failed');
            return resp.json();
          })
          .then(data => {
            localStorage.setItem(TOKEN_KEY, data.access_token);
            localStorage.setItem(REFRESH_KEY, data.refresh_token);
            setToken(data.access_token);
            return fetch('/api/auth/me', {
              headers: { Authorization: `Bearer ${data.access_token}` },
            });
          })
          .then(resp => resp.ok ? resp.json() : Promise.reject())
          .then(userData => {
            setUser(userData);
            localStorage.setItem(USER_KEY, JSON.stringify(userData));
            scheduleRefresh(240);
          })
          .catch(() => clearAuth())
          .finally(() => setLoading(false));
        return; // Don't setLoading(false) yet
      })
      .finally(() => setLoading(false));

    return () => { if (refreshTimer.current) clearTimeout(refreshTimer.current); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AuthContext.Provider value={{ user, token, loading, login, logout, isAuthenticated: !!token }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
