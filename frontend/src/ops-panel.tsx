import { useEffect, useState } from 'react';
import './ops.css';

type ApiState = {
  system?: any;
  market?: any;
  queue?: any;
  workers?: any;
  reconciliation?: any;
  audit?: any;
  kill?: any;
  blind?: any;
};

const badgeClass = (value?: string | boolean | null) => {
  const text = String(value ?? '').toUpperCase();
  if (value === true || ['OK', 'PASSED', 'RUNNING', 'HEALTHY', 'FRESH', 'IDLE'].includes(text)) return 'ops-ok';
  if (['WARNING', 'DEGRADED', 'LATE', 'HALT_NEW_ENTRIES'].includes(text)) return 'ops-warn';
  if (value === false || ['FAILED', 'HALTED', 'STALE', 'CRITICAL', 'RECOVERY_PENDING'].includes(text)) return 'ops-bad';
  return 'ops-neutral';
};

const prettyTime = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('sk-SK');
};

const readJson = async (url: string) => {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${url}:${response.status}`);
  return response.json();
};

export default function OpsPanel() {
  const [data, setData] = useState<ApiState>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError('');
    try {
      const [
        system,
        market,
        queue,
        workers,
        reconciliation,
        audit,
        kill,
        blind,
      ] = await Promise.all([
        readJson('/api/paper/system-health'),
        readJson('/api/paper/market-health'),
        readJson('/api/paper/queue-health'),
        readJson('/api/paper/worker-health'),
        readJson('/api/paper/reconciliation'),
        readJson('/api/paper/audit-health'),
        readJson('/api/paper/kill-switch'),
        readJson('/api/paper/blind-status'),
      ]);
      setData({ system, market, queue, workers, reconciliation, audit, kill, blind });
      setRefreshedAt(new Date().toISOString());
    } catch {
      setError('Operačný stav sa nepodarilo načítať. Paper infra zostáva fail-closed.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const ks = data.kill?.kill_switch || data.system?.kill_switch || {};
  const blind = data.blind?.blind || {};
  const recon = data.reconciliation?.reconciliation || {};
  const latest = recon.latest || {};
  const issueCounts = Array.isArray(recon.open_issue_counts) ? recon.open_issue_counts : [];
  const workers = Array.isArray(data.workers?.workers) ? data.workers.workers : [];
  const marketCounts = data.market?.status_counts || {};
  const backlog = Number(data.queue?.queue?.current_backlog || 0);
  const auditPassed = data.audit?.audit_integrity_passed;

  return (
    <section className="ops-panel" aria-label="Paper execution infraštruktúra">
      <div className="ops-head">
        <div>
          <span className="eyebrow">PAPER EXECUTION INFRA</span>
          <h3>Prevádzkový stav</h3>
          <p>
            Len bezpečné systémové metriky. Výkon TEST-SPEC-002 zostáva skrytý do konca blind testu.
          </p>
        </div>
        <button className="ghost" onClick={refresh} disabled={loading}>
          {loading ? 'Obnovujem…' : 'Obnoviť stav'}
        </button>
      </div>

      {error && <div className="ops-error" role="status">{error}</div>}

      <div className="ops-grid">
        <article>
          <span>KILL SWITCH</span>
          <strong className={badgeClass(ks.state)}>{ks.state || '—'}</strong>
          <small>{ks.reason_code || 'bez dôvodu'}</small>
        </article>

        <article>
          <span>PERSISTENCIA</span>
          <strong className={badgeClass(Boolean(data.system?.persistence?.durable))}>
            {data.system?.persistence?.durable ? 'SUPABASE DURABLE' : 'NEDOSTUPNÁ'}
          </strong>
          <small>live trading: vypnutý</small>
        </article>

        <article>
          <span>RECONCILIATION</span>
          <strong className={badgeClass(latest.status)}>{latest.status || '—'}</strong>
          <small>{latest.completed_at ? prettyTime(latest.completed_at) : 'zatiaľ bez dokončeného runu'}</small>
        </article>

        <article>
          <span>AUDIT</span>
          <strong className={badgeClass(auditPassed)}>
            {auditPassed === true ? 'PASS' : auditPassed === false ? 'FAIL' : '—'}
          </strong>
          <small>integrita event streamov</small>
        </article>

        <article>
          <span>QUEUE</span>
          <strong className={backlog > 0 ? 'ops-warn' : 'ops-ok'}>{backlog}</strong>
          <small>aktuálny backlog, nie história obchodov</small>
        </article>

        <article>
          <span>MARKET DATA</span>
          <strong className={badgeClass(data.market?.status)}>{String(data.market?.status || '—').toUpperCase()}</strong>
          <small>
            H {marketCounts.HEALTHY || 0} · D {marketCounts.DEGRADED || 0} · S {marketCounts.STALE || 0} · X {marketCounts.HALTED || 0}
          </small>
        </article>
      </div>

      <div className="ops-two-col">
        <div className="ops-box">
          <h4>Worker health</h4>
          {workers.length ? (
            <ul>
              {workers.map((worker: any) => (
                <li key={worker.worker_name}>
                  <span>
                    <b>{worker.worker_name}</b>
                    <small>posledný úspech: {worker.last_success_age_category || '—'}</small>
                  </span>
                  <strong className={badgeClass(worker.status)}>{worker.status || '—'}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ops-muted">Workery ešte nemajú heartbeat. Scheduler nie je aktivovaný.</p>
          )}
        </div>

        <div className="ops-box">
          <h4>Reconciliation issues</h4>
          {issueCounts.length ? (
            <ul>
              {issueCounts.map((item: any) => (
                <li key={`${item.check_code}:${item.severity}`}>
                  <span><b>{item.check_code}</b><small>{item.severity}</small></span>
                  <strong className={badgeClass(item.severity)}>{item.count}</strong>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ops-muted">Žiadne otvorené WARNING/CRITICAL integrity issues.</p>
          )}
        </div>
      </div>

      <div className="ops-blind">
        <div>
          <span>BLIND FORWARD TEST</span>
          <strong className="ops-ok">ACTIVE</strong>
        </div>
        <p>
          {blind.strategy_version_id || 'TEST-SPEC-002'} · skryté do {prettyTime(blind.blind_until)}
        </p>
        <small>
          Bez PnL, PF, expectancy, win rate, side, price, quantity, per-trade timestampov alebo exposure metrík.
        </small>
      </div>

      <footer className="ops-foot">
        <span>Reálne obchodovanie: <b>VYPNUTÉ</b></span>
        <span>Posledné UI obnovenie: {prettyTime(refreshedAt)}</span>
        <span>Ovládanie kill switchu nie je dostupné z browsera.</span>
      </footer>
    </section>
  );
}
