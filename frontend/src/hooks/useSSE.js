import { useEffect, useRef, useCallback } from 'react';

export default function useSSE(url, onMessage, onError) {
  const onMessageRef = useRef(onMessage);
  const onErrorRef = useRef(onError);

  // Keep refs current to avoid stale closures
  useEffect(() => { onMessageRef.current = onMessage; }, [onMessage]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);

  const esRef = useRef(null);

  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'heartbeat') return;
        onMessageRef.current?.(data, es);
      } catch {}
    };

    es.onerror = () => {
      es.close();
      esRef.current = null;
      // SSE doesn't expose HTTP status; clear auth on connection failure
      onErrorRef.current?.();
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [url]);

  return esRef;
}
