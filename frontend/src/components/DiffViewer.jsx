import React, { useMemo } from 'react';

function computeDiff(aLines, bLines) {
  const m = aLines.length, n = bLines.length;
  if (m > 800 || n > 800) {
    const rows = [];
    const maxL = Math.max(m, n);
    for (let i = 0; i < maxL; i++) {
      if (i < m && i < n) {
        if (aLines[i] === bLines[i]) rows.push({ t: '=', a: aLines[i], la: i + 1, lb: i + 1 });
        else {
          rows.push({ t: '-', a: aLines[i], la: i + 1 });
          rows.push({ t: '+', b: bLines[i], lb: i + 1 });
        }
      } else if (i < m) rows.push({ t: '-', a: aLines[i], la: i + 1 });
      else rows.push({ t: '+', b: bLines[i], lb: i + 1 });
    }
    return rows;
  }

  const dp = Array.from({ length: m + 1 }, () => new Int32Array(n + 1));
  for (let i = 1; i <= m; i++)
    for (let j = 1; j <= n; j++)
      dp[i][j] = aLines[i - 1] === bLines[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);

  const ops = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && aLines[i - 1] === bLines[j - 1]) {
      ops.push({ t: '=', a: aLines[i - 1], la: i, lb: j }); i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ t: '+', b: bLines[j - 1], lb: j }); j--;
    } else {
      ops.push({ t: '-', a: aLines[i - 1], la: i }); i--;
    }
  }
  return ops.reverse();
}

export default function DiffViewer({ stored, live }) {
  const diff = useMemo(() => {
    const a = (stored || '').split('\n');
    const b = (live || '').split('\n');
    return computeDiff(a, b);
  }, [stored, live]);

  const CONTEXT = 4;
  const visible = new Set();
  diff.forEach((op, idx) => {
    if (op.t !== '=') {
      for (let k = Math.max(0, idx - CONTEXT); k <= Math.min(diff.length - 1, idx + CONTEXT); k++)
        visible.add(k);
    }
  });

  let dels = 0, ins = 0;
  let lastVisible = -1;
  const rows = [];

  diff.forEach((op, idx) => {
    if (!visible.has(idx)) { lastVisible = idx; return; }
    if (lastVisible >= 0 && lastVisible < idx - 1) {
      rows.push(
        <tr key={`skip-${idx}`}>
          <td className="ln">...</td><td className="ln">...</td>
          <td className="text-muted">@@ skipped @@</td>
        </tr>
      );
    }
    lastVisible = -2;

    const la = op.la ?? '';
    const lb = op.lb ?? '';
    if (op.t === '=') {
      rows.push(<tr key={idx}><td className="ln">{la}</td><td className="ln">{lb}</td><td>{op.a}</td></tr>);
    } else if (op.t === '-') {
      dels++;
      rows.push(<tr key={idx} className="del"><td className="ln">{la}</td><td className="ln"></td><td>{`- ${op.a}`}</td></tr>);
    } else {
      ins++;
      rows.push(<tr key={idx} className="ins"><td className="ln"></td><td className="ln">{lb}</td><td>{`+ ${op.b}`}</td></tr>);
    }
  });

  return (
    <div>
      <div className="text-muted small mb-2">{`-${dels} / +${ins} строк`}</div>
      <div className="diff-table" style={{ maxHeight: '500px', overflow: 'auto' }}>
        <table><tbody>{rows.length ? rows : <tr><td colSpan={3} className="text-muted p-3">Различий нет</td></tr>}</tbody></table>
      </div>
    </div>
  );
}
