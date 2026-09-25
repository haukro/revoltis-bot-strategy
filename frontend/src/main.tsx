import { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { normalizeOptimizerResult, combineOptimizerResults } from './optimizer-result';
import OpsPanel from './ops-panel';
import './styles.css';
import './presets.css';
import './market.css';
import './run-status.css';
import './settings-info.css';
import './simulation-controls.css';
import './optimizer.css';

type Config = Record<string, string | number | string[]>;
const coins = ['PEPE/USDT', 'DOGE/USDT', 'WIF/USDT', 'BONK/USDT', 'SUI/USDT', 'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'XEC/USDT', 'ZEC/USDT'];
const historicalRangeLabels: Record<number, string> = { 1: '1 hodina', 6: '6 hodín', 24: '24 hodín', 168: '7 dní', 336: '14 dní', 720: '30 dní' };
const fields: Record<string, string> = { timeframe: 'Sviečka', initial_capital: 'Počiatočný kapitál', stake_amount: 'Suma na obchod', max_open_trades: 'Otvorené pozície', daily_trade_limit: 'Denný limit obchodov', rsi_oversold: 'RSI prepredané', atr_min_percent: 'Min. ATR volatilita', atr_max_percent: 'Max. ATR volatilita', min_volume_ratio: 'Pomer objemu', min_quote_volume_usdt: 'Min. objem', rebound_min_percent: 'Min. otočenie', rebound_max_percent: 'Max. otočenie', stop_loss_percent: 'Stop-loss', trailing_start_percent: 'Začiatok trailing', trailing_distance_percent: 'Trailing odstup', bb_period: 'Bollinger perióda', bb_deviation: 'Bollinger odchýlka', rsi_period: 'RSI perióda', atr_period: 'ATR perióda' };
const groups = [['Kapitál a limity', ['timeframe', 'initial_capital', 'stake_amount', 'max_open_trades', 'daily_trade_limit']], ['Vstup do obchodu', ['rsi_oversold', 'atr_min_percent', 'atr_max_percent', 'min_volume_ratio', 'min_quote_volume_usdt', 'rebound_min_percent', 'rebound_max_percent']], ['Výstup a ochrana', ['stop_loss_percent', 'trailing_start_percent', 'trailing_distance_percent']], ['Rozšírené indikátory', ['bb_period', 'bb_deviation', 'rsi_period', 'atr_period']]] as const;
const presets: Record<string, { title: string, description: string, settings: Partial<Config> }> = {
  conservative: { title: 'Konzervatívna', description: 'Menej vstupov, silnejšie potvrdenie a menšia pozícia.', settings: { timeframe: '5m', stake_amount: 25, max_open_trades: 1, daily_trade_limit: 4, bb_period: 20, bb_deviation: 2.2, rsi_period: 14, rsi_oversold: 30, atr_period: 14, atr_min_percent: 0.2, atr_max_percent: 2, min_volume_ratio: 1.2, min_quote_volume_usdt: 50000, rebound_min_percent: 0.5, rebound_max_percent: 0.8, stop_loss_percent: 2.5, trailing_start_percent: 1.5, trailing_distance_percent: 0.4 } },
  growing: { title: 'Rastúca', description: 'Vyvážený profil pre pravidelné obchody a kontrolované riziko.', settings: { timeframe: '3m', stake_amount: 40, max_open_trades: 1, daily_trade_limit: 8, bb_period: 20, bb_deviation: 2, rsi_period: 14, rsi_oversold: 35, atr_period: 14, atr_min_percent: 0.15, atr_max_percent: 4, min_volume_ratio: 0.9, min_quote_volume_usdt: 20000, rebound_min_percent: 0.3, rebound_max_percent: 0.6, stop_loss_percent: 4, trailing_start_percent: 1.2, trailing_distance_percent: 0.5 } },
  risky: { title: 'Riziková', description: 'Viac príležitostí, rýchle sviečky a širšie rizikové hranice.', settings: { timeframe: '1m', stake_amount: 30, max_open_trades: 2, daily_trade_limit: 15, bb_period: 16, bb_deviation: 1.7, rsi_period: 10, rsi_oversold: 40, atr_period: 10, atr_min_percent: 0.1, atr_max_percent: 8, min_volume_ratio: 0.6, min_quote_volume_usdt: 5000, rebound_min_percent: 0.15, rebound_max_percent: 0.4, stop_loss_percent: 6, trailing_start_percent: 0.8, trailing_distance_percent: 0.35 } },
};
const settingInfo: Record<string, { purpose: string, effect: string, mechanism: string }> = {
  selected_pairs: { purpose: 'Vybrané coiny', effect: 'Určujú, z ktorých trhov aplikácia načíta sviečky a ktoré zahrnie do simulácie.', mechanism: 'Každý pár je napríklad PEPE/USDT. Viac párov prináša viac príležitostí, ale môže zvyšovať počet súčasných signálov.' },
  timeframe: { purpose: 'Dĺžka sviečky', effect: 'Mení rýchlosť reakcie stratégie a množstvo trhového šumu.', mechanism: '1m reaguje najrýchlejšie, ale je hlučnejšia. 3m až 5m sú vyváženejšie. Dlhšie sviečky dávajú menej, ale stabilnejších signálov.' },
  initial_capital: { purpose: 'Počiatočný kapitál', effect: 'Nastavuje simulovanú hodnotu portfólia na začiatku testu.', mechanism: 'Je to iba virtuálny kapitál v USDT. Výsledok stratégie sa počíta vzhľadom na túto hodnotu.' },
  stake_amount: { purpose: 'Suma na obchod', effect: 'Určuje, koľko USDT simulácia vloží do jedného vstupu.', mechanism: 'Vyššia suma zväčší zisk aj stratu jedného obchodu. Nemala by prekročiť dostupný simulovaný kapitál.' },
  max_open_trades: { purpose: 'Otvorené pozície', effect: 'Obmedzuje, koľko coinov môže stratégia držať naraz.', mechanism: 'Hodnota 1 chráni kapitál pred rozdelením medzi viac rizikových pozícií. Vyššia hodnota zvyšuje súčasné riziko.' },
  daily_trade_limit: { purpose: 'Denný limit obchodov', effect: 'Brzdí príliš časté vstupy počas jedného dňa.', mechanism: 'Po dosiahnutí limitu stratégia nové vstupy nepridá, ale otvorené pozície môže ďalej uzatvárať.' },
  rsi_oversold: { purpose: 'RSI prepredané', effect: 'Určuje, aký silný pokles musí RSI potvrdiť pred hľadaním vstupu.', mechanism: 'Nižšie číslo znamená prísnejší vstup a menej obchodov. Vyššie číslo znamená viac vstupov, ale slabšie potvrdenie poklesu.' },
  atr_min_percent: { purpose: 'Minimálna ATR volatilita', effect: 'Vylučuje príliš pokojné trhy, kde pohyb nemusí pokryť poplatky.', mechanism: 'ATR meria typický rozsah pohybu sviečok. Ak je pod hranicou, stratégia nevstúpi.' },
  atr_max_percent: { purpose: 'Maximálna ATR volatilita', effect: 'Vylučuje extrémne rozkolísané situácie.', mechanism: 'Ak ATR prekročí hranicu, trh sa považuje za príliš nepredvídateľný pre tento model.' },
  min_volume_ratio: { purpose: 'Pomer objemu', effect: 'Kontroluje, či má aktuálna sviečka dostatočný objem oproti priemeru.', mechanism: 'Hodnota 1 znamená aspoň priemerný objem. Nižšia hodnota pustí viac obchodov, vyššia vyžaduje silnejší záujem trhu.' },
  min_quote_volume_usdt: { purpose: 'Minimálny objem', effect: 'Filtruje sviečky s nízkou obchodovanou hodnotou v USDT.', mechanism: 'Vyššia hranica obmedzí menej likvidné pohyby, ktoré môžu mať horší spread a nepresné vyplnenie.' },
  rebound_min_percent: { purpose: 'Minimálne otočenie', effect: 'Vyžaduje, aby sa cena po poklese začala vracať nahor.', mechanism: 'Znižuje riziko nákupu počas pokračujúceho pádu. Vyššia hodnota je prísnejšia a vytvorí menej vstupov.' },
  rebound_max_percent: { purpose: 'Maximálne otočenie', effect: 'Obmedzuje príliš neskorý vstup po prudkom odraze.', mechanism: 'Ak sa cena odrazí príliš výrazne, stratégia už nevstúpi, aby nekupovala až po veľkej časti pohybu.' },
  stop_loss_percent: { purpose: 'Stop-loss', effect: 'Obmedzuje maximálnu simulovanú stratu jednej pozície.', mechanism: 'Ak cena klesne od vstupu o zadané percento, simulácia pozíciu uzavrie. Menšie číslo chráni rýchlejšie, ale môže častejšie ukončiť obchod.' },
  trailing_start_percent: { purpose: 'Začiatok trailing profitu', effect: 'Určuje, od akého zisku začne stratégia chrániť rastúcu pozíciu.', mechanism: 'Kým zisk nedosiahne túto hranicu, trailing výstup sa nezapne.' },
  trailing_distance_percent: { purpose: 'Trailing odstup', effect: 'Určuje, aký pokles od najvyššej ceny ukončí ziskový obchod.', mechanism: 'Menší odstup uzatvára zisk skôr. Väčší odstup dáva rastu viac priestoru, ale môže vrátiť viac už dosiahnutého zisku.' },
  bb_period: { purpose: 'Bollinger perióda', effect: 'Určuje počet sviečok pre výpočet priemeru a pásiem.', mechanism: 'Kratšia perióda reaguje rýchlejšie. Dlhšia perióda filtruje krátkodobý šum.' },
  bb_deviation: { purpose: 'Bollinger odchýlka', effect: 'Určuje šírku pásiem okolo priemernej ceny.', mechanism: 'Vyššia hodnota vyžaduje výraznejší pokles pod pásmo a vytvára menej vstupov. Nižšia hodnota je citlivejšia.' },
  rsi_period: { purpose: 'RSI perióda', effect: 'Mení citlivosť ukazovateľa RSI.', mechanism: 'Kratšia perióda reaguje rýchlejšie na pohyb ceny. Dlhšia perióda dáva plynulejší, pomalší signál.' },
  atr_period: { purpose: 'ATR perióda', effect: 'Mení, z koľkých sviečok sa počíta bežná volatilita.', mechanism: 'Kratšia perióda reaguje na aktuálne zmeny volatility rýchlejšie. Dlhšia je stabilnejšia.' },
};
const inputDateTime = (date: Date) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const calculatePairMetrics = (trades: any[], initialCapital: number) => { const closed = [...trades].filter(trade => trade.status === 'closed').sort((a, b) => String(a.closed_at || a.opened_at || '').localeCompare(String(b.closed_at || b.opened_at || ''))); const realized = closed.reduce((sum, trade) => sum + Number(trade.profit_usdt || 0), 0); const wins = closed.filter(trade => Number(trade.profit_usdt || 0) > 0).length; let equity = initialCapital; let peak = equity; let drawdown = 0; closed.forEach(trade => { equity += Number(trade.profit_usdt || 0); peak = Math.max(peak, equity); drawdown = Math.max(drawdown, peak ? (peak - equity) / peak * 100 : 0); }); return { initial_capital: initialCapital, portfolio_value: Number((initialCapital + realized).toFixed(4)), realized_profit: Number(realized.toFixed(4)), unrealized_profit: 0, closed_trades: closed.length, win_rate: closed.length ? Number((wins / closed.length * 100).toFixed(1)) : 0, max_drawdown_percent: Number(drawdown.toFixed(2)) }; };
const calculatePairCurve = (trades: any[], initialCapital: number) => { let equity = initialCapital; const curve = [{ time: 'start', value: equity }]; [...trades].filter(trade => trade.status === 'closed').sort((a, b) => String(a.closed_at || a.opened_at || '').localeCompare(String(b.closed_at || b.opened_at || ''))).forEach(trade => { equity += Number(trade.profit_usdt || 0); curve.push({ time: trade.closed_at || trade.opened_at || 'start', value: Number(equity.toFixed(4)) }); }); return curve; };

function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [dashboard, setDashboard] = useState<any>(null);
  const [versions, setVersions] = useState<any[]>([]);
  const [backtests, setBacktests] = useState<any[]>([]);
  const [left, setLeft] = useState(''); const [right, setRight] = useState('');
  const [comparison, setComparison] = useState<any>(null); const [message, setMessage] = useState('');
  const [activePreset, setActivePreset] = useState('');
  const [marketPair, setMarketPair] = useState('');
  const [market, setMarket] = useState<any>(null);
  const [marketError, setMarketError] = useState('');
  const [scanner, setScanner] = useState<any>(null);
  const [scannerError, setScannerError] = useState('');
  const [scannerLoading, setScannerLoading] = useState(false);
  const [universeProposal, setUniverseProposal] = useState<any>(null);
  const [universeLoading, setUniverseLoading] = useState(false);
  const [universeSaving, setUniverseSaving] = useState(false);
  const [activeUniverse, setActiveUniverse] = useState<any>(null);
  const [paperBotRunning, setPaperBotRunning] = useState(false);
  const [simulation, setSimulation] = useState<any>(null);
  const [lastRun, setLastRun] = useState<any>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [runStatus, setRunStatus] = useState<'idle' | 'running' | 'completed' | 'stopped' | 'failed'>('idle');
  const [testStart, setTestStart] = useState(inputDateTime(new Date()));
  const [testEnd, setTestEnd] = useState(inputDateTime(new Date(Date.now() + 24 * 60 * 60 * 1000)));
  const [timeLimited, setTimeLimited] = useState(false);
  const [historicalMode, setHistoricalMode] = useState(false);
  const [historicalHours, setHistoricalHours] = useState<1 | 6 | 24 | 168 | 336 | 720>(24);
  const [chartRange, setChartRange] = useState<'hour' | 'day' | 'week'>('day');
  const [optimizer, setOptimizer] = useState<any>(null);
  const [replayAvailability, setReplayAvailability] = useState<any>(null);
  const [optimizerDays, setOptimizerDays] = useState<1 | 7 | 14 | 30 | 90>(7);
  const [optimizerTrials, setOptimizerTrials] = useState(3);
  const [optimizerRunning, setOptimizerRunning] = useState(false);
  const [installPrompt, setInstallPrompt] = useState<any>(null);
  const optimizerTimerRef = useRef<number | null>(null);
  const runAbortRef = useRef<AbortController | null>(null);
  const liveTimerRef = useRef<number | null>(null);
  const liveEnabledRef = useRef(false);
  const load = async () => {
    const [overview, savedVersions, savedBacktests, latestOptimizer] = await Promise.all([fetch('/api/dashboard'), fetch('/api/strategy-versions'), fetch('/api/backtests'), fetch('/api/optimizer-latest')]);
    if (!overview.ok) throw new Error('dashboard_failed');
    const data = await overview.json(); setDashboard(data); setConfig(data.strategy); setActiveUniverse(data.active_universe || null); setMarketPair(current => data.strategy.selected_pairs.includes(current) ? current : data.strategy.selected_pairs[0] || ''); setLastRun(data.last_simulation); if (data.last_simulation) { setSimulation({ metrics: data.last_simulation.summary, equity_curve: data.last_simulation.progress?.equity_curve || [], per_pair_metrics: data.last_simulation.progress?.per_pair_metrics || {}, per_pair_equity_curves: data.last_simulation.progress?.per_pair_equity_curves || {}, data_coverage: data.last_simulation.progress?.data_coverage || {} }); } setVersions(savedVersions.ok ? await savedVersions.json() : []); const tests = savedBacktests.ok ? await savedBacktests.json() : []; setBacktests(tests); if (tests.length > 1) { setLeft(tests[1].id); setRight(tests[0].id); } if (latestOptimizer.ok) { const saved = await latestOptimizer.json(); if (saved) setOptimizer(saved); }
  };
  useEffect(() => { load().catch(() => setMessage('Aplikáciu sa nepodarilo načítať. Skontroluj, či beží API.')); }, []);
  useEffect(() => {
    let active = true;
    fetch('/api/optimizer/validation-replay').then(async response => {
      if (!response.ok) throw new Error('replay_preflight_failed');
      const result = await response.json();
      if (active) setReplayAvailability(result);
    }).catch(() => { if (active) setReplayAvailability({ status: 'replay_unavailable', reason: 'api_unavailable' }); });
    return () => { active = false; };
  }, []);
  const scanOkx = async () => {
    setScannerLoading(true); setScannerError('');
    try {
      const response = await fetch('/api/market/scan?quote=USDT&limit=12');
      if (!response.ok) throw new Error('scan_failed');
      setScanner(await response.json());
    } catch {
      setScannerError('OKX trhy sa momentálne nepodarilo načítať.');
    } finally { setScannerLoading(false); }
  };
  useEffect(() => { scanOkx(); }, []);
  useEffect(() => { if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => undefined); const capture = (event: Event) => { event.preventDefault(); setInstallPrompt(event); }; window.addEventListener('beforeinstallprompt', capture); return () => window.removeEventListener('beforeinstallprompt', capture); }, []);
  useEffect(() => {
    const pairs = (config?.selected_pairs || []) as string[];
    const pair = marketPair || pairs[0];
    if (!pair || !config?.timeframe) return;
    if (!marketPair) setMarketPair(pair);
    setMarketError('');
    const minutes = Number(String(config.timeframe).replace('m', '')) || 3;
    const dayLimit = Math.min(1500, Math.ceil(24 * 60 / minutes));
    fetch(`/api/market/candles?pair=${encodeURIComponent(pair)}&timeframe=${encodeURIComponent(String(config.timeframe))}&limit=${dayLimit}`)
      .then(async response => response.ok ? response.json() : Promise.reject(await response.text()))
      .then(setMarket)
      .catch(() => { setMarket(null); setMarketError('Sviečky z burzy sa momentálne nepodarilo načítať.'); });
  }, [config?.timeframe, config?.selected_pairs, marketPair]);
  const update = (key: string, value: any) => { setConfig(current => ({ ...(current as Config), [key]: value })); setActivePreset(''); };
  const applyPreset = (key: string) => { setConfig(current => ({ ...(current as Config), ...(presets[key].settings as Config) })); setActivePreset(key); setMessage(`Profil „${presets[key].title}“ bol nastavený. Hodnoty môžeš ďalej upraviť.`); };
  const save = async () => { const response = await fetch('/api/strategy', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) }); setMessage(response.ok ? 'Nastavenie je uložené.' : 'Nastavenie sa nepodarilo uložiť.'); };
  const saveVersion = async () => { const name = window.prompt('Názov verzie stratégie:', `Verzia ${new Date().toLocaleDateString('sk-SK')}`); if (!name) return; const note = window.prompt('Čo sa v tejto verzii zmenilo?') || ''; const response = await fetch('/api/strategy-versions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, note, settings: config }) }); if (response.ok) { setMessage('Verzia stratégie je uložená.'); load(); } else setMessage('Verziu sa nepodarilo uložiť.'); };
  const proposeUniverse = async () => {
    if (!config || universeLoading) return;
    setUniverseLoading(true); setUniverseProposal(null); setMessage('OKX kontroluje likviditu, knihu objednávok, režim trhu a korelácie…');
    try {
      const timeframe = String(config.timeframe) === '15m' ? '15m' : '5m';
      const response = await fetch('/api/market/universe/pick', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ timeframe, max_picks: 5, trade_notional_usdt: Number(config.stake_amount || 50), lock_hours: 12 }) });
      if (!response.ok) throw new Error(await response.text());
      const proposal = await response.json(); setUniverseProposal(proposal);
      setMessage(proposal.status === 'ok' ? 'Návrh je pripravený. Coiny ešte neboli zmenené.' : proposal.message);
    } catch { setMessage('Návrh z OKX sa nepodarilo vytvoriť. Pôvodný zoznam zostal bez zmeny.'); }
    finally { setUniverseLoading(false); }
  };
  const acceptUniverse = async () => {
    if (!config || universeProposal?.status !== 'ok' || universeSaving || optimizerRunning || isRunning) return;
    setUniverseSaving(true);
    try {
    const pairs = universeProposal.picks.map((item: any) => item.pair);
    const nextConfig = { ...config, selected_pairs: pairs };
    const universe = { proposal_id: universeProposal.proposal_id, source: universeProposal.source, accepted_at: new Date().toISOString(), lock: universeProposal.lock, pairs, method: universeProposal.method, fee_schedule: 'okx_global_regular', cost_profiles: Object.fromEntries(universeProposal.picks.map((item: any) => [item.pair, item.cost_components])) };
    const versionResponse = await fetch('/api/strategy-versions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: `OKX universe ${new Date().toLocaleDateString('sk-SK')}`, note: `Automatický návrh ${universeProposal.proposal_id}; uzamknutý na ${universeProposal.lock.hours} h.`, settings: nextConfig, universe }) });
    if (!versionResponse.ok) { setMessage('Návrh sa nepodarilo uložiť ako novú verziu stratégie.'); return; }
    const saved = await versionResponse.json();
    setConfig(saved.strategy); setActiveUniverse(saved.active_universe); setMarketPair(pairs[0]); setUniverseProposal(null);
    setVersions(current => [saved.version, ...current]);
    setMessage(`Návrh bol použitý, uložený a zamknutý: ${pairs.join(', ')}.`);
    } catch { setMessage('Návrh sa nepodarilo uložiť. Skús to znova.'); }
    finally { setUniverseSaving(false); }
  };
  const compare = async () => { if (!left || !right || left === right) { setMessage('Vyber dve rozdielne verzie backtestu.'); return; } const response = await fetch(`/api/backtests/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`); if (response.ok) setComparison(await response.json()); else setMessage('Backtesty sa nepodarilo porovnať.'); };
  const exportConfig = async () => { if (!optimizer?.result || !normalizeOptimizerResult(optimizer.result).qualified) return; const response = await fetch('/api/export/freqtrade', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) }); if (!response.ok) { setMessage('Freqtrade export nie je dostupný bez overeného kandidáta.'); return; } const blob = new Blob([JSON.stringify(await response.json(), null, 2)], { type: 'application/json' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'revoltis-dry-run-config.json'; link.click(); URL.revokeObjectURL(link.href); };
  const startOptimizer = async () => {
    if (!config || optimizerRunning || universeSaving) return;
    if (dashboard?.persistence?.production_ready === false) { setMessage('persistence_unavailable: Najprv pripoj trvalé úložisko Supabase.'); return; }
    setOptimizerRunning(true); setOptimizer({ status: 'running', progress: 0, message: 'Pripravujem optimalizáciu…' });
    try {
      const contextResponse = await fetch('/api/strategy-context', { cache: 'no-store' });
      if (!contextResponse.ok) throw new Error('Zamknutú verziu sa nepodarilo načítať. Skús obnoviť stránku.');
      const context = await contextResponse.json();
      const lock = context.active_universe;
      if (!lock?.version_id || !lock.pairs?.length) throw new Error('Chýba aktívny zamknutý zoznam coinov. Klikni na „Navrhnúť z OKX“ a potom „Použiť návrh“.');
      // Freeze one server version for the whole multi-request run. The backend
      // rejects a changed version rather than mixing different universes.
      if ([...(config.selected_pairs as string[])].sort().join(',') !== [...lock.pairs].sort().join(',')) throw new Error('Karty nie sú locknutý universe. Obnov stránku.');
      const optimizationPairs: string[] = [...lock.pairs];
      const optimizationConfig = { ...config, selected_pairs: optimizationPairs };
      setActiveUniverse(lock); setConfig(optimizationConfig);
      setMarketPair(current => optimizationPairs.includes(current) ? current : optimizationPairs[0]);
      if (JSON.stringify(config.selected_pairs) !== JSON.stringify(optimizationPairs)) {
        setMessage(`Výber bol obnovený zo zamknutej verzie: ${optimizationPairs.join(', ')}.`);
      }
      // Vercel must finish an optimizer request before returning it. Running all
      // markets in one request exceeds the function limit, so process one coin
      // at a time and keep the best independently validated result.
      const completed: any[] = [];
      const completedJobIds: string[] = [];
      for (let index = 0; index < optimizationPairs.length; index += 1) {
        const pair = optimizationPairs[index];
        setOptimizer({ version_id: lock.version_id, locked_pairs: optimizationPairs, status: 'running', progress: Math.round(index / optimizationPairs.length * 100), message: `Testujem ${pair} (${index + 1}/${optimizationPairs.length})` });
        const response = await fetch('/api/optimizer/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version_id: lock.version_id, settings: optimizationConfig, pairs: [pair], timeframes: ['15m', '5m'], history_days: optimizerDays, trials_per_market: optimizerTrials }),
        });
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(detail || `HTTP ${response.status}`);
        }
        let job = await response.json();
        while (job.status === 'running') {
          await new Promise(resolve => window.setTimeout(resolve, 1200));
          const status = await fetch(`/api/optimizer/${job.id}`);
          if (!status.ok) throw new Error(await status.text());
          job = await status.json();
        }
        if (job.status === 'failed') throw new Error(job.message || `Test ${pair} zlyhal.`);
        if (job.result) {
          completed.push(job.result);
          completedJobIds.push(job.id);
        }
      }

      const finalizeResponse = await fetch('/api/optimizer/finalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version_id: lock.version_id,
          source_job_ids: completedJobIds,
        }),
      });
      if (!finalizeResponse.ok) {
        const detail = await finalizeResponse.text();
        throw new Error(detail || `Finalize HTTP ${finalizeResponse.status}`);
      }
      const finalized = await finalizeResponse.json();
      const result = finalized?.result || combineOptimizerResults(completed);
      setOptimizer(finalized?.result
        ? finalized
        : { status: 'completed', progress: 100, message: result.job_verdict, result });
    } catch (error) {
      const raw = error instanceof Error ? error.message : String(error);
      setOptimizer({ status: 'failed', message: `Výpočet sa prerušil. ${raw.slice(0, 300)}` });
    } finally {
      setOptimizerRunning(false);
    }
  };
  const copyAlgorithm = async () => { if (!optimizer?.result) return; const result = normalizeOptimizerResult(optimizer.result); if (!result.qualified || !result.strategy_code) return; await navigator.clipboard.writeText(result.strategy_code); setMessage('Výsledný algoritmus bol skopírovaný.'); };
  const installApp = async () => { if (installPrompt) { await installPrompt.prompt(); await installPrompt.userChoice; setInstallPrompt(null); return; } setMessage('iPhone/iPad: v Safari stlač Zdieľať a potom „Pridať na plochu“. Android: otvor menu prehliadača a vyber „Inštalovať aplikáciu“.'); };
  const runSimulation = async () => {
    const currentConfig = config;
    if (!currentConfig) return;
    if (historicalMode) {
      const endTime = Date.now();
      const startTime = endTime - historicalHours * 60 * 60 * 1000;
      const minutes = Number(String(currentConfig.timeframe).replace('m', '')) || 3;
      const wantedCandles = Math.ceil(historicalHours * 60 / minutes);
      const controller = new AbortController();
      runAbortRef.current = controller;
      setIsRunning(true);
      setRunStatus('running');
      setMessage(`Historický test prebieha: posledných ${historicalRangeLabels[historicalHours]}.`);
      try {
        const response = await fetch('/api/simulations/run', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: currentConfig, candle_limit: Math.min(45000, Math.max(100, wantedCandles)), start_time: startTime, end_time: endTime, force_close_at_end: true }) });
        if (!response.ok) throw new Error('historical_simulation_failed');
        const result = await response.json();
        setSimulation(result);
        setLastRun(result.run);
        setRunStatus('completed');
        setMessage(`Historický test dokončený: ${result.metrics.closed_trades} uzatvorených obchodov.`);
      } catch (error: any) {
        if (error?.name === 'AbortError') return;
        setRunStatus('failed');
        setMessage('Historické dáta sa nepodarilo spracovať. Skontroluj pripojenie na OKX.');
      } finally {
        setIsRunning(false);
        runAbortRef.current = null;
      }
      return;
    }
    const scheduledStart = new Date(testStart).getTime();
    const scheduledEnd = new Date(testEnd).getTime();
    if (timeLimited && (!Number.isFinite(scheduledStart) || !Number.isFinite(scheduledEnd) || scheduledStart >= scheduledEnd || scheduledEnd <= Date.now())) {
      setMessage('Ukončenie časovo limitovanej simulácie musí byť v budúcnosti.');
      return;
    }
    liveEnabledRef.current = true;
    setIsRunning(true);
    setRunStatus('running');
    const finishTimedRun = () => {
      liveEnabledRef.current = false;
      liveTimerRef.current = null;
      setIsRunning(false);
      setRunStatus('completed');
      setMessage('Časovo limitovaná simulácia dosiahla zvolený čas a bola ukončená.');
    };
    const runOnce = async (): Promise<void> => {
      if (!liveEnabledRef.current) return;
      const now = Date.now();
      if (timeLimited && now >= scheduledEnd) { finishTimedRun(); return; }
      if (timeLimited && now < scheduledStart) {
        setMessage(`Simulácia je naplánovaná. Spustí sa ${new Date(scheduledStart).toLocaleString('sk-SK')}.`);
        liveTimerRef.current = window.setTimeout(runOnce, Math.min(60_000, scheduledStart - now));
        return;
      }
      const endTime = now;
      const startTime = endTime - 24 * 60 * 60 * 1000;
      const minutes = Number(String(currentConfig.timeframe).replace('m', '')) || 3;
      const wantedCandles = Math.ceil((endTime - startTime) / 60000 / minutes);
      const controller = new AbortController();
      runAbortRef.current = controller;
      setMessage(timeLimited ? `Simulácia prebieha do ${new Date(scheduledEnd).toLocaleString('sk-SK')}.` : 'Živá simulácia beží. Vyhodnocujem nové sviečky z OKX.');
      try {
        const response = await fetch('/api/simulations/run', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: currentConfig, candle_limit: Math.min(5000, Math.max(100, wantedCandles)), start_time: startTime, end_time: endTime }) });
        if (!response.ok) throw new Error('simulation_failed');
        const result = await response.json();
        setSimulation(result);
        setLastRun(result.run);
        if (!liveEnabledRef.current) return;
        if (timeLimited && Date.now() >= scheduledEnd) { finishTimedRun(); return; }
        setMessage(timeLimited ? `Simulácia prebieha do ${new Date(scheduledEnd).toLocaleString('sk-SK')}. Ďalšia kontrola o 1 minútu.` : 'Živá simulácia beží. Ďalšie vyhodnotenie prebehne o 1 minútu.');
        const remaining = timeLimited ? Math.max(1, scheduledEnd - Date.now()) : 60_000;
        liveTimerRef.current = window.setTimeout(runOnce, Math.min(60_000, remaining));
      } catch (error: any) {
        if (error?.name === 'AbortError') return;
        liveEnabledRef.current = false;
        setIsRunning(false);
        setRunStatus('failed');
        setMessage('Simuláciu sa nepodarilo spustiť. Skontroluj pripojenie na burzu.');
      } finally {
        runAbortRef.current = null;
      }
    };
    await runOnce();
  };
  const testScannerPair = async (pair: string) => {
    if (!config || isRunning) return;
    const nextConfig: Config = { ...(config as Config), selected_pairs: [pair] };
    const hours: 24 = 24;
    const endTime = Date.now();
    const startTime = endTime - hours * 60 * 60 * 1000;
    const minutes = Number(String(nextConfig.timeframe).replace('m', '')) || 3;
    const wantedCandles = Math.ceil(hours * 60 / minutes);
    const controller = new AbortController();
    runAbortRef.current = controller;
    setMarketPair(pair);
    setConfig(nextConfig);
    setHistoricalMode(true);
    setHistoricalHours(hours);
    setTimeLimited(false);
    setIsRunning(true);
    setRunStatus('running');
    setMessage(`Historický test ${pair} prebieha: posledných 24 hodín.`);
    window.setTimeout(() => document.querySelector('.simulation-control')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
    try {
      const response = await fetch('/api/simulations/run', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: nextConfig, candle_limit: Math.min(45000, Math.max(100, wantedCandles)), start_time: startTime, end_time: endTime, force_close_at_end: true }) });
      if (!response.ok) throw new Error('scanner_backtest_failed');
      const result = await response.json();
      setSimulation(result);
      setLastRun(result.run);
      setRunStatus('completed');
      setMessage(`Test ${pair} dokončený: ${result.metrics.closed_trades} uzatvorených obchodov.`);
    } catch (error: any) {
      if (error?.name === 'AbortError') return;
      setRunStatus('failed');
      setMessage(`Test ${pair} zlyhal. Skontroluj pripojenie k trhovým dátam.`);
    } finally {
      setIsRunning(false);
      runAbortRef.current = null;
    }
  };
  const stopSimulation = () => { liveEnabledRef.current = false; if (liveTimerRef.current !== null) window.clearTimeout(liveTimerRef.current); liveTimerRef.current = null; runAbortRef.current?.abort(); runAbortRef.current = null; setIsRunning(false); setRunStatus('stopped'); setMessage('Simulácia bola zastavená.'); };
  useEffect(() => () => { liveEnabledRef.current = false; if (liveTimerRef.current !== null) window.clearTimeout(liveTimerRef.current); if (optimizerTimerRef.current !== null) window.clearTimeout(optimizerTimerRef.current); runAbortRef.current?.abort(); }, []);
  const setQuickRange = (hours: number) => { const now = Date.now(); setTestStart(inputDateTime(new Date(now))); setTestEnd(inputDateTime(new Date(now + hours * 60 * 60 * 1000))); };
  const chooseScannerPair = (pair: string) => {
    setMarketPair(pair);
    setConfig(current => ({ ...(current as Config), selected_pairs: Array.from(new Set([pair, ...(((current as Config)?.selected_pairs || []) as string[])])) }));
    setMessage(`${pair} bol pridaný do simulácie podľa výsledku OKX skenera.`);
  };
  const togglePaperBot = () => {
    if (!paperBotRunning && scanner?.markets?.[0]) chooseScannerPair(scanner.markets[0].pair);
    setPaperBotRunning(current => !current);
    setMessage(paperBotRunning ? 'Paper bot bol zastavený.' : 'Paper bot sleduje najlepšie hodnotené OKX trhy. Reálne príkazy sú vypnuté.');
  };
  const selected = useMemo(() => (config?.selected_pairs || []) as string[], [config]);
  const availableCoins = useMemo(() => Array.from(new Set([...coins, ...selected, ...(activeUniverse?.pairs || []), ...(universeProposal?.picks || []).map((item: any) => item.pair)])), [selected, activeUniverse, universeProposal]);
  const universeLocked = Boolean(activeUniverse && Date.parse(activeUniverse.expires_at) > Date.now());
  if (!config || !dashboard) return <main className="loading"><div className="brand-mark">R</div><h1>Revoltis Bot Strategy</h1><p>{message || 'Pripravujem simuláciu…'}</p></main>;
  const currentPair = marketPair || selected[0];
  const allVisibleTrades = simulation?.trades || dashboard.trades || [];
  const currentTrades = allVisibleTrades.filter((trade: any) => trade.pair === currentPair);
  const currentInitialCapital = Number(simulation?.metrics?.initial_capital || dashboard.metrics.initial_capital || 100);
  const metrics = simulation?.per_pair_metrics?.[currentPair] || calculatePairMetrics(currentTrades, currentInitialCapital);
  const currentEquityCurve = simulation?.per_pair_equity_curves?.[currentPair] || calculatePairCurve(currentTrades, currentInitialCapital);
  const currentCoverage = simulation?.data_coverage?.[currentPair];
  return <main>
    <header className="topbar"><div className="brand"><div className="brand-mark">R</div><div><h1>Revoltis <b>Bot Strategy</b></h1><p>Navrhuj · testuj · porovnávaj</p></div></div><div className="top-actions"><span className="mode"><i />SIMULÁCIA</span><button className="install-app" onClick={installApp}>⇩ Inštalovať aplikáciu</button><button className="ghost" onClick={exportConfig} disabled={!optimizer?.result || !normalizeOptimizerResult(optimizer.result).qualified}>Export pre backtest</button><button onClick={save}>Uložiť</button></div></header>
    <section className="hero"><div><span className="eyebrow">AKTÍVNY PRACOVNÝ PRIESTOR</span><h2>Stratégia pre pohyb trhu,<br /><em>nie pre domnienky.</em></h2><p>Každá úprava parametrov ostáva v bezpečnom dry-run režime. Pred ďalším krokom ju porovnaj s historickým výsledkom.</p></div><div className="hero-status"><span>Stav synchronizácie</span><strong>{dashboard.last_sync ? 'Dáta prijaté' : 'Čaká na údaje'}</strong><small>{dashboard.last_sync?.received_at ? new Date(dashboard.last_sync.received_at).toLocaleString('sk-SK') : 'Zatiaľ bez simulovaných obchodov'}</small></div></section>
    <OpsPanel />
    <BotControlCenter scanner={scanner} error={scannerError} loading={scannerLoading} running={paperBotRunning} onRefresh={scanOkx} onToggle={togglePaperBot} onChoose={testScannerPair} dailyLoss={Math.min(5, Number(config.stop_loss_percent || 4))} stake={Number(config.stake_amount || 0)} />
    <section className={`simulation-control status-${runStatus}`}>
      <div className="run-state"><span className="state-dot" /><div><small>STAV SIMULÁCIE</small><strong>{runStatus === 'running' ? (historicalMode ? 'HISTORICKÝ TEST PREBIEHA' : 'PREBIEHA') : runStatus === 'completed' ? (historicalMode ? 'Historický test dokončený' : 'Čas simulácie uplynul – dokončená') : runStatus === 'stopped' ? 'Simulácia zastavená' : runStatus === 'failed' ? 'Simulácia zlyhala' : 'Simulácia nebeží'}</strong><p>{runStatus === 'running' ? (historicalMode ? 'Spracúvam zvolené historické sviečky zrýchlene.' : timeLimited ? `Aktívna do ${new Date(testEnd).toLocaleString('sk-SK')}. Stav zostane rozsvietený až do ukončenia.` : 'Stav zostáva aktívny, kým nestlačíš Zastaviť simuláciu.') : lastRun ? `Posledné vyhodnotenie: ${new Date(lastRun.finished_at).toLocaleString('sk-SK')}` : 'Spusti živú simuláciu alebo zapni historický test.'}</p></div></div>
      <div className="run-actions">{isRunning ? <button className="stop-button" onClick={stopSimulation}>■ Zastaviť simuláciu</button> : <button className="run-button" onClick={runSimulation}>▶ Spustiť simuláciu</button>}</div>
      <div className="run-mode" style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 12, padding: '14px 12px', border: `1px solid ${historicalMode ? '#5a9ee8' : '#304055'}`, borderRadius: 10, background: historicalMode ? '#12283d' : '#0d151e', boxShadow: historicalMode ? '0 0 0 3px #5a9ee822' : 'none' }}><label style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700 }}><input type="checkbox" checked={historicalMode} onChange={event => { setHistoricalMode(event.target.checked); if (event.target.checked) setTimeLimited(false); }} disabled={isRunning} /><span>Historické dáta</span></label><p style={{ margin: 0, color: historicalMode ? '#c8e2ff' : '#a2b1c3', fontSize: 13 }}>{historicalMode ? 'Zapnuté – po spustení sa vykoná zrýchlený test spätne.' : 'Vypnuté – nové obchody vznikajú iba zo živých sviečok po spustení.'}</p></div>
      {historicalMode && <div className="test-window"><div><strong style={{ color: '#c8e2ff' }}>Vyber obdobie spätne</strong></div><nav>{([1, 6, 24, 168, 336, 720] as const).map(hours => <button key={hours} className={historicalHours === hours ? 'active' : 'ghost'} onClick={() => setHistoricalHours(hours)} disabled={isRunning}>{historicalRangeLabels[hours]}</button>)}</nav></div>}
      {!historicalMode && <div className="run-mode" style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 12, padding: '14px 12px', border: `1px solid ${timeLimited ? '#43c98a' : '#304055'}`, borderRadius: 10, background: timeLimited ? '#153326' : '#0d151e', boxShadow: timeLimited ? '0 0 0 3px #43c98a22' : 'none' }}><label style={{ display: 'flex', alignItems: 'center', gap: 9, fontWeight: 700 }}><input type="checkbox" checked={timeLimited} onChange={event => setTimeLimited(event.target.checked)} disabled={isRunning} /><span>Časovo limitovaná živá simulácia</span></label><p style={{ margin: 0, color: timeLimited ? '#bff6d8' : '#a2b1c3', fontSize: 13 }}>{timeLimited ? 'Zapnuté – živá simulácia bude prebiehať až do zvoleného dátumu a času.' : 'Vypnuté – živá simulácia beží až do manuálneho zastavenia.'}</p></div>}
      {!historicalMode && timeLimited && <div className="test-window"><div><label>Spustenie<input type="datetime-local" value={testStart} onChange={event => setTestStart(event.target.value)} disabled={isRunning} /></label><label>Ukončenie<input type="datetime-local" value={testEnd} onChange={event => setTestEnd(event.target.value)} disabled={isRunning} /></label></div><nav><button className="ghost" onClick={() => setQuickRange(6)} disabled={isRunning}>6 h</button><button className="ghost" onClick={() => setQuickRange(24)} disabled={isRunning}>24 h</button><button className="ghost" onClick={() => setQuickRange(24 * 7)} disabled={isRunning}>7 dní</button><button className="ghost" onClick={() => setQuickRange(24 * 30)} disabled={isRunning}>30 dní</button></nav></div>}
    </section>
    <section className="metric-grid"><Metric label={`Simulačný kapitál · ${currentPair}`} value={`${metrics.initial_capital} USDT`} hint="počiatočný stav" /><Metric label={`Realizovaný výsledok · ${currentPair}`} value={`${metrics.realized_profit >= 0 ? '+' : ''}${metrics.realized_profit} USDT`} hint="po uzatvorených obchodoch" positive={metrics.realized_profit >= 0} /><Metric label={`Úspešnosť · ${currentPair}`} value={`${metrics.win_rate} %`} hint={`${metrics.closed_trades} uzatvorených obchodov`} /><Metric label={`Max. drawdown · ${currentPair}`} value={`${metrics.max_drawdown_percent ?? 0} %`} hint="pokles od maxima" /></section>
    <section className="preset-section"><div className="preset-heading"><div><span className="eyebrow">RÝCHLE NASTAVENIE</span><h3>Vyber profil stratégie</h3></div><p>Profil automaticky prenastaví obchodné hranice. Coiny a počiatočný kapitál ostanú zachované.</p></div><div className="preset-grid">{Object.entries(presets).map(([key, preset]) => <button key={key} className={`preset ${key} ${activePreset === key ? 'selected' : ''}`} onClick={() => applyPreset(key)}><span>{key === 'conservative' ? '◒' : key === 'growing' ? '↗' : '⚡'}</span><div><b>{preset.title}</b><small>{preset.description}</small></div><i>{activePreset === key ? 'Aktívna' : 'Použiť'}</i></button>)}</div></section>
    <section className="optimizer-panel">
      <div className="optimizer-head"><div><span className="eyebrow">AI OPTIMALIZAČNÝ AGENT</span><h3>Hľadanie najlepšej stratégie</h3><p>Agent otestuje uzamknutý zoznam coinov na hlavných intervaloch 5m a 15m. Tri walk-forward okná a záverečný holdout chránia pred prispôsobením minulosti.</p></div><button onClick={startOptimizer} disabled={optimizerRunning || universeSaving || dashboard?.persistence?.production_ready === false}>{optimizerRunning ? 'Agent pracuje…' : '✦ Spustiť AI optimalizáciu'}</button></div>
      <div className="optimizer-options"><label>História<select value={optimizerDays} onChange={event => setOptimizerDays(Number(event.target.value) as 1 | 7 | 14 | 30 | 90)} disabled={optimizerRunning}><option value={1}>1 deň</option><option value={7}>7 dní</option><option value={14}>14 dní</option><option value={30}>30 dní</option><option value={90}>90 dní</option></select></label><label>Varianty na trh<select value={optimizerTrials} onChange={event => setOptimizerTrials(Number(event.target.value))} disabled={optimizerRunning}><option value={2}>2 · rýchle</option><option value={3}>3 · odporúčané</option><option value={5}>5 · dôkladné</option><option value={8}>8 · maximum</option></select></label><span>Výpočet hodnotí zisk, drawdown, počet obchodov, stabilitu a odhadované náklady.</span></div>
      <p className="universe-lock">Verzia: {optimizer?.version_id || optimizer?.result?.version_id || activeUniverse?.version_id || '—'} · Lock: {(optimizer?.locked_pairs || optimizer?.result?.locked_pairs || activeUniverse?.pairs || []).join(', ') || 'nedostupný'}</p>
      {dashboard?.persistence?.durable === false && <p className="diagnostics-missing" role="status">persistence_unavailable · Režim demo. Lock nemá trvalé uloženie v Supabase a po cold starte sa môže stratiť. Uloženie nového locku a nový job sú v produkcii pozastavené.</p>}
      <p className="optimizer-note">Náklady pre nové výpočty: okx_global_regular · taker 0,10 % (maker 0,08 %) + half-spread + impact podľa páru. Sadzobník je výskumný predpoklad, nie overenie poplatkov tvojho účtu.</p>
      {optimizer?.status === 'running' && <div className="optimizer-progress"><div><span style={{ width: `${optimizer.progress || 0}%` }} /></div><p>{optimizer.message} · {optimizer.progress || 0} %</p></div>}
      {optimizer?.status === 'failed' && <div className="optimizer-error">Optimalizácia zlyhala: {optimizer.message}</div>}
      {optimizer?.result && <AlgorithmResult result={optimizer.result} onCopy={copyAlgorithm} />}
      <ReplayAvailabilityNotice availability={replayAvailability} />
      <p className="optimizer-note">Optimalizácia negarantuje budúci zisk. Pred použitím reálnych peňazí musí výsledok prejsť novým backtestom, kontrolou skreslenia a dlhším dry-run testom.</p>
    </section>
    <FreqtradeVisual
  result={optimizer?.result || null}
  activeVersionId={activeUniverse?.version_id || null}
