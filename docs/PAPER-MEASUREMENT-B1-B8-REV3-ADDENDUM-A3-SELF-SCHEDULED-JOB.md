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

**Zastavenie P4 je deaktivácia (`3_pause.sql`), nie odstránenie.** Rovnakým
vzorom sa v jednej transakcii dočasne pridá aj EXECUTE na `cron.alter_job`
(nemá PUBLIC EXECUTE, `postgres` ho má s GRANT OPTION). Job sa nastaví na
`active = false`, rola dostane späť `NOLOGIN` a všetky dočasné práva zaniknú.
Job aj `cron.job_run_details` ostávajú, takže ticky P4 ostanú overiteľné
podľa A2 aj pri neskoršej kontrole. `3_stop.sql` (`unschedule`) slúži iba na
upratanie po uzavretí nezávislej kontroly P4 alebo v CI.

Overenie má dve časti: `2_verify.sql` iba číta, `2b_verify_roles.sql` beží
v transakcii ukončenej `ROLLBACK`. V nej sa dočasne pridá SET na
`paper_rpc_owner` a `paper_cron_owner` a skontroluje sa:
`paper_private.origin` pod `paper_rpc_owner` (kontext assess RPC), zákaz
čítania cronu pre `paper_rpc_owner` a zákaz mazania logov a `unschedule` pre
`paper_cron_owner`. Nič z toho neostane uložené.

Súbory a SHA-256:

| Súbor | SHA-256 |
| --- | --- |
| `ci/paper-supabase/a3/1_schedule.sql` | `aaa1264ee34553ab0f4559bcb1578e0c9eb53d9c1f856496614b68deaac96994` |
| `ci/paper-supabase/a3/2_verify.sql` | `62021a7b69a30f7ed2f691c1223789c4e78e3e77a432d6e0cabeb0d85d45830b` |
| `ci/paper-supabase/a3/2b_verify_roles.sql` | `c4803202fd3ae5798f4963448f326ca1b0e173170785180410950037fc57838a` |
| `ci/paper-supabase/a3/3_pause.sql` | `11c9573dbbbd862a4d83def0107f7e85e6da69d7f4db7e6ebf9db6ae16883adc` |
| `ci/paper-supabase/a3/3_stop.sql` (iba upratanie) | `636f32f6db46f7e1877c437bdb96d178ea1aab184976396cbadf19fc37d0136d` |

`3_stop.sql` sa oproti skúške z 30. 10. (`71922adc…`) zmenil iba v úvodnom
komentári: je označený ako súbor iba na upratanie.

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
  `no_evidence`, teda fail-closed. Potvrdené na spravovanom projekte: tick
  skúšky po odstránení vracia `no_evidence`. Preto sa P4 zastavuje iba
  deaktiváciou.

## A3.3 Testy

| ID | Scenár a očakávaný výsledok | Zaradenie |
| --- | --- | --- |
| AT59 | `postgres` volá `schedule_in_database` pre `paper_cron_owner` → `must be superuser to create a job for another role`, žiadny job. | A2 CI (lokálne) + zaznamenané na spravovanom projekte |
| AT60 | `1_schedule.sql` ako `postgres`: job s `username = paper_cron_owner`, aktívny, správny príkaz. Po transakcii nemá `paper_cron_owner` USAGE na `cron` a `postgres` nemá SET/INHERIT členstvo. | A2 CI + spravované |
| AT61 | Prvý dokončený beh je `succeeded`. `2_verify.sql` ukáže tick s `succeeded` a `paper_private.origin` vráti `verified` (doplnenie AT58a). | A2 CI + spravované |
| AT62 | Počas behu `paper_cron_owner`: DELETE na `cron.job_run_details` a `cron.unschedule` → `permission denied for schema cron`. | A2 CI |
| AT63 | `3_stop.sql`: job odstránený, NOLOGIN, bez USAGE a členstva. | A2 CI + spravované |
| AT64 | Autentifikácia cron pripojenia na spravovanom projekte. | **Iba spravované**; CI ju nedokazuje |
| AT65 | `2b_verify_roles.sql`: `origin = verified` pod `paper_rpc_owner`; čítanie cronu (`paper_rpc_owner`), mazanie logov a `unschedule` (`paper_cron_owner`) → `permission denied for schema cron`. Po `ROLLBACK` žiadne SET/INHERIT členstvo. | A2 CI + spravované (doplní AT58a) |
| AT66 | `3_pause.sql`: job ostane s `active = false`, logy ostanú, NOLOGIN, bez USAGE a bez EXECUTE na `alter_job`. Počas aspoň jednej minútovej hranice nepribudne beh a `origin` zostane `verified`. | A2 CI + spravované pri P4 |

## A3.4 Skúška na spravovanom projekte (30. 10. 2026)

`nofomo-paper-validation`, súbory z `7a07a74`, hashe overené pred spustením:

| Krok | Výsledok |
| --- | --- |
| AT59 | `schedule_in_database` pre inú rolu → `XX000 must be superuser to create a job for another role` |
| AT60 (`1_schedule.sql`) | `jobid = 1`, `paper_cron_owner`, `postgres`, aktívny; bez USAGE a dočasného členstva |
| AT64 | prvý beh 20:22:00 UTC **succeeded**, 13,4 ms, `1 row`. Autentifikácia cron pripojenia funguje |
| AT61 (čiastočne) | `tick_run_status` = `succeeded`. `origin` pod `paper_rpc_owner` sa tu nevolal (doplní AT65) |
| `3_stop.sql` (`71922adc…`) | job odstránený, NOLOGIN, bez USAGE, 0 dočasných členstiev |

## A3.5 Riziká a otvorené body

1. AT65 a AT66 na spravovanom projekte ešte neprebehli.
2. Odchýlka od rev3 §4.1: job nevytvára administrátor, ale rola cronu
   s dočasnými právami.
3. Ak by `paper_cron_owner` niekto neskôr pridal USAGE na `cron`, získal by
   DELETE na vlastné logy. AT55 a samooverenie fáz to kontrolujú.

## Záznam zmien

| Dátum | Zmena | Stav |
| --- | --- | --- |
| 30. 10. 2026 | Návrh A3 po odmietnutí `schedule_in_database` pre inú rolu na spravovanom projekte. | NÁVRH; CI 41/41 (`7a07a74`). |
| 30. 10. 2026 | Skúška na spravovanom projekte: AT59, AT60, AT64 prešli, AT61 čiastočne (§A3.4). | So súhlasom Romana; job odstránený. |
| 30. 10. 2026 | Zastavenie P4 deaktiváciou (`3_pause.sql`) namiesto `unschedule`, overenie pod rolami (`2b_verify_roles.sql`), AT65–AT66. | NÁVRH; čaká na CI a na schválenie A3. |
