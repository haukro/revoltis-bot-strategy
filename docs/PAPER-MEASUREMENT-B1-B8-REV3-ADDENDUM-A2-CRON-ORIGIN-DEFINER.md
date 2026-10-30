# B1–B8 rev3 — dodatok A2: overenie pôvodu cez privátnu funkciu (fallback A1.4)

**NÁVRH NA SCHVÁLENIE ROMANOM — 30. 10. 2026. NIE JE SCHVÁLENÝ.**

Dopĺňa [FINAL B1–B8 rev3](PAPER-MEASUREMENT-B1-B8-REV3.md) §4.2 a
[dodatok A1](PAPER-MEASUREMENT-B1-B8-REV3-ADDENDUM-CRON-READ.md) §A1.4.
FINAL rev3, R1–R5, AT1–AT51 a súbory migrácií P2/P3 sa nemenia. Schválenie
A2 samo nepovoľuje produkčné DDL, merge, deploy, cron job ani shadow test.

## A2.0 Dôvod: AT51 na spravovanom Supabase neprešla

Izolovaný projekt `nofomo-paper-validation` (ref `zioazncbennqmscfnivv`),
pg_cron 1.6.4, pg_net 0.20.4, 30. 10. 2026. Kroky A1 boli vyskúšané
v jednej transakcii a vrátené, nič nezostalo uložené:

| A1 krok | Výsledok na spravovanej platforme |
| --- | --- |
| USAGE na `cron` + SELECT na `cron.job`, `cron.job_run_details` pre `paper_rpc_owner` | povolené |
| REVOKE PUBLIC DELETE na `cron.job_run_details` | bez účinku: grant udelil `supabase_admin`, `postgres` odoberá iba vlastné granty |
| REVOKE PUBLIC EXECUTE na `schedule` ×2, `unschedule` ×2 | bez účinku, z rovnakého dôvodu |
| CREATE POLICY na `cron.job` | `42501 must be owner of table job` |
| CREATE POLICY na `cron.job_run_details` | `42501 must be owner of table job_run_details` |

Vlastníkom cron schémy, tabuliek aj funkcií je `supabase_admin` (superuser).
`postgres` má `rolsuper=false`, `rolbypassrls=true` a práva na cron
s GRANT OPTION. Samotný krok 1 by čítacej role otvoril PUBLIC DELETE na logoch
a PUBLIC EXECUTE na správe jobov, čo porušuje AT50. Preto sa A1.1–A1.2
na spravovanom prostredí nedajú splniť a podľa A1.4 sa navrhuje fallback.

## A2.1 Mechanizmus

1. Nová schéma `paper_cron_proof` vo vlastníctve `postgres`. Bez PUBLIC,
   USAGE iba pre `paper_rpc_owner`. Schéma `paper_private` z P2 ostáva
   nezmenená a jej vlastník nemôže pomocnú funkciu zmazať ani nahradiť.
2. Funkcia `paper_cron_proof.tick_run_status(p_tick_id uuid) returns text`:
   - `SECURITY DEFINER`, vlastník `postgres`, `search_path = ''`;
   - EXECUTE iba `postgres` (vlastník) a `paper_rpc_owner`, bez PUBLIC
     a bez aplikačných rolí;
   - jediný vstup je `tick_id`. PID a čas načíta z uloženého ticku
     `public.paper_measure_ticks`, nikdy od volajúceho;
   - výstupom je jediný token: `no_tick`, `no_evidence`, `in_progress`,
     `succeeded`, `failed`. Nevracia riadky, polia ani text cron logov;
   - iba statické SQL so schémou v každom názve, bez dynamického SQL,
     zápisov a volaní správy jobov.
3. `postgres` dostane iba stĺpcový SELECT `(tick_id, backend_pid, created_at)`
   na `public.paper_measure_ticks`. Ide o jediný nový grant na existujúci objekt.
4. `paper_private.origin` sa nahradí novou migráciou (`create or replace`,
   vlastník a ACL ostávajú). Rozhodovacia logika je nezmenená, mení sa iba
   zdroj dôkazu (§A2.2).
