import { useEffect, useRef } from 'react';

export default function useSSE(url, onMessage, onError) {
  const esRef = useRef(null);

  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'heartbeat') return;
        onMessage(data, es);
      } catch {}
    };

    es.onerror = () => {
      es.close();
      esRef.current = null;
      onError?.();
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [url]);

  return esRef;
}
