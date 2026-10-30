# B1–B8 rev3 — dodatok A3: job `paper_tick` naplánovaný rolou `paper_cron_owner`

**NÁVRH NA SCHVÁLENIE ROMANOM — 30. 10. 2026. NIE JE SCHVÁLENÝ.**

Dopĺňa [FINAL B1–B8 rev3](PAPER-MEASUREMENT-B1-B8-REV3.md) §4.1–4.2 a
[A2](PAPER-MEASUREMENT-B1-B8-REV3-ADDENDUM-A2-CRON-ORIGIN-DEFINER.md).
Nemení FINAL rev3, A1, A2 ani migrácie P2/P3/A2. Schválenie A3 samo
nepovoľuje produkčné zmeny, merge, deploy ani 24 h beh P4.

## A3.0 Dôvod

Na `nofomo-paper-validation` (30. 10. 2026) vrátil pokus vytvoriť job ako
`postgres` pre inú rolu:

```
cron.schedule_in_database('paper_tick', '* * * * *',
  'select public.paper_tick_start()', 'postgres', 'paper_cron_owner', true)
→ ERROR XX000: must be superuser to create a job for another role
```

Transakcia sa vrátila: `paper_cron_owner` ostal NOLOGIN, 0 jobov, 0 tickov.
Rev3 §4.2 vyžaduje, aby job bežal pod `paper_cron_owner`, lebo
`paper_tick_start()` odmietne iný `session_user`. CI to nezachytilo, lebo
tam job plánoval superuser `supabase_admin`.

## A3.1 Mechanizmus

Job si naplánuje sám `paper_cron_owner`, takže pg_cron zapíše
`username = paper_cron_owner`. Rola `postgres` ho k tomu dočasne použije:

| Krok (jedna transakcia) | Účel |
| --- | --- |
| `grant paper_cron_owner to postgres with inherit false, set true` | umožní `SET ROLE` (grantor `postgres`; automatické ADMIN členstvo od `supabase_admin` ostáva) |
| `grant usage on schema cron to paper_cron_owner` | `cron.schedule` je v schéme `cron`; EXECUTE na `schedule(text,text,text)` už má PUBLIC |
| `alter role paper_cron_owner login` | pg_cron sa pripája cez libpq (`cron.use_background_workers = off`, `cron.host = localhost`) |
| `set local role paper_cron_owner; select cron.schedule('paper_tick','* * * * *','select public.paper_tick_start()')` | job s vlastníkom `paper_cron_owner` v aktuálnej databáze |
| `revoke usage on schema cron from paper_cron_owner; revoke paper_cron_owner from postgres` | dočasné práva zaniknú v tej istej transakcii |
| samooverenie | bez USAGE, bez SET/INHERIT členstva, presne jeden aktívny job so správnym príkazom; inak sa všetko vráti |

Vypnutie jobu (`3_stop.sql`) používa rovnaký vzor: `unschedule`, odobratie
práv, `NOLOGIN` a samooverenie. Overenie (`2_verify.sql`) iba číta.

Súbory a SHA-256:

| Súbor | SHA-256 |
| --- | --- |
| `ci/paper-supabase/a3/1_schedule.sql` | `aaa1264ee34553ab0f4559bcb1578e0c9eb53d9c1f856496614b68deaac96994` |
| `ci/paper-supabase/a3/2_verify.sql` | `62021a7b69a30f7ed2f691c1223789c4e78e3e77a432d6e0cabeb0d85d45830b` |
| `ci/paper-supabase/a3/3_stop.sql` | `71922adc92421037e99656dcb7cde6984974480fe57383ab9a9a6d852ff7bee4` |

## A3.2 Bezpečnosť

- Okno dočasných práv je zvonka neviditeľné: vznikajú aj zanikajú v jednej
  transakcii.
- Počas behu jobu `paper_cron_owner` nemá USAGE na `cron`. Nevie zmazať
  vlastné logy (PUBLIC DELETE na `job_run_details` je bez USAGE nedosiahnuteľné),
  meniť ani rušiť joby. Rev3 vyžaduje, aby sa záznamy počas testu nemazali.
- `paper_cron_owner` má počas testu LOGIN. Heslo nemá nastavené, takže pri
  autentifikácii `scram-sha-256` sa zvonka neprihlási. Ak spravovaný `pg_hba`
  pre `localhost` heslo vyžaduje, job zlyhá (pozri A3.4). Nastavenie hesla
  nie je súčasťou A3.
- Po `unschedule` zmizne riadok `cron.job`. Ticky jobu potom A2 hodnotí ako
  `no_evidence`, teda fail-closed. Počas testu sa job preto neodstraňuje.

## A3.3 Testy

| ID | Scenár a očakávaný výsledok | Zaradenie |
| --- | --- | --- |
| AT59 | `postgres` volá `schedule_in_database` pre `paper_cron_owner` → `must be superuser to create a job for another role`, žiadny job. | A2 CI (lokálne) + zaznamenané na spravovanom projekte |
| AT60 | `1_schedule.sql` ako `postgres`: job s `username = paper_cron_owner`, aktívny, správny príkaz. Po transakcii nemá `paper_cron_owner` USAGE na `cron` a `postgres` nemá SET/INHERIT členstvo. | A2 CI + spravované |
| AT61 | Prvý dokončený beh je `succeeded`. `2_verify.sql` ukáže tick s `succeeded` a `paper_private.origin` vráti `verified` (doplnenie AT58a). | A2 CI + spravované |
| AT62 | Počas behu `paper_cron_owner`: DELETE na `cron.job_run_details` a `cron.unschedule` → `permission denied for schema cron`. | A2 CI |
| AT63 | `3_stop.sql`: job odstránený, NOLOGIN, bez USAGE a členstva. | A2 CI + spravované |
| AT64 | Autentifikácia cron pripojenia na spravovanom projekte. | **Iba spravované**; CI ju nedokazuje |

## A3.4 Riziká a otvorené body

1. **Autentifikácia (AT64).** `pg_hba` sa z `postgres` čítať nedá (`42501`).
   Ak spravovaný projekt pre `localhost` vyžaduje heslo, prvý beh skončí
   `failed` s chybou pripojenia. Riešenie (heslo, background workers, podpora
   Supabase) by bolo samostatné rozhodnutie.
2. Odchýlka od rev3 §4.1: job nevytvára administrátor, ale rola cronu
   s dočasnými právami.
3. Ak by `paper_cron_owner` niekto neskôr pridal USAGE na `cron`, získal by
   DELETE na vlastné logy. AT55 a samooverenie fáz to kontrolujú.

## Záznam zmien

| Dátum | Zmena | Stav |
| --- | --- | --- |
| 30. 10. 2026 | Návrh A3 po odmietnutí `schedule_in_database` pre inú rolu na spravovanom projekte. | NÁVRH; overenie v CI, skúška na spravovanom projekte čaká na súhlas. |