/>
    <section className="panel market-panel"><div className="section-title"><span>LIVE</span><div><h3>Aktuálne sviečky z burzy</h3><p>Verejné spot dáta OKX · bez API kľúča · {config.timeframe}</p></div><select className="market-pair" value={marketPair} onChange={event => setMarketPair(event.target.value)}>{selected.map(pair => <option key={pair}>{pair}</option>)}</select></div><MarketChart market={market} error={marketError} />{simulation && <div className="simulation-result"><b>Výsledok poslednej simulácie · {currentPair}</b><span>Hodnota portfólia: {metrics.portfolio_value} USDT</span><span>Zisk/strata: {metrics.realized_profit >= 0 ? '+' : ''}{metrics.realized_profit} USDT</span><span>Obchody: {metrics.closed_trades}</span><span>Drawdown: {metrics.max_drawdown_percent} %</span>{currentCoverage && <span className={currentCoverage.complete ? 'positive' : 'negative'}>Dáta: {currentCoverage.candles}/{currentCoverage.expected_candles} sviečok {currentCoverage.complete ? '✓' : '⚠'}</span>}</div>}</section>
    <section className="workspace"><aside className="settings panel"><div className="section-title"><span>01</span><div><h3>Konfigurátor stratégie</h3><p>Vyber coiny a hranice simulácie.</p></div></div><div className="universe-actions"><button className="ghost" onClick={proposeUniverse} disabled={universeLoading || universeSaving || optimizerRunning || isRunning}>{universeLoading ? 'Vyhodnocujem OKX…' : 'Navrhnúť z OKX'}</button>{universeProposal?.status === 'ok' && <button onClick={acceptUniverse} disabled={universeSaving || optimizerRunning || isRunning}>{universeSaving ? 'Ukladám…' : 'Použiť návrh'}</button>}</div>{universeProposal && <div className={`universe-proposal ${universeProposal.status}`}><b>{universeProposal.status === 'ok' ? 'Návrh – zatiaľ nepoužitý' : 'Návrh nevytvorený'}</b><p>{universeProposal.message}</p>{universeProposal.picks?.map((item: any) => <span key={item.pair}>{item.pair} <strong>{item.score}/100</strong><small>spread {(item.spread_ratio * 100).toFixed(3)} % · ATR {(item.atr_ratio * 100).toFixed(2)} %</small></span>)}{universeProposal.data_quality?.incomplete_pairs?.length > 0 && <small>Neúplné dáta: {universeProposal.data_quality.incomplete_pairs.join(', ')}</small>}</div>}<p className="universe-lock" role="status">{universeLocked ? `Uložené a zamknuté: ${activeUniverse.pairs.join(', ')}. Návrh sa ukladá automaticky.` : 'Pre optimalizáciu použi a ulož návrh z OKX tlačidlom „Použiť návrh“.'}</p><h4>Vybrané coiny <small>{selected.length}</small></h4><div className="coin-grid">{availableCoins.map(coin => <label className={selected.includes(coin) ? 'coin active' : 'coin'} key={coin}><input type="checkbox" disabled={universeLocked || optimizerRunning || universeSaving} checked={selected.includes(coin)} onChange={event => update('selected_pairs', event.target.checked ? [...selected, coin] : selected.filter(item => item !== coin))} /><span>{coin.replace('/USDT', '')}</span><small>USDT</small></label>)}</div>{groups.map(([title, keys]) => <details key={title} open={title !== 'Rozšírené indikátory'}><summary>{title}<span>⌄</span></summary><div className="field-grid">{keys.map(key => <label key={key}>{fields[key]}{key === 'timeframe' ? <select value={String(config[key])} onChange={event => update(key, event.target.value)}>{['1m', '3m', '5m', '15m'].map(value => <option key={value}>{value}</option>)}</select> : <div className="number"><input type="number" value={Number(config[key])} step="0.01" onChange={event => update(key, Number(event.target.value))} /><span>{key.includes('percent') || key.includes('ratio') || key.includes('stop') || key.includes('trailing') || key.includes('rebound') ? '%' : key.includes('capital') || key.includes('amount') || key.includes('volume') ? 'USDT' : ''}</span></div>}</label>)}</div></details>)}</aside>
      <div className="results"><section className="panel chart-panel"><div className="section-title"><span>02</span><div><h3>Výsledok simulácie · {currentPair}</h3><p>Kapitál vybraného coinu po uzatvorených obchodoch.</p></div><span className="saved-progress">Priebeh uložený</span></div><div className="chart-controls"><span>Časový raster:</span><button className={chartRange === 'hour' ? 'active' : 'ghost'} onClick={() => setChartRange('hour')}>Hodiny</button><button className={chartRange === 'day' ? 'active' : 'ghost'} onClick={() => setChartRange('day')}>Dni / 24 h</button><button className={chartRange === 'week' ? 'active' : 'ghost'} onClick={() => setChartRange('week')}>Týždne</button></div><EquityChart points={currentEquityCurve} range={chartRange} /></section><section className="two-col"><div className="panel"><h3>Dôvody nevstúpenia</h3><Rejections reasons={simulation?.rejections || dashboard.analytics?.rejections || {}} /></div><div className="panel"><h3>Simulované obchody · {currentPair}</h3><TradeList trades={currentTrades} /></div></section><section className="panel"><div className="section-title"><span>03</span><div><h3>Verzie a backtesty</h3><p>Ulož parametre pred každým backtestom.</p></div><button className="ghost push" onClick={saveVersion}>Nová verzia</button></div><VersionList versions={versions} /><div className="compare"><h4>Porovnanie backtestov</h4><select value={left} onChange={event => setLeft(event.target.value)}><option value="">Prvý backtest</option>{backtests.map(item => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.timerange}</option>)}</select><span>vs</span><select value={right} onChange={event => setRight(event.target.value)}><option value="">Druhý backtest</option>{backtests.map(item => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.timerange}</option>)}</select><button onClick={compare}>Porovnať</button></div>{comparison && <Comparison data={comparison} />}</section></div>
    </section><section className="settings-info"><div className="info-heading"><span className="eyebrow">INFO</span><h3>Vysvetlenie nastavení stratégie</h3><p>Otvor bod, ktorý chceš upraviť. Nájdeš tu význam parametra, jeho vplyv aj spôsob fungovania v simulácii.</p></div><div className="info-grid">{Object.entries(settingInfo).map(([key, info]) => <details key={key}><summary>{fields[key] || 'Vybrané coiny'}<span>+</span></summary><div><p><b>Na čo slúži:</b> {info.purpose}</p><p><b>Čo ovplyvní:</b> {info.effect}</p><p><b>Ako funguje:</b> {info.mechanism}</p></div></details>)}</div></section><p className="message">{message}</p>
  </main>;
}
function BotControlCenter({ scanner, error, loading, running, onRefresh, onToggle, onChoose, dailyLoss, stake }: { scanner: any, error: string, loading: boolean, running: boolean, onRefresh: () => void, onToggle: () => void, onChoose: (pair: string) => void, dailyLoss: number, stake: number }) {
  const leaders = scanner?.markets || [];
  const best = leaders[0];
  const money = (value: number) => new Intl.NumberFormat('sk-SK', { notation: 'compact', maximumFractionDigits: 1 }).format(value || 0);
  return <section className={`bot-control ${running ? 'bot-running' : ''}`}>
    <div className="bot-control-head"><div><span className="eyebrow">OKX SIMULAČNÝ BOT</span><h3>Riadiace centrum bota</h3><p>Automatický výber spot trhu podľa reálnej likvidity, spreadu a volatility. Bez API kľúča a bez reálnych príkazov.</p></div><div className="bot-head-actions"><button className="ghost" onClick={onRefresh} disabled={loading}>{loading ? 'Skenujem…' : 'Obnoviť trhy'}</button><button className={running ? 'stop-button' : 'run-button'} onClick={onToggle} disabled={!best}>{running ? '■ Zastaviť simulačného bota' : '▶ Spustiť simulačného bota'}</button></div></div>
    <div className="bot-status-grid">
      <article><span>STAV</span><b className={running ? 'positive' : ''}>{running ? 'SLEDUJE TRHY' : 'PRIPRAVENÝ'}</b><small>{running ? 'Reálne obchodovanie vypnuté' : 'Čaká na spustenie'}</small></article>
      <article><span>NAJLEPŠÍ TRH</span><b>{best?.pair || '—'}</b><small>{best ? `Skóre ${best.score}/100` : 'Čakám na OKX dáta'}</small></article>
      <article><span>MAX. SUMA NA OBCHOD</span><b>{stake} USDT</b><small>podľa konfigurácie</small></article>
      <article><span>DENNÁ OCHRANA</span><b>{dailyLoss.toFixed(1)} %</b><small>po limite sa nové vstupy zastavia</small></article>
    </div>
    {error ? <div className="bot-error">{error}</div> : <div className="scanner-table"><div className="scanner-row scanner-header"><span># / Pár</span><span>Objem 24 h</span><span>Spread</span><span>Volatilita</span><span>Skóre</span><span /></div>{leaders.slice(0, 8).map((market: any, index: number) => <div className="scanner-row" key={market.pair}><span><i>{index + 1}</i><b>{market.pair}</b><small className={market.change_24h_percent >= 0 ? 'positive' : 'negative'}>{market.change_24h_percent >= 0 ? '+' : ''}{market.change_24h_percent} %</small></span><span>{money(market.volume_24h)} USDT</span><span>{market.spread_percent.toFixed(3)} %</span><span>{market.volatility_24h_percent.toFixed(2)} %</span><span><strong>{market.score}</strong>/100</span><span><button className="ghost" onClick={() => onChoose(market.pair)}>Testovať</button></span></div>)}</div>}
    <footer><span>{scanner ? `${scanner.scanned} obchodovateľných párov skontrolovaných` : 'Načítavam verejné OKX trhy…'}</span><span>{scanner?.method || 'Likvidita · spread · volatilita'}</span><span>Výbery cez API: <b>ZAKÁZANÉ</b></span></footer>
  </section>;
}
function FreqtradeVisual({ result, activeVersionId }: { result: any, activeVersionId: string | null }) {
  const evaluated = Boolean(result && activeVersionId && result.version_id === activeVersionId);
  const candidate = evaluated && normalizeOptimizerResult(result).qualified;

  return <section className="freqtrade-visual">
    <div className="ft-brand">
      <div className="ft-logo">F</div>
      <div>
        <span>OVERENIE STRATÉGIE</span>
        <h3>Freqtrade</h3>
        <p>OKX spot · historické dáta a backtest s exportom obchodov</p>
      </div>
      <i className={candidate ? '' : 'ft-blocked'}>
        {candidate
          ? 'PRIPRAVENÉ'
          : evaluated
            ? 'ZABLOKOVANÉ — CHÝBA KANDIDÁT'
            : 'ZABLOKOVANÉ — NEVYHODNOTENÉ'}
      </i>
    </div>

    <div className="ft-bottom">
      <div>
        <span>DIAGNOSTICKÝ ROZSAH</span>
        <p>UNI/USDT · ZEC/USDT · 5m · exchange okx · spot</p>
        <p>download-data · backtesting --export trades</p>
      </div>

      <div className="ft-safety">
        <span>STAV EXPORTU</span>
        <p>
          {candidate
            ? 'Kandidát umožňuje export na samostatné overenie.'
            : evaluated
              ? 'NEPREŠIEL. Export stratégie aj dry-run sú vypnuté.'
              : 'NEVYHODNOTENÉ. Aktuálny lock ešte nemá výsledok optimalizácie. Export aj dry-run sú vypnuté.'}
        </p>
        <small>
          Fee 0.001 = taker 0,10 % za stranu. Spread a impact v tomto Freqtrade poplatku nie sú zahrnuté. Žiadny príkaz sa tu automaticky nespúšťa.
        </small>
      </div>
    </div>
  </section>;
}
function AlgorithmResult({ result, onCopy }: { result: any, onCopy: () => void }) {
  const view = normalizeOptimizerResult(result);
  const m = view.holdout_metrics;
  const parameterKeys = ['bb_period', 'bb_deviation', 'rsi_period', 'rsi_oversold', 'atr_period', 'atr_min_percent', 'atr_max_percent', 'min_volume_ratio', 'rebound_min_percent', 'rebound_max_percent', 'stop_loss_percent', 'trailing_start_percent', 'trailing_distance_percent', 'max_no_trail_hours'];
  return <div className={`algorithm-result ${view.qualified ? '' : 'algorithm-rejected'}`}>
    <div className="result-title"><div>
      <span>{view.job_verdict}</span>
      <h4>{view.qualified ? `${view.pair} · ${view.timeframe}` : view.verdict}</h4>
      {!view.qualified && <p>Víťaz: žiadny. Diagnostika variantu: {view.pair} · {view.timeframe}</p>}
      <p>{view.method} · {view.tested_combinations} otestovaných kombinácií</p>
      <p>{view.qualified ? view.verdict : 'Parametre sa vyberajú vo validácii. Neúspešný holdout nespustí hľadanie náhradníka.'}</p>
    </div><button onClick={onCopy} disabled={!view.qualified || !view.strategy_code}>Kopírovať algoritmus</button></div>
    <p className="validation-counts">Validačné obchody: {view.validation_trade_count}/20 · Holdout: {view.holdout_visible ? `${m.closed_trades || 0}/10` : '— (nevyhodnotený)'} · Maximum validačných obchodov zo skúšaných variantov: {view.max_validation_trades ?? 'nezaznamenané'}</p>
    <div className="result-metrics">
      <div><span>Čistý zisk/strata holdoutu</span><b>{m.realized_profit ?? '—'} USDT</b><small>{view.holdout_profit_percent ?? '—'} % kapitálu</small></div>
      <div><span>Max. drawdown / limit 15 %</span><b>{m.max_drawdown_percent ?? '—'} %</b></div>
      <div><span>Obchody holdoutu / minimum 10</span><b>{view.holdout_visible ? m.closed_trades || 0 : '—'}</b></div>
      <div><span>Úspešnosť (od 20 obchodov)</span><b>{view.displayed_win_rate}</b></div>
      <div><span>Hold rovnakého coinu a obdobia</span><b>{view.buy_hold_percent ?? '—'} %</b></div>
    </div>
    {view.holdout_visible && <p className="validation-counts">Holdout po nákladoch: priemerná výhra {m.avg_win ?? 'n/a'} USDT · priemerná strata {m.avg_loss ?? 'n/a'} USDT · payoff {m.closed_trades >= 10 ? m.payoff ?? 'n/a' : 'n/a'} · expectancy {m.expectancy ?? 'n/a'} USDT/obchod.</p>}
    {!!Object.keys(view.cost_profiles || {}).length && <details><summary>Náklady podľa páru · {view.fee_schedule}<span>⌄</span></summary><ul className="list">{Object.entries(view.cost_profiles).map(([pair, value]) => { const c = value as any; const pct = (n: number) => `${(n * 100).toFixed(4)} %`; return <li key={pair}><span>{pair} · {c.role}<small>fee {pct(c.fee_rate)} · half-spread {pct(c.half_spread)} · impact nákup {pct(c.buy_impact)} / predaj {pct(c.sell_impact)}</small><small>Aktuálny snapshot knihy {c.book_ts}; nejde o historické L2. {c.thin_L1 ? 'thin_L1' : ''}</small></span></li>; })}</ul></details>}
    <VariantTable view={view} />
    <CurrentTradeAuditPanel view={view} />
    <TradeTapePanel view={view} />
    {!!view.per_coin_results?.length && <details><summary>Vyhodnotenie po coinoch <span>⌄</span></summary><ul className="list">
      {view.per_coin_results.map((row: any) => <li key={row.pair}><span>{row.pair} · {row.timeframe}<small>Validácia: {row.walk_forward_metrics?.closed_trades ?? 0}/20 · Holdout: {row.validation_passed && row.holdout_metrics ? `${row.holdout_metrics.closed_trades}/10` : '—'}</small></span><b>{row.verdict}</b></li>)}
    </ul></details>}
    <details><summary>{view.qualified ? 'Výsledné nastavenia' : 'Diagnostické nastavenia — nepoužiť'} <span>⌄</span></summary>
      <div className="parameter-grid">{parameterKeys.map(key => <div key={key}><span>{fields[key]}</span><b>{String(view.settings?.[key] ?? '—')}</b></div>)}</div>
    </details>
    {view.qualified && <details><summary>Kopírovateľný Freqtrade algoritmus <span>+</span></summary><pre>{view.strategy_code}</pre></details>}
    <small className="cost-model">Model nákladov: {view.cost_model}</small>
  </div>;
}
function VariantTable({ view }: { view: any }) {
  const rows = view.variant_results || [];
  const number = (value: any, digits = 2) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toLocaleString('sk-SK', { maximumFractionDigits: digits }) : '—';
  const payoff = (metrics: any, minimum: number) => Number(metrics?.closed_trades) >= minimum && metrics?.payoff != null ? number(metrics.payoff) : 'n/a';
  return <section className="variant-diagnostics" aria-label="Všetky skúšané varianty">
    <h4>Všetky skúšané varianty <small>{rows.length}/{view.tested_combinations ?? '—'}</small></h4>
    <p>Jeden riadok = coin, interval a variant. Zisk aj ziskové okná sú po nákladoch. Nestabilita znamená menej ako 2 z 3 ziskových WF okien.</p>
    <p>Na holdout ide iba jeden finalista za coin vybraný vo validácii. Ostatné riadky majú „—“. Neúspešný holdout nemá náhradníka.</p>
    <p>Poradie určuje expectancy validácie, potom payoff a nižší DD. Payoff vo WF oknách je iba opisný. Pod 20 validačnými obchodmi sa payoff nerankuje a úspešnosť je n/a. Bez stratových obchodov je payoff n/a.</p>
    <p>Expectancy je priemerný čistý výsledok na uzavretý obchod vrátane nulových obchodov. „slaby_pomer“ pri holdout payoff pod 0,8 je upozornenie, nemení verdikt.</p>
    {!view.diagnostics_complete && <p className="diagnostics-missing" role="status">Starší alebo neúplný záznam neobsahuje všetky varianty a WF okná. Chýbajúce údaje sú „—“; nič sa dodatočne nepočíta ani nespúšťa.</p>}
    {view.all_variants_insufficient_trades && <p className="diagnostics-stop" role="status">Všetky varianty majú menej ako 20 validačných obchodov. Výskum tejto sady na tomto zámku sa končí.</p>}
    {rows.length > 0 && <div className="variant-table-scroll" tabIndex={0} role="region" aria-label="Tabuľka variantov, posúvaj vodorovne">
      <table className="variant-table">
        <caption>Všetky zaznamenané kombinácie; WF bunky uvádzajú obchody a čisté PnL v USDT. Vs hold je rozdiel výnosu v percentuálnych bodoch.</caption>
        <thead><tr><th scope="col">Coin</th><th scope="col">Interval</th><th scope="col">ID variantu</th><th scope="col">WF1</th><th scope="col">WF2</th><th scope="col">WF3</th><th scope="col">Validácia / 20</th><th scope="col">PnL validácie USDT</th><th scope="col">Max. DD validácie %</th><th scope="col">Ziskové okná / 3</th><th scope="col">Úspešnosť validácie</th><th scope="col">Priem. výhra validácie USDT</th><th scope="col">Priem. strata validácie USDT</th><th scope="col">Payoff validácie</th><th scope="col">Expectancy validácie USDT/obchod</th><th scope="col">Holdout / 10</th><th scope="col">PnL holdoutu USDT</th><th scope="col">DD holdoutu %</th><th scope="col">Vs hold p. b.</th><th scope="col">Priem. výhra holdoutu USDT</th><th scope="col">Priem. strata holdoutu USDT</th><th scope="col">Payoff holdoutu</th><th scope="col">Expectancy holdoutu USDT/obchod</th><th scope="col">Dôvod vyradenia / stav</th></tr></thead>
        <tbody>{rows.map((row: any) => <tr key={row.variant_id || `${row.pair}:${row.timeframe}:${row.variant}`}>
          <th scope="row">{row.pair}</th><td>{row.timeframe}</td><td title={row.variant_id}>v{row.variant ?? '—'}</td>
          {[0, 1, 2].map(index => <td key={index}>{number(row.validation_windows?.[index]?.closed_trades, 0)}<small>{number(row.validation_windows?.[index]?.realized_profit, 4)} USDT</small><small>Payoff {payoff(row.validation_windows?.[index], 1)}</small></td>)}
          <td>{number(row.walk_forward_metrics?.closed_trades, 0)}/20</td><td>{number(row.walk_forward_metrics?.realized_profit, 4)}</td><td>{number(row.walk_forward_metrics?.max_drawdown_percent)}</td><td>{number(row.profitable_validation_windows, 0)}/3</td>
          <td>{Number(row.walk_forward_metrics?.closed_trades) >= 20 ? `${number(row.walk_forward_metrics?.win_rate)} %` : 'n/a'}</td><td>{number(row.walk_forward_metrics?.avg_win, 4)}</td><td>{number(row.walk_forward_metrics?.avg_loss, 4)}</td><td>{payoff(row.walk_forward_metrics, 20)}</td><td>{number(row.walk_forward_metrics?.expectancy, 4)}</td>
          <td>{row.holdout_evaluated ? `${number(row.holdout_metrics?.closed_trades, 0)}/10` : '—'}</td><td>{number(row.holdout_metrics?.realized_profit, 4)}</td><td>{number(row.holdout_metrics?.max_drawdown_percent)}</td><td>{number(row.vs_hold_percentage_points, 4)}</td>
          <td>{number(row.holdout_metrics?.avg_win, 4)}</td><td>{number(row.holdout_metrics?.avg_loss, 4)}</td><td>{row.holdout_evaluated ? payoff(row.holdout_metrics, 10) : '—'}</td><td>{number(row.holdout_metrics?.expectancy, 4)}</td>
          <td className="variant-reasons">{row.rejection_reasons?.length ? row.rejection_reasons.join(' · ') : row.legacy ? 'Nezaznamenané' : row.status === 'accepted' ? 'Holdout prijatý' : 'Validácia prešla; nevybraný na holdout'}{row.rejection_phase && <small>{row.rejection_phase === 'holdout' ? 'Vyradený na holdoute' : 'Vyradený vo validácii'}</small>}{row.payoff_warning && <small>Payoff: {row.payoff_warning} (značka)</small>}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
function ReplayAvailabilityNotice({ availability }: { availability: any }) {
  if (availability?.status !== 'replay_unavailable') return null;
  return (
    <p className="diagnostics-missing" role="status">
      <strong>Historická diagnostika UNI/ZEC nedostupná</strong> ·
      Pôvodný report, parametre, okná a snapshot sviečok pre starý UNI/ZEC 5m v2 replay
      sa nepodarilo overiť. Tento stav nie je verdiktom aktuálneho locku.
    </p>
  );
}
function CurrentTradeAuditPanel({ view }: { view: any }) {
  const trades = Array.isArray(view.trades)
    ? view.trades.filter((trade: any) => ['wf1', 'wf2', 'wf3', 'holdout'].includes(trade.window))
    : [];
  if (!trades.length) return null;

  const validation = trades.filter((trade: any) => ['wf1', 'wf2', 'wf3'].includes(trade.window));
  const holdout = trades.filter((trade: any) => trade.window === 'holdout');
  const wins = trades.filter((trade: any) => Number(trade.pnl_net) > 0);
  const losses = trades.filter((trade: any) => Number(trade.pnl_net) < 0);

  const median = (values: number[]) => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };
  const number = (value: any, digits = 4) =>
    value != null && Number.isFinite(Number(value))
      ? Number(value).toLocaleString('sk-SK', { maximumFractionDigits: digits })
      : '—';

  const lossHitTrailThenStop = losses.filter((trade: any) =>
    trade.mfe_reached_trailing_start === true && trade.exit_reason === 'stop_loss'
  ).length;
  const canonicalReason = (trade: any) => trade.exit_reason === 'trailing' ? 'trailing_profit' : trade.exit_reason === 'window_end' ? 'end_of_test' : trade.exit_reason;
  const winningTrailing = wins.filter((trade: any) => canonicalReason(trade) === 'trailing_profit').length;
  const winningWindowEnd = wins.filter((trade: any) => canonicalReason(trade) === 'end_of_test').length;
  const medianWinMfe = median(wins.map((trade: any) => Number(trade.mfe)).filter(Number.isFinite));
  const medianLossMae = median(losses.map((trade: any) => Math.abs(Number(trade.mae))).filter(Number.isFinite));

  return <section className="trade-tape-panel" aria-label="Read-only audit aktuálneho optimizer výsledku">
    <h4>Read-only audit aktuálneho výsledku · {view.pair} · {view.timeframe} · v{view.variant ?? '—'}</h4>
    <p>Len už uložená páska z tohto optimizer jobu. Nič sa neprepočítava a nespúšťa sa nový holdout ani grid.</p>
    <p>
      Obchody spolu {trades.length} · validácia {validation.length} · holdout {holdout.length} ·
      výhry {wins.length} · straty {losses.length}.
    </p>
    <p>
      Straty s MFE ≥ trailing start a následným stop-lossom: <strong>{lossHitTrailThenStop}/{losses.length}</strong> ·
      výhry zatvorené trailingom: <strong>{winningTrailing}</strong> ·
      výhry zatvorené na konci okna: <strong>{winningWindowEnd}</strong>.
    </p>
    <p>
      Medián MFE výhier: <strong>{number(medianWinMfe)} %</strong> ·
      medián |MAE| strát: <strong>{number(medianLossMae)} %</strong>.
    </p>
    <details>
      <summary>37-obchodová páska / uložené obchody <span>⌄</span></summary>
      <div className="trade-tape-scroll" tabIndex={0} role="region" aria-label="Read-only tabuľka obchodov aktuálneho výsledku">
        <table className="trade-tape">
          <thead><tr>{['Okno', 'Vstup UTC', 'Výstup UTC', 'PnL USDT', 'MAE %', 'MFE %', 'Dôvod', 'MFE ≥ trail start'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{trades.map((trade: any, index: number) => <tr key={`${trade.window}:${trade.entry_ts}:${index}`}>
            <th scope="row">{trade.window}</th>
            <td>{trade.entry_ts}</td>
            <td>{trade.exit_ts}</td>
            <td>{number(trade.pnl_net, 6)}</td>
            <td>{number(trade.mae)}</td>
            <td>{number(trade.mfe)}</td>
            <td>{trade.exit_reason}</td>
            <td>{trade.mfe_reached_trailing_start == null ? '—' : trade.mfe_reached_trailing_start ? 'áno' : 'nie'}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </section>;
}

function TradeTapePanel({ view }: { view: any }) {
  const rows = (view.variant_results || []).filter((row: any) => ['UNI/USDT', 'ZEC/USDT'].includes(row.pair) && row.timeframe === '5m' && row.variant === 2 && Number(row.walk_forward_metrics?.closed_trades) >= 20);
  const number = (value: any, digits = 4) => value != null && Number.isFinite(Number(value)) ? Number(value).toLocaleString('sk-SK', { maximumFractionDigits: digits }) : '—';
  if (!rows.length) return null;
  return <section className="trade-tape-panel" aria-label="Páska validačných obchodov UNI a ZEC">
    <h4>Páska obchodov · UNI a ZEC · 5m · v2</h4>
    <p>Len validation WF1–WF3. Rozbor nemení verdikt ani neodomyká export.</p>
    <p>MAE je najnižšia a MFE najvyššia zmena ceny od vstupu v %. Sú pred nákladmi; PnL je po nákladoch uložených v danom reporte; historický UNI/ZEC replay zachováva 0,15 % na každej strane. Vstupná sviečka sa do extrémov nepočíta, pretože vstup je na jej close.</p>
    <p>SL sa spúšťa podľa close, nie samotného wicku. „SL pred trailingom“ znamená, že na rovnakom close platili obe výstupné podmienky a prednosť dostal SL. Poradie high/low v sviečke nepoznáme.</p>
    {rows.map((row: any) => {
      const trades = (row.trades || []).filter((trade: any) => ['wf1', 'wf2', 'wf3'].includes(trade.window));
      const complete = row.trade_tape_version === 1 && trades.length === row.walk_forward_metrics.closed_trades;
      const wins = trades.filter((t: any) => t.pnl_net > 0), losses = trades.filter((t: any) => t.pnl_net < 0);
      const avg = (items: any[], key: string) => items.length && items.every(t => t[key] != null) ? items.reduce((sum, t) => sum + Number(t[key]), 0) / items.length : null;
      return <details key={row.variant_id} open><summary>{row.pair} · WF1–WF3 · {complete ? trades.length : '—'}/{row.walk_forward_metrics.closed_trades} obchodov <span>⌄</span></summary>
        {!complete ? <p className="diagnostics-missing"><strong>replay_unavailable</strong> · Páska sa v pôvodnom behu neuložila. Detaily sa nedajú odvodiť zo súhrnu. Bez pôvodného snapshotu a zhody všetkých WF súhrnov sa replay nevykoná.</p> : <>
          <p>Obchody {trades.length} · výhry {wins.length} · straty {losses.length} · nulové {trades.length - wins.length - losses.length}. Priemerná čistá výhra {number(avg(wins, 'pnl_net'))} USDT · strata {number(losses.length ? -avg(losses, 'pnl_net')! : null)} USDT.</p>
          <p>Priemerné MFE pri stratách {number(avg(losses, 'mfe'))} % · straty s MFE ≥ trailing start: {losses.filter((t: any) => t.mfe_reached_trailing_start === true).length}/{losses.length}.</p>
          <p>{['stop_loss', 'trailing_profit', 'max_no_trail', 'end_of_test', 'other'].map(reason => { const n = trades.filter((t: any) => (t.exit_reason === 'trailing' ? 'trailing_profit' : t.exit_reason === 'window_end' ? 'end_of_test' : t.exit_reason) === reason).length; return `${reason}: ${n} (${number(n / trades.length * 100, 1)} %)`; }).join(' · ')}</p>
          <div className="trade-tape-scroll" tabIndex={0} role="region" aria-label={`Obchody ${row.pair}, posúvaj vodorovne`}><table className="trade-tape">
            <caption>{row.pair} · 5m · v2. Časy vstupu a výstupu sú UTC.</caption>
            <thead><tr>{['Okno', 'Vstup UTC', 'Výstup UTC', 'Hold barov', 'Hold min', 'Cena vstupu', 'Cena výstupu', 'Čisté PnL USDT', 'MAE %', 'MFE %', 'Dôvod', 'Trailing aktivovaný', 'SL pred trailingom', 'Trailing aktívny pred výstupnou sviečkou'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
            <tbody>{trades.map((t: any, index: number) => <tr key={`${t.window}:${t.entry_ts}:${index}`}><th scope="row">{t.window}</th><td>{t.entry_ts}</td><td>{t.exit_ts}</td><td>{number(t.hold_bars, 0)}</td><td>{number(t.hold_minutes ?? t.duration_min, 2)}</td><td>{number(t.entry_px, 8)}</td><td>{number(t.exit_px, 8)}</td><td>{number(t.pnl_net, 6)}</td><td>{number(t.mae)}</td><td>{number(t.mfe)}</td><td>{t.exit_reason}</td><td>{(t.trailing_activated ?? t.mfe_reached_trailing_start) == null ? '—' : (t.trailing_activated ?? t.mfe_reached_trailing_start) ? 'áno' : 'nie'}</td><td>{t.sl_before_trail ? 'áno' : 'nie'}</td><td>{t.trail_active_before_exit_candle == null ? '—' : t.trail_active_before_exit_candle ? 'áno' : 'nie'}</td></tr>)}</tbody>
          </table></div>
        </>}
      </details>;
    })}
  </section>;
}
function Metric({ label, value, hint, positive }: { label: string, value: string, hint: string, positive?: boolean }) { return <article className="metric"><span>{label}</span><strong className={positive ? 'positive' : ''}>{value}</strong><small>{hint}</small></article>; }
function MarketChart({ market, error }: { market: any, error: string }) { if (error) return <div className="empty-chart"><span>!</span><p>{error}</p></div>; if (!market?.candles?.length) return <div className="empty-chart"><span>⌁</span><p>Načítavam sviečky z burzy…</p></div>; const candles = market.candles; const min = Math.min(...candles.map((c: any) => c.low)); const max = Math.max(...candles.map((c: any) => c.high)); const range = max - min || 1; const scale = (value: number) => 92 - ((value - min) / range * 84); const last = candles[candles.length - 1]; const first = candles[0]; const change = (last.close / first.open - 1) * 100; const format = (value: number) => value.toFixed(value < 1 ? 8 : 4); const candleWidth = Math.max(.04, 62 / candles.length); const wickWidth = Math.max(.025, candleWidth * .35); return <div className="market-chart"><div className="market-price"><b>{format(last.close)} USDT</b><span className={change >= 0 ? 'positive' : 'negative'}>{change >= 0 ? '+' : ''}{change.toFixed(2)} %</span></div><div className="chart-layout"><div className="y-axis"><span>{format(max)}</span><span>{format((max + min) / 2)}</span><span>{format(min)}</span></div><svg viewBox="0 0 100 100" preserveAspectRatio="none">{candles.map((c: any, index: number) => { const x = index / candles.length * 100; const color = c.close >= c.open ? '#82edb6' : '#ff8798'; const top = scale(Math.max(c.open, c.close)); const body = Math.max(.7, Math.abs(scale(c.open) - scale(c.close))); return <g key={c.open_time}><line x1={x + candleWidth / 2} x2={x + candleWidth / 2} y1={scale(c.high)} y2={scale(c.low)} stroke={color} strokeWidth={wickWidth}/><rect x={x} y={top} width={candleWidth} height={body} fill={color}/></g>; })}</svg></div><div className="x-axis"><span>{new Date(first.open_time).toLocaleString('sk-SK')}</span><b>Posledných 24 hodín · {market.timeframe}</b><span>{new Date(last.close_time).toLocaleString('sk-SK')}</span></div><div className="market-footer"><span>{market.pair}</span><span>{market.source}</span></div></div>; }
function EquityChart({ points, range: chartRange }: { points: { time: string, value: number }[], range: 'hour' | 'day' | 'week' }) { if (points.length < 2) return <div className="empty-chart"><span>⌁</span><p>Krivka sa zobrazí po prvom uzatvorenom obchode.</p></div>; const bucket = (time: string) => { const date = new Date(time); if (Number.isNaN(date.getTime())) return time; if (chartRange === 'hour') return date.toISOString().slice(0, 13); if (chartRange === 'day') return date.toISOString().slice(0, 10); const monday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() - ((date.getUTCDay() + 6) % 7))); return monday.toISOString().slice(0, 10); }; const aggregated = new Map<string, { time: string, value: number }>(); points.forEach(point => aggregated.set(bucket(point.time), point)); const displayed = [...aggregated.values()]; const values = displayed.map(point => point.value); const min = Math.min(...values); const max = Math.max(...values); const valueRange = max - min || 1; const path = displayed.map((point, index) => `${index ? 'L' : 'M'} ${index / (displayed.length - 1) * 100} ${88 - ((point.value - min) / valueRange * 72)}`).join(' '); const first = displayed[0]; const last = displayed[displayed.length - 1]; const label = (time: string) => { const date = new Date(time); if (Number.isNaN(date.getTime())) return 'Štart'; return chartRange === 'hour' ? date.toLocaleString('sk-SK') : date.toLocaleDateString('sk-SK'); }; const rasterLabel = chartRange === 'hour' ? 'Hodiny' : chartRange === 'day' ? 'Dni / 24 h' : 'Týždne'; return <div className={`chart chart-${chartRange}`}><div className="chart-layout"><div className="y-axis"><span>{max.toFixed(2)} USDT</span><span>{((max + min) / 2).toFixed(2)}</span><span>{min.toFixed(2)}</span></div><svg viewBox="0 0 100 100" preserveAspectRatio="none"><path d={path} /></svg></div><div className="x-axis"><span>{label(first.time)}</span><b>{rasterLabel}</b><span>{label(last.time)}</span></div></div>; }
function Rejections({ reasons }: { reasons: Record<string, number> }) { const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1]); return entries.length ? <ul className="list">{entries.map(([reason, count]) => <li key={reason}><span>{reason}</span><b>{count}×</b></li>)}</ul> : <div className="empty"><span>◌</span><p>Diagnostika sa zobrazí po pripojení stratégie.</p></div>; }
function TradeList({ trades }: { trades: any[] }) { return trades.length ? <ul className="list">{trades.slice(0, 8).map(trade => <li key={trade.id}><span><b>{trade.pair}</b><small>{new Date(trade.opened_at).toLocaleString('sk-SK')} → {new Date(trade.closed_at).toLocaleString('sk-SK')}</small><small>{trade.entry_rate} → {trade.exit_rate} · {trade.exit_reason}</small><small>Poplatky: {Number((trade.raw?.entry_fee_usdt || 0) + (trade.raw?.exit_fee_usdt || 0)).toFixed(4)} USDT · {trade.raw?.holding_candles ?? '—'} sviečok</small></span><b className={Number(trade.profit_usdt) >= 0 ? 'positive' : 'negative'}>{Number(trade.profit_usdt) >= 0 ? '+' : ''}{trade.profit_usdt ?? 0}</b></li>)}</ul> : <div className="empty"><span>◎</span><p>Zatiaľ bez simulovaných obchodov.</p></div>; }
function VersionList({ versions }: { versions: any[] }) { return versions.length ? <ul className="version-list">{versions.slice(0, 5).map(version => <li key={version.id}><div><b>{version.name}</b><small>{version.note || 'Bez poznámky'}</small></div><span>{new Date(version.created_at).toLocaleDateString('sk-SK')}</span></li>)}</ul> : <p className="muted">Ešte nemáš uloženú verziu stratégie.</p>; }
function Comparison({ data }: { data: any }) { const diff = data.difference; return <div className="comparison"><span>ROZDIEL DRUHEJ VERZIE</span><div><b>Zisk {diff.profit_total_usdt ?? '—'} USDT</b><b>Obchody {diff.total_trades ?? '—'}</b><b>Úspešnosť {diff.win_rate_percent ?? '—'} %</b><b>Drawdown {diff.max_drawdown_percent ?? '—'} %</b></div></div>; }
createRoot(document.getElementById('root')!).render(<App />);