5. `paper_rpc_owner` nedostane USAGE na `cron`, žiadny grant ani politiku
   na cron objekty a nepribudne mu žiadne členstvo.

Migrácia: `supabase/migrations/20261030200000_paper_measurement_a2_cron_origin.sql`
(SHA-256 `c0f275382ed6436a226a2b6cec333461c1cf4ebd0c84709e9338f6dab39978d6`).
Musí ju spustiť `postgres`, vyžaduje P2 aj P3 a odmietne opakované použitie.
Na konci overí vlastníka, `SECURITY DEFINER`, `search_path`, presné ACL,
skalárny návratový typ, neprítomnosť prístupu aplikačných rolí, žiadny priamy
prístup čítacej roly ku cronu a odobraté dočasné členstvo. Pri akejkoľvek
odchýlke sa celá migrácia vráti. Nainštaluje sa aj bez pg_cron, lebo funkcia
cron tabuľky kontroluje cez `to_regclass` až pri behu.

## A2.2 Zachované podmienky pôvodného `paper_private.origin`

| Podmienka P2 | A2 |
| --- | --- |
| `j.jobname = 'paper_tick'` | rovnako |
| `j.username = 'paper_cron_owner'`, `d.username = 'paper_cron_owner'` | rovnako, v jobe aj v logu |
| `j.database = current_database()`, `d.database = current_database()` | rovnako, v jobe aj v logu |
| `d.job_pid = tick.backend_pid` | rovnako; PID číta z uloženého ticku |
| `d.start_time <= tick.created_at` a (`end_time` null alebo `>= created_at`) | rovnako |
| najnovší zodpovedajúci beh: `order by start_time desc, runid desc limit 1` | rovnako; starší beh s rovnakým PID neprekryje aktuálny |
| `succeeded` iba pri `status='succeeded'` a `end_time >= created_at` | rovnako (`succeeded` bez `end_time` = `in_progress`) |
| `failed` → `unverifiable` aj počas lehoty | rovnako |
| inak `pending` pre `0 ≤ now − created_at ≤ origin_grace_ms`, potom `unverifiable` | rovnako; lehota a hranica sa nemenia |
| `insufficient_privilege` / `undefined_table` → grace vetva | rovnako; navyše `undefined_function` a `invalid_schema_name` (chýbajúca alebo zmazaná pomocná funkcia) |
| neexistujúci tick → výnimka `query returned no rows` | rovnako |
| chýbajúce cron tabuľky → žiadny dôkaz | rovnako (`to_regclass`) |

A1.3 platí bez zmeny. Chýbajúci alebo nečitateľný dôkaz nikdy neznamená
overený pôvod.

## A2.3 Bezpečnostný model

- Volajúci ovplyvní iba `tick_id`. Nevie podstrčiť PID, čas, job ani databázu.
- Výstup je token. Cron logy, príkazy jobov ani `return_message` neopustia
  funkciu.
- Aplikačné roly (`anon`, `authenticated`, `service_role`, `authenticator`)
  a `paper_cron_owner` nemajú EXECUTE na funkciu, USAGE na schému ani SET/USAGE
  členstvo v `paper_rpc_owner`. Schéma nie je vystavená cez PostgREST.
- `paper_rpc_owner` ostáva `NOLOGIN`, bez `BYPASSRLS` a bez práv na cron.
  Funkciu nemôže nahradiť ani zmazať.
- Prevzaté riziko: vlastník funkcie `postgres` má na spravovanej platforme
  `BYPASSRLS` a vidí všetky cron logy. `postgres` je administrátorská rola
  projektu, ktorá tieto práva má aj bez A2. Na spravovanom projekte aj v lokálnom
  obraze je členom `pg_read_all_data` (INHERIT), `service_role`, `pg_monitor`
  a `supabase_privileged_role`. Číta teda všetky tabuľky už teraz. A2 mu pridáva
  iba explicitné čítanie troch stĺpcov tickov, aby funkcia nezávisela od
  implicitného `pg_read_all_data` (overuje AT57). Bezpečnosť stojí na tele funkcie, preto je jej text súčasťou
  schválenia a CI kontroluje vlastníka, ACL aj absenciu zápisov a dynamického SQL.

