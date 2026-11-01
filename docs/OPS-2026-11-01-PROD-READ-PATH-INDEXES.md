# Prevádzková zmena 1. 11. 2026: indexy pre čítanie `simulation_runs` a `optimizer_runs`

Projekt: produkčná Supabase `vdglyxhlmukxeqynguke` (revoltis-bot-strategy).
Schválil: Roman. Vykonal: Roman v SQL editore. Overenie: iba čítanie.

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
idempotentný záznam (`IF NOT EXISTS`, bez `CONCURRENTLY`). Produkčná tabuľka
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

Návrat: `drop index concurrently simulation_runs_finished_at_idx;` a
`drop index concurrently optimizer_runs_kind_version_finished_idx;`.

## Otvorené (mimo tejto zmeny)

1. `/api/optimizer/validation-replay` číta `optimizer_runs?order=finished_at.desc&limit=100`
   bez výberu stĺpcov, teda všetkých 104 riadkov so 121 MB komprimovaných dát
   v `result`. Index to nevyrieši. Volá sa pri načítaní stránky a padá na
   `statement_timeout`. Neošetrená výnimka (`raise_for_status` bez `try`) navyše
   vytvára záznam „Exception in ASGI application“, ktorý Vercel môže priradiť
   k inej súbežnej požiadavke (pravdepodobne aj záznam pri
   `/api/cron/paper/outbox-tick` o 00:40:05 UTC; jeho RPC volania vrátili `200`).
   Vyžaduje zmenu kódu, samostatné rozhodnutie.
2. Projekt v Supabase je na pláne FREE s označením „Exceeding usage limits“
   (snímka dashboardu). Malá inštancia (`shared_buffers` 224 MB) a obmedzený
   disk pravdepodobne prispievajú k pomalému čítaniu veľkých tabuliek. Hypotéza,
   neoverené.
