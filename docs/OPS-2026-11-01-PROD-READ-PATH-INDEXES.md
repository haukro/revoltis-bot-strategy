# Prevádzková zmena 1. 11. 2026: indexy pre čítanie `simulation_runs` a `optimizer_runs`

Projekt: produkčná Supabase `vdglyxhlmukxeqynguke` (revoltis-bot-strategy).
Schválil: Roman. Vykonal: ChatGPT cez produkčný Supabase SQL editor. Overenie: iba čítanie.

## Problém

- `/api/dashboard` a `/api/optimizer-latest` padali na `statement_timeout`
  (8 s, rola `authenticator`). Postgres logoval `canceling statement due to
  statement timeout`, PostgREST vracal 500, frontend hlásil „Aplikáciu sa
  nepodarilo načítať“. Opakovane 31. 10. o 02:08, 16:05 a 23:56 UTC.
- `/api/dashboard`: `simulation_runs?order=finished_at.desc&limit=30` robil
  Seq Scan + Sort cez 63 796 riadkov (42 MB tabuľka, 532 MB TOAST, index iba na
  `id`). V `pg_stat_statements` 35 úspešných behov, priemer 5,2 s, maximum 7,8 s.
- `/api/optimizer-latest`: filter `result->>'version_id'` musel rozbaliť `result`
  každého z 13 riadkov `kind=aggregate` (spolu 43 MB komprimovaných, riadky do
  9,2 MB), hoci hľadaný riadok má 6,4 kB. Žiadny úspešný beh.

## Zmena (vykonaná samostatnými príkazmi, mimo transakcie)

```sql
create index concurrently if not exists simulation_runs_finished_at_idx
  on public.simulation_runs (finished_at desc);
create index concurrently if not exists optimizer_runs_kind_version_finished_idx
  on public.optimizer_runs ((request->>'kind'), (result->>'version_id'), finished_at desc);
analyze public.simulation_runs;
analyze public.optimizer_runs;
```

Repo: `supabase/migrations/20261101003700_prod_read_path_indexes.sql` je
idempotentný záznam (`IF NOT EXISTS`, bez `CONCURRENTLY`). Na už opravenej
produkcii existujúce indexy preskočí. Na inom naplnenom projekte však obyčajný
`CREATE INDEX` počas tvorby blokuje zápisy do tabuľky; nie je ekvivalentom
ručného `CREATE INDEX CONCURRENTLY`. Produkčná tabuľka
`supabase_migrations.schema_migrations` túto zmenu neobsahuje, pretože išla cez
SQL editor.

## Overenie (1. 11. 2026, iba čítanie)

| Kontrola | Výsledok |
| --- | --- |
| Indexy | oba `indisvalid = true`, `indisready = true`; `simulation_runs_finished_at_idx` 1,4 MB, `optimizer_runs_kind_version_finished_idx` 16 kB |
| `ANALYZE` | `simulation_runs` 00:37:01 UTC, `optimizer_runs` 00:37:08 UTC (predtým nikdy; štatistika uvádzala 6 riadkov namiesto 104) |
| Plán `simulation_runs` | `Index Scan using simulation_runs_finished_at_idx` (predtým Seq Scan + Sort) |
| Plán `optimizer_runs` | `Index Scan using optimizer_runs_kind_version_finished_idx`, `Index Cond` na `kind` aj `version_id` (predtým Seq Scan + Filter + Sort) |
| Produkcia po zmene | `/api/dashboard` 4× `200` (00:37:21 až 00:38:18 UTC), `/api/optimizer-latest` 4× `200` (00:37:24 až 00:38:17 UTC) |
| Timeouty po zmene | jeden o 00:38:23 UTC, patrí `/api/optimizer/validation-replay` (mimo tejto zmeny) |
| Čas DB, meranie ChatGPT | `/api/dashboard` (`simulation_runs`) približne **51 ms**, `/api/optimizer-latest` (`optimizer_runs`) **3,2 ms**; v oboch prípadoch tri nové DB požiadavky |
| Čas DB, `pg_stat_statements` (kontrola) | `optimizer_runs` dotaz PostgREST: 4 behy po zmene, spolu 10,2 ms, priemer 2,5 ms (predtým žiadny úspešný beh). `simulation_runs`: štatistika je kumulovaná od 14. 10., rozdiel 35 → 39 behov dáva 4 nové behy spolu ~193 ms, priemer ~48 ms, minimum 30,2 ms (predtým priemer 5,2 s) |
| Trvanie zmeny | `create index concurrently` na `optimizer_runs` 14,8 s; `analyze optimizer_runs` 7,3 s |

Návrat: `drop index concurrently simulation_runs_finished_at_idx;` a
`drop index concurrently optimizer_runs_kind_version_finished_idx;`.

## Otvorené (mimo tejto zmeny)

1. `/api/optimizer/validation-replay` číta `optimizer_runs?order=finished_at.desc&limit=100`
   bez výberu stĺpcov, takže vracia najviac 100 z približne 104 riadkov a pri
   každom vybranom riadku aj celé veľké JSON stĺpce. Tabuľka má približne
   121 MB komprimovaných `result` dát celkovo; bez merania to nemožno označiť
   za veľkosť konkrétnej HTTP odpovede. Index samotný tento prenos nevyrieši.
   Endpoint sa volá pri načítaní stránky a bol priradený k zostávajúcemu
   `statement_timeout`. Neošetrené `raise_for_status` môže vytvoriť
   „Exception in ASGI application“, ale priradenie iného súbežného Vercel logu
   (vrátane `/api/cron/paper/outbox-tick`) k tejto príčine nie je potvrdené.
   Vyžaduje zmenu kódu a samostatné rozhodnutie. Analýza kódu ukazuje, že
   preflight hľadá dve historické cieľové varianty a následne používa z
   `result` najmä `variant_results`, `locked_pairs`, `source`,
   `replay_snapshots` a `version_id`. Ďalší krok je zúžiť kandidátne riadky
   a prenášané JSON polia bez zmeny replay logiky.
2. Projekt v Supabase je na pláne FREE s označením „Exceeding usage limits“
   (snímka dashboardu). Malá inštancia (`shared_buffers` 224 MB) a obmedzený
   disk pravdepodobne prispievajú k pomalému čítaniu veľkých tabuliek. Hypotéza,
   neoverené.