## A2.4 Model `postgres` s BYPASSRLS a bez neho

- **S BYPASSRLS (spravovaný projekt, zistené 30. 10. 2026):** funkcia vidí
  tick aj cron záznam. Pôvod sa overí podľa §A2.2.
- **Bez BYPASSRLS:** RLS na `paper_measure_ticks` tick skryje (`no_tick`).
  Aj pri viditeľnom ticku pôvodná pg_cron politika `username = current_user`
  skryje cudzie záznamy (`no_evidence`). Výsledok je vždy fail-closed:
  `pending` v lehote, potom `unverifiable`. Nikdy `verified`.
- CI musí pokryť oba režimy (AT56) a zaznamenať skutočný stav `rolbypassrls`.
  Zmena režimu na spravovanom projekte by znamenala, že všetky behy po bráne
  budú neoveriteľné. Pred P4 aj pred každým ďalším spravovaným nasadením sa
  preto kontroluje `rolbypassrls` a práva `postgres` na cron (AT52).

## A2.5 Vzťah k A1

- A2 nahrádza mechanizmus A1.1 (granty) a A1.2 (politiky) v spravovanom
  prostredí. Rozsah viditeľnosti A1.2 sa v A2 vynucuje filtrom vo funkcii.
- CI kandidát A1 (granty a politiky pre `paper_rpc_owner` v
  `tests/paper_p2_supabase`) sa s A2 nesmie kombinovať. Samooverenie A2
  sa pri ňom odmietne nainštalovať (`a2_reader_cron_usage`). A2 potrebuje
  vlastný CI job na čistom zásobníku.
- AT47–AT50 sa pre A2 nahrádzajú AT53–AT55. AT51 je zaznamenaná ako neúspešná
  pre A1 a pre A2 sa znovu otvára ako AT58a (inštalácia) a AT58b (upgrade).

## A2.6 Akceptačné testy

