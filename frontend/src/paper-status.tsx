import { useCallback, useEffect, useState } from 'react';
import './paper-status.css';

type HealthPayload = Record<string, any>;

type Snapshot = {
  system?: HealthPayload;
  market?: HealthPayload;
  queue?: HealthPayload;
  workers?: HealthPayload;
  reconciliation?: HealthPayload;
  audit?: HealthPayload;
  kill?: HealthPayload;
  blind?: HealthPayload;
  tests?: HealthPayload;
};

const ENDPOINTS = {
  system: '/api/paper/system-health',
  market: '/api/paper/market-health',
  queue: '/api/paper/queue-health',
  workers: '/api/paper/worker-health',
  reconciliation: '/api/paper/reconciliation',
  audit: '/api/paper/audit-health',
  kill: '/api/paper/kill-switch',
  blind: '/api/paper/blind-status',
  tests: '/api/paper/tests?limit=50',
} as const;

async function readJson(url: string): Promise<HealthPayload | null> {
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function badgeClass(value?: string | boolean | null): string {
  const text = String(value ?? '').toUpperCase();
  if (value === true || ['OK', 'PASSED', 'RUNNING', 'HEALTHY', 'FRESH', 'IDLE', 'ACTIVE'].includes(text)) {
    return 'ps-ok';
  }
  if (['WARNING', 'DEGRADED', 'LATE', 'HALT_NEW_ENTRIES'].includes(text)) return 'ps-warn';
  if (
    value === false ||
    ['FAILED', 'HALTED', 'STALE', 'CRITICAL', 'RECOVERY_PENDING', 'NEDOSTUPNÁ', 'AUDIT_FAIL'].includes(text)
  ) {
    return 'ps-bad';
  }
  return 'ps-neutral';
}

function prettyTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('sk-SK');
}

