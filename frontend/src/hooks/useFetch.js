import { useState, useEffect, useCallback } from 'react';

/**
 * Reusable data fetching hook.
 * @param {Function} fetcher - async function returning data (e.g. () => api.envList())
 * @param {Array} deps - dependency array to trigger re-fetch
 * @returns {{ data, error, loading, reload }}
 */
export default function useFetch(fetcher, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await fetcher();
      setData(result);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { reload(); }, [reload]);

  return { data, error, loading, reload };
}