| ID | Scenár a očakávaný výsledok | Zaradenie |
| --- | --- | --- |
| AT52 | Model platformy: `postgres` nie je superuser, má USAGE na `cron` a SELECT na obe tabuľky. Stav BYPASSRLS sa zaznamená. Funkcia: vlastník `postgres`, SECURITY DEFINER, `search_path=""`, návrat `text` (nie set), ACL presne `{postgres=X/postgres,paper_rpc_owner=X/postgres}`, bez `execute`/zápisov/správy jobov v tele. `postgres` má SELECT iba na `tick_id, backend_pid, created_at`. | A2 CI + spravované prostredie |
| AT53 | Skutočný cron job `paper_tick` pod `paper_cron_owner` → `succeeded`, `origin = verified`. | A2 CI; zopakovať v spravovanom prostredí v P4 |
| AT54 | Negatívna matica: (a) PID reuse: starší failed + novší succeeded = `verified`; starší succeeded + novší failed = `unverifiable`; rovnaký PID iba mimo intervalu = žiadny dôkaz. (b) Iný job rovnakého vlastníka (aj skutočný cron), (c) rovnaký názov pod iným vlastníkom, (d) job inej databázy (dočasná zmena databázy skutočného jobu v transakcii, ktorá sa vráti; unikátny index `(jobname, username)` druhý job nedovolí), (e) log s inou databázou alebo používateľom, (f) job čítacej roly, (g) beh začatý po ticku alebo skončený pred ním, (h) chýbajúci log → `pending` na hranici `created_at + grace`, `unverifiable` o 1 ms neskôr. (i) `running`/`starting` a `succeeded` bez `end_time` = `in_progress`. (j) Rovnaký `start_time`: vyhráva vyšší `runid`. (k) Novší `running` skryje starší `succeeded`. (l) Neznámy tick: funkcia `no_tick`, origin výnimka ako v P2. | A2 CI (syntetické neaktívne joby) + emulácia |
| AT55 | `anon`, `authenticated`, `service_role`, `authenticator`, `paper_cron_owner`: bez EXECUTE, USAGE a členstva; volanie aj cez PostgREST odmietnuté. `paper_rpc_owner`: bez USAGE na `cron`, bez cron grantov a politík. SELECT, DELETE, `schedule` a `unschedule` → `permission denied for schema cron`. Nevie funkciu nahradiť ani zmazať. | A2 CI + spravované prostredie |
| AT56 | `postgres` s BYPASSRLS → overenie funguje. Bez BYPASSRLS → `no_tick`; s viditeľným tickom → `no_evidence`; origin nikdy `verified`. Pôvodný stav sa obnoví. | A2 CI (iba lokálne, na spravovanom sa neprepína) |
| AT57 | Degradácia: odobraté EXECUTE, zmazaná funkcia/schéma, `postgres` bez USAGE na `cron`, bez SELECT na ticky (v oboch prípadoch spolu s odobratím zdedeného `pg_read_all_data`), chýbajúce cron tabuľky → `pending`, potom `unverifiable`. Bez `pg_read_all_data`, iba s explicitnými grantmi, overenie funguje. Opakovaná aplikácia A2 je odmietnutá. | A2 CI + emulácia |
| AT58a | Inštalácia na spravovanej platforme (`nofomo-paper-validation`): A2 sa nainštaluje rolou `postgres` bez superusera, samooverenie migrácie prejde a AT52–AT55 platia na spravovanej DB. Zaznamená sa verzia pg_cron/pg_net, SHA-256 uloženého SQL a výsledky. | Otvorená brána spravovaného prostredia; overuje sa po schválení A2 |
| AT58b | Správanie po podporovanom upgrade alebo obnove pg_cron: vlastník, ACL a výsledky AT52–AT55 zostanú zachované. Výsledky pred/po a obe verzie sa zaznamenajú. | **Neoverené**, kým sa upgrade alebo obnova skutočne nevykoná. Úspešná AT58a ani CI ju nenahrádzajú. |

Testy: `backend/tests/paper_a2_supabase/test_cron_origin_definer.py`.
Spúšťajú sa iba pri `PAPER_A2_SUPABASE=1` v jobe `paper-a2-supabase`.

Pravidlá testovacích dát:

- pg_cron má unikátny index `(jobname, username)`. Existuje teda jediný job
  `paper_tick`/`paper_cron_owner`, a to skutočný job z AT53. Po prvom úspešnom
  behu sa deaktivuje a čaká sa, kým nemá žiadny beh `starting`/`running`.
  Na konci modulu test overí, že medzitým nevznikol ďalší skutočný beh.
- Syntetické riadky AT54 sa viažu na tento job alebo na neaktívne joby iných
  vlastníkov. Používajú PID ≥ 2 000 000 000 (mimo rozsahu jadra) a pevný
  historický čas, takže sa so skutočnými behmi nemôžu zmiešať.
- Model spravovaného projektu (`postgres` s BYPASSRLS) nastaví fixture pred
  prvým testom. Pôvodný stav lokálneho obrazu sa vypíše do logu a na konci obnoví.
- Opakovaná aplikácia A2 sa skúša ako `postgres` cez `DB_URL` zásobníka,
  teda rovnakou rolou ako `supabase db push` a spravovaný projekt.

## A2.7 CI job `paper-a2-supabase`

Je v `.github/workflows/ci.yml`. Čistý lokálny zásobník (ubuntu-24.04,
Supabase CLI 2.122.0, rovnaký `prepare.py`):

1. `supabase start` aplikuje iba P2.
2. `supabase_admin` nainštaluje pg_cron (`pg_catalog`) a pg_net (`extensions`),
   rovnako ako na spravovanom projekte.
3. Bajtovo overené kópie P3 a A2 (vypíše SHA-256), `db push --local --yes`
   ako `postgres`.