function ageLabel(iso?: string | null): string {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '—';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hours = Math.floor(min / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export default function PaperStatusPage() {
  const [data, setData] = useState<Snapshot>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const entries = await Promise.all(
        Object.entries(ENDPOINTS).map(async ([key, url]) => [key, await readJson(url)] as const),
      );
      const next: Snapshot = {};
      for (const [key, value] of entries) {
        if (value) (next as any)[key] = value;
      }
      setData(next);
      setRefreshedAt(new Date().toISOString());
      if (!entries.some(([, v]) => v)) {
        setError('Paper health endpointy neodpovedajú. Fail-closed.');
      }
    } catch {
      setError('Nepodarilo sa načítať paper status.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(id);
  }, [autoRefresh, refresh]);

  const ks = data.kill?.kill_switch || data.system?.kill_switch || {};
  const blind = data.blind?.blind || {};
  const recon = data.reconciliation?.reconciliation || {};
  const latest = recon.latest || {};
  const issueCounts = Array.isArray(recon.open_issue_counts) ? recon.open_issue_counts : [];
  const workers = Array.isArray(data.workers?.workers) ? data.workers.workers : [];
  const marketCounts = data.market?.status_counts || {};
  const backlog = Number(
    data.tests?.summary?.queue_backlog ?? data.queue?.queue?.current_backlog ?? 0,
  );
  const auditPassed = data.audit?.audit_integrity_passed ?? latest.audit_integrity_passed;
  const durable = Boolean(data.system?.persistence?.durable);

  const runs: Array<{
    id: string;
    kind?: string;
    at?: string;
    origin?: string;
    outcome?: string;
    lag_ms?: number | null;
  }> = Array.isArray(data.tests?.runs) ? data.tests.runs : [];

  const ticks: Array<{
    id: string;
    at?: string;
    origin?: string;
    outcome?: string;
    lag_ms?: number | null;
  }> = Array.isArray(data.tests?.ticks) ? data.tests.ticks : [];

  const tableRows =
    ticks.length > 0
      ? ticks.map((t) => ({ ...t, kind: 'tick' }))
      : runs.map((r) => ({ ...r, kind: r.kind || 'run' }));

  return (
    <div className="ps-page">
      <header className="ps-header">
        <div className="ps-brand">
          <a href="/" className="ps-back">
            ← NoFomo
          </a>
          <div>
            <span className="ps-eyebrow">PAPER READ-ONLY</span>
            <h1>Paper status</h1>
            <p>Systémový prehľad bez PnL, PF a trade detailov (blind-safe).</p>
          </div>
        </div>
        <div className="ps-actions">
          <label className="ps-toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto 30s
          </label>
          <button className="ps-btn" onClick={refresh} disabled={loading}>
            {loading ? 'Obnovujem…' : 'Obnoviť'}
          </button>
        </div>
      </header>

      {error && (
        <div className="ps-error" role="status">
          {error}
        </div>
      )}

      <section className="ps-grid" aria-label="Systémové karty">
        <article className="ps-card">
          <span>KILL SWITCH</span>
          <strong className={badgeClass(ks.state)}>{ks.state || '—'}</strong>
          <small>{ks.reason_code || 'bez dôvodu'}</small>
          <small className="ps-muted">upd: {prettyTime(ks.updated_at)}</small>
        </article>

        <article className="ps-card">
          <span>PERSISTENCIA</span>
          <strong className={badgeClass(durable)}>{durable ? 'SUPABASE' : 'NEDOSTUPNÁ'}</strong>
          <small>{data.system?.persistence?.mode || '—'}</small>
          <small className="ps-muted">live trading: vypnutý</small>
        </article>

        <article className="ps-card">
          <span>RECONCILIATION</span>
          <strong className={badgeClass(latest.status)}>{latest.status || '—'}</strong>
          <small>{latest.completed_at ? prettyTime(latest.completed_at) : 'bez dokončeného runu'}</small>
          <small className="ps-muted">vek: {ageLabel(latest.completed_at)}</small>
        </article>

        <article className="ps-card">
          <span>AUDIT</span>
          <strong className={badgeClass(auditPassed)}>
            {auditPassed === true ? 'PASS' : auditPassed === false ? 'FAIL' : '—'}
          </strong>
          <small>integrita event streamov</small>
        </article>

        <article className="ps-card">
          <span>QUEUE BACKLOG</span>
          <strong className={backlog > 0 ? 'ps-warn' : 'ps-ok'}>{backlog}</strong>
          <small>aktuálny backlog, nie história obchodov</small>
        </article>

        <article className="ps-card">
          <span>MARKET DATA</span>
          <strong className={badgeClass(data.market?.status)}>
            {String(data.market?.status || '—').toUpperCase()}
          </strong>
          <small>
            H {marketCounts.HEALTHY || 0} · D {marketCounts.DEGRADED || 0} · S {marketCounts.STALE || 0} · X{' '}
            {marketCounts.HALTED || 0}
          </small>
        </article>
      </section>

      <section className="ps-two">
        <div className="ps-box">
          <h2>Worker health</h2>
          {workers.length ? (
            <ul className="ps-list">
              {workers.map((worker: any) => (
                <li key={worker.worker_name || worker.name}>
                  <span>
                    <b>{worker.worker_name || worker.name}</b>
                    <small>
                      last success: {worker.last_success_age_category || ageLabel(worker.last_success_at) || '—'}
                    </small>
                  </span>
                  <strong className={badgeClass(worker.status)}>{worker.status || '—'}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ps-muted">Workery ešte nemajú heartbeat. Scheduler nie je aktivovaný / bez reportu.</p>
          )}
        </div>

        <div className="ps-box">
          <h2>Reconciliation issues</h2>
          {issueCounts.length ? (
            <ul className="ps-list">
              {issueCounts.map((item: any) => (
                <li key={`${item.check_code}:${item.severity}`}>
                  <span>
                    <b>{item.check_code}</b>
                    <small>{item.severity}</small>
                  </span>
                  <strong className={badgeClass(item.severity)}>{item.count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ps-muted">Žiadne otvorené WARNING/CRITICAL integrity issues.</p>
          )}
        </div>
      </section>

      <section className="ps-box ps-blind">
        <div className="ps-blind-head">
          <span>BLIND FORWARD TEST</span>
          <strong className="ps-ok">ACTIVE</strong>
        </div>
        <p>
          {blind.strategy_version_id || 'TEST-SPEC-002'}
          {blind.blind_test_id ? ` · ${blind.blind_test_id}` : ''}
        </p>
        <small>
          Skryté do {prettyTime(blind.blind_until)}. Bez PnL, PF, expectancy, win rate, side, price, quantity.
        </small>
      </section>

      <section className="ps-box">
        <div className="ps-table-head">
          <h2>Ticky / behy / outcomes</h2>
          <span className="ps-pill">
            {data.tests
              ? `runs ${data.tests.summary?.run_count ?? tableRows.length} · ticks ${data.tests.summary?.tick_count ?? 0}`
              : '/api/paper/tests ešte nedostupné'}
          </span>
        </div>
        {tableRows.length ? (
          <div className="ps-table-wrap">
            <table className="ps-table">
              <thead>
                <tr>
                  <th>Čas</th>
                  <th>Typ</th>
                  <th>ID</th>
                  <th>Pôvod</th>
                  <th>Outcome</th>
                  <th>lag_ms</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => (
                  <tr key={`${row.kind}-${row.id}-${row.at}`}>
                    <td>{prettyTime(row.at)}</td>
                    <td>{row.kind || '—'}</td>
                    <td className="ps-mono">{String(row.id).slice(0, 12)}</td>
                    <td>{row.origin || '—'}</td>
                    <td>
                      <strong className={badgeClass(row.outcome)}>{row.outcome || '—'}</strong>
                    </td>
                    <td className="ps-mono">{row.lag_ms ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="ps-empty">
            <p>
              {data.tests
                ? 'Zatiaľ žiadne worker/reconciliation behy ani meracie ticky.'
                : 'Endpoint /api/paper/tests ešte nie je nasadený (404). Po deployi backendu sa tu zobrazia behy.'}
            </p>
            <p className="ps-muted">
              Read-only · blind-safe · bez PnL. Meracie ticky (P4) sa napoja do poľa <code>ticks</code>.
            </p>
          </div>
        )}
      </section>

      <footer className="ps-foot">
        <span>
          Reálne obchodovanie: <b>VYPNUTÉ</b>
        </span>
        <span>Posledné obnovenie UI: {prettyTime(refreshedAt)}</span>
        <span>Read-only · bez zápisu do DB</span>
      </footer>
    </div>
  );
}