4. `db advisors --local --type security --level warn --fail-on error`.
5. `pytest tests/paper_a2_supabase` s `PAPER_A2_SUPABASE=1`.

`tests/paper_p2_supabase` (CI kandidát A1) sa v tomto jobe nespúšťa.

## A2.8 Doplnkový dôkaz: emulácia

Spustená 30. 10. 2026 na PostgreSQL 16.15. **Nie je to pg_cron.** Schéma
`cron` mala stĺpce pg_cron 1.6.4 a ACL, RLS a politiky zistené na spravovanom
projekte. `postgres` bol nastavený ako `nosuperuser` s `BYPASSRLS` a pridané boli
aj Supabase default privileges pre funkcie. Na P2 + P3 + A2 prešlo
**141/141 kontrol**:

- 19 prípadov × 3 časy (v lehote, presne na hranici, 1 ms po nej): nový
  `origin` sa vo všetkých 57 porovnaniach zhoduje s pôvodným telom P2
  spusteným superuserom (ekvivalent A1 politík);
- A1 `CREATE POLICY` zlyhal rovnako ako na spravovanom projekte;
- AT52, AT55–AT57 a odmietnutie opakovanej aplikácie.

Emulácia overuje SQL a model oprávnení. Ide o výsledok dodaného emulačného
harnessu, nie o CI so skutočným pg_cron. Nenahrádza AT53–AT57 v jobe
`paper-a2-supabase` ani AT58a/AT58b.

## A2.9 Otvorené riziká a body na schválenie

1. Schváliť presné znenie funkcie a jediný nový grant (stĺpcový SELECT pre
   `postgres`).
2. `postgres` s BYPASSRLS je predpoklad overenia. Ak ho platforma zmení,
   meranie bude fail-closed a P4 neprejde (nie falošný PASS).
3. Upgrade pg_cron so zmenou stĺpcov alebo stavov sa prejaví ako
   `no_evidence`/výnimka, teda fail-closed. Kontroluje AT58b; bez vykonaného upgrade ostáva neoverené.
4. Verzie v histórii migrácií spravovaného projektu (`20261030…`) sa
   nezhodujú s repom. Rozhodnúť pred akýmkoľvek CLI `db push`.
5. `postgres` si ponecháva automatické ADMIN členstvo (PG16+) v rolách `paper_*`
   bez SET/INHERIT. A2 ho nemení.

## Záznam zmien

| Dátum | Zmena | Stav |
| --- | --- | --- |
| 30. 10. 2026 | Návrh A2: fallback A1.4 po neúspešnej AT51 na spravovanom projekte. Privátna funkcia, nová migrácia, AT52–AT58, emulácia 141/141. | NÁVRH, čaká na Romanovo schválenie. Nič neaplikované do spravovanej DB, cron nezapnutý. |
| 30. 10. 2026 | CI run 41104771895 na `f3f6055`: všetkých 5 jobov úspešných, `paper-a2-supabase` 39/39. AT58 rozdelená na AT58a (inštalácia na spravovanej platforme) a AT58b (upgrade, neoverené bez vykonaného upgrade). | NÁVRH; čaká na schválenie znenia. |
| 30. 10. 2026 | Prvé CI so skutočným pg_cron: 36/38. Dva prípady AT57 zlyhali, lebo `postgres` dedí SELECT/USAGE z `pg_read_all_data` (potvrdené aj na spravovanom projekte). Test degradácie odoberá aj toto členstvo. Pribudla kontrola nezávislosti od `pg_read_all_data`. | Oprava testu, migrácia A2 bez zmeny. |
| 30. 10. 2026 | Oprava testov po review: jediný job `paper_tick`/`paper_cron_owner` (unikátny index), job inej databázy cez vrátenú transakciu, deaktivovaný a nečinný skutočný job počas syntetických testov, BYPASSRLS nastavený pred prvým testom, opakovaná aplikácia cez `DB_URL` ako `postgres`. Doplnený job `paper-a2-supabase`. | NÁVRH; overenie v draft PR. Migrácia A2 bez zmeny. |
