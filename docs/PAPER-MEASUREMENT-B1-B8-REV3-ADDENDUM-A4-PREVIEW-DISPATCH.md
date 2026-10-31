# B1–B8 rev3 — dodatok A4: odoslanie ticku z testovacej DB na izolovaný Preview

**rev2 — NÁVRH, čaká na Romanovo schválenie (31. 10. 2026).** rev1 je
BLOKOVANÁ a nesmie sa aplikovať (§A4.5). rev2 neposiela cez frontu pg_net
žiadnu statickú tajnú hodnotu ani Vercel bypass.

Platí iba pre testovací projekt `nofomo-paper-validation` a Preview vetvu
`claude/paper-p4-preview` ([postup Preview](PAPER-MEASUREMENT-P4-PREVIEW-SETUP.md)).
Nadväzuje na A2 (overenie pôvodu) a A3 (vytvorenie a zastavenie jobu).
Nemení FINAL rev3, A1–A3 ani migrácie P2/P3/A2. Produkcia je mimo rozsahu.

## A4.0 Zistenia na testovacom projekte (30. 10. 2026, iba čítanie)

- Vault 0.3.1: `postgres` číta `vault.decrypted_secrets`, `paper_cron_owner` nie.
- pg_net 0.20.4: USAGE na `net` a EXECUTE na `net.http_post` majú `anon`,
  `authenticated`, `service_role`, `authenticator` aj obe roly `paper_*`.
- **`net.http_request_queue` (stĺpec `headers`) a `net._http_response` majú
  všetky práva pre PUBLIC.** Vlastník je `supabase_admin`, takže `postgres`
  ich neodoberie (rovnako ako pri crone v A2). `pg_net.ttl` je 6 h, frontu
  pg_net vyprázdni hneď po odoslaní.

## A4.1 Mechanizmus (rev2)

Súbor `ci/paper-supabase/a4/a4_dispatch.sql` (spúšťa `postgres`).
**Zámerne nie je v `supabase/migrations`**, aby sa nikdy nedostal do produkcie
cez `db push`. Vyžaduje A2, Vault, pg_net a pgcrypto (`extensions.hmac`).

- schéma `paper_dispatch` (vlastník `postgres`, USAGE iba `paper_cron_owner`);
- `paper_dispatch.target`: jediný riadok, URL pevne daná obmedzením
  `https://p4-preview.haukro.com/api/paper/measure/tick`, predvolene
  `enabled = false`. Subdoména čaká na rozhodnutie (§A4.6); iný názov
  znamená novú verziu súboru a nový hash;
- `paper_dispatch.sent`: každý tick sa odošle najviac raz;
- `paper_dispatch.send_tick(tick_id)`: SECURITY DEFINER, vlastník `postgres`,
  `search_path = ''`, EXECUTE iba `paper_cron_owner`. Odošle iba, ak
  `session_user = paper_cron_owner`, tick vznikol v **tej istej session**
  (`backend_pid = pg_backend_pid()`) najviac pred 10 s, ešte nebol odoslaný,
  cieľ je zapnutý a `paper_p4_cron_secret` vo Vault má aspoň 32 znakov.
  Inak skončí chybou a do fronty nič nezapíše;
- **jediná hlavička s autentifikáciou:**
  `Authorization: PaperTick v1.<tick_id>.<hex HMAC-SHA256(CRON_SECRET, 'paper-tick:v1:' || tick_id)>`.
  `CRON_SECRET` DB neopustí. Telo je `{"tick_id": "<tick_id>"}`. Žiadny
  `x-vercel-protection-bypass`;
- `paper_dispatch.delivery_report()`: SECURITY INVOKER, `search_path = ''`,
  EXECUTE iba `postgres` (vlastník). Pre každý odoslaný tick vráti `delivery`
  podľa §A4.1.2 a príznak `pg_net_conflict`.

### A4.1.1 Token (zhodne v DB a v endpointe)

| Časť | Hodnota |
| --- | --- |
| Kľúč | `CRON_SECRET` ako UTF-8 (DB: Vault `paper_p4_cron_secret`, endpoint: premenná Preview vetvy) |
| Správa | UTF-8 reťazec `paper-tick:v1:` + `tick_id` v kanonickom tvare UUID (malé písmená, so spojovníkmi) |
| Algoritmus | HMAC-SHA256, výstup hex malými písmenami (64 znakov) |
| DB | `encode(extensions.hmac(convert_to(správa,'UTF8'), convert_to(kľúč,'UTF8'), 'sha256'), 'hex')` |
| Endpoint | `hmac.new(kľúč, správa, sha256).hexdigest()` v `tick_token()`, porovnanie `secrets.compare_digest` s tokenom pre `tick_id` z tela |
| Pevný vektor | kľúč `'k' * 40`, tick `00000000-0000-0000-0000-000000000001` → `8ec3a7b53733daee250202a8cac55df6792ac9c468954e2e39ed7bcd6343f324` |

Token nemá vlastnú expiráciu. Platnosť určuje vek ticku v DB (0 až 119 999 ms,
P2 `paper_claim_run` a P3 `paper_worker_page`) a idempotencia (`unique (test_id, tick_id)`).

### A4.1.2 Doručenie

pg_net zapisuje výsledok do `net._http_response`, ktorú môže meniť PUBLIC.
Stav pg_net preto o doručení **nerozhoduje**. Rozhoduje iba DB dôkaz, že
endpoint tick spracoval (riadok v `paper_run_claims` alebo `paper_tick_outcomes`):

| `delivery` | Podmienka |
| --- | --- |
| `delivered` | aspoň jeden claim alebo outcome pre tick |
| `pending` | bez dôkazu, tick mladší ako 120 s |
| `unverifiable` | bez dôkazu a žiadny bežiaci test (worker by nič nezapísal) |
| `undelivered` | bez dôkazu, tick starší ako 120 s, beží aspoň jeden test |

`pg_net_conflict = true`, ak pg_net hlási 2xx a DB dôkaz chýba (sfalšovaná
alebo klamlivá odpoveď). Krátka skúška preto vyžaduje aspoň jeden bežiaci
shadow test. Kontrolu robí `a4/2_verify_dispatch.sql` (iba čítanie, `postgres`).

Endpoint `POST /api/paper/measure/tick` (`backend/app/paper_routes.py`):

- **Preview** (`VERCEL_ENV=preview`, vetva `claude/paper-p4-preview`, URL
  testovacieho projektu): prijme iba token, ktorý sa zhoduje s tokenom
  vypočítaným pre `tick_id` z tela (`compare_digest`). `Bearer CRON_SECRET`,
  token pre iný tick, iný secret alebo zmenený podpis → 401, worker sa nevolá.
- **CI** (`GITHUB_ACTIONS`, lokálny Supabase): iba `Bearer CRON_SECRET`, tick token → 401.
- **Diagnostika** (`/diagnostic/run`): bez zmeny, iba `Bearer PAPER_DIAGNOSTIC_SECRET`.
- Platnosť ticku ďalej stráži DB (P2 `paper_claim_run`): tick musí existovať,
  mať `source = 'pg_cron'`, byť mladší ako 120 s; opakovanie → `tick_already_used`.

Príkaz jobu (bez tajných hodnôt):
`select paper_dispatch.send_tick(public.paper_tick_start())`. Job vytvorí
`a4/1_schedule_dispatch.sql` (zhodný s A3 `1_schedule.sql` až na príkaz).
Zastavenie a overenie bez zmeny z A3 (`3_pause.sql`, `3b_wait_idle.sql`,
`2_verify.sql`, `2b_verify_roles.sql`).

| Súbor | SHA-256 |
| --- | --- |
| `ci/paper-supabase/a4/a4_dispatch.sql` (rev2) | `bf0a0b004e9780738bccddb26c8c1addb90c438f32240339364ef1eb9ebb27b0` |
| `ci/paper-supabase/a4/1_schedule_dispatch.sql` | `09bbcbcecc39a6183e7ebe7ee21a3a5abcfa68a00ffdb0129a371d872d5bbd02` |
| `ci/paper-supabase/a4/2_verify_dispatch.sql` | `9e01871df83ab7484f9c3de132414ee11214a6ef0786c925053838aafd54e730` |

## A4.2 Tajné hodnoty

Iba jedna, `CRON_SECRET` vetvy `claude/paper-p4-preview` (Vercel, scope Preview
+ táto vetva). Roman ju vloží v SQL editore testovacieho projektu, nie cez chat,
logy ani PR:

```sql
select vault.create_secret('<CRON_SECRET z Vercel Preview vetvy>', 'paper_p4_cron_secret', 'P4 preview tick');
```

Secret `paper_p4_vercel_bypass` z rev1 sa **nevytvára**. Celoprojektový
Protection Bypass for Automation sa nevytvára.

## A4.3 Zvyškové riziká (iba testovací projekt)

1. **Fronta pg_net je pre aplikačné roly zapisovateľná** (§A4.0, §A4.5).
   Kto vie spustiť SQL nad `net`, môže pred odoslaním zmeniť `url` alebo telo.
   - zmenené telo (iný `tick_id`) → token nesedí → 401;
   - presmerovaná `url` → útočník získa token jedného ticku. Ten platí iba
     pre ten tick a do 120 s od jeho vzniku. Použitie má rovnaký účinok ako
     legitímne doručenie (idempotentné, `tick_already_used`). Skutočná škoda
     je strata jedného doručenia (medzera v meraní), nie únik secretu.
     `delivery_report()` takú stratu ukáže ako `undelivered`;
   - sfalšovaná odpoveď v `net._http_response` (napr. 200 `processed`) nezmení
     výsledok, iba nastaví `pg_net_conflict` (AT74);
   - vložená vlastná požiadavka → bez `CRON_SECRET` nevie vyrobiť platný token.
   PostgREST schému `net` nevystavuje, prakticky ide o `postgres`/`supabase_admin`.
2. **Vlastná doména sprístupní deployment vetvy bez SSO.** Overené 31. 10.
   (iba názvy a scope, bez hodnôt): všetky produkčné premenné majú scope
   `production`, premenné pre všetky Preview neexistujú. Vetva má iba
   `PAPER_MEASUREMENT_ENABLED`, `PAPER_SUPABASE_URL`, `CRON_SECRET`,
   `PAPER_DIAGNOSTIC_SECRET` (+ `PAPER_SUPABASE_SERVICE_ROLE_KEY`, doplní Roman).
   Verejne viditeľný je teda kód frontendu vetvy, nie produkčné dáta.
   Podmienka: do Preview scope nepridávať žiadne produkčné premenné.
3. Únik `CRON_SECRET` (napr. z Vercelu) by umožnil vyrobiť tokeny, dopad je
   rovnaký ako v bode 1 (iba existujúce ticky do 120 s, idempotentne).
4. Po P4: rotovať `CRON_SECRET` (Vercel aj Vault), `paper_dispatch.target`
   vypnúť, vlastnú doménu odpojiť.
5. Pre produkciu tento model nie je schválený.

## A4.4 Testy

| ID | Scenár a očakávaný výsledok | Zaradenie |
| --- | --- | --- |
| AT67 | Definícia a ACL `send_tick`, pevná URL (iná → `target_url_check`), aplikačné roly a `paper_rpc_owner` bez prístupu, `paper_cron_owner` nečíta `target`, `sent` ani Vault. | A2 CI |
| AT68 | Pozitívny prípad v transakcii s ROLLBACK: POST iba na pevnú URL, bez bypass hlavičky, `CRON_SECRET` sa v hlavičkách nevyskytuje, `Authorization` sa rovná tokenu z Pythonu. Pevný vektor (§A4.1.1) cez skutočnú `send_tick` aj v Python unit teste. Po ROLLBACK fronta bez zmeny. | A2 CI, backend CI |
| AT69 | Odmietnutia bez zápisu do fronty: session `postgres`, tick z iného PID, tick starší ako 10 s (11 s), tick z budúcnosti, neznámy tick, opakované odoslanie, vypnutý cieľ, chýbajúci alebo krátky secret. Hranica: tick starý 9 s sa odošle. Opakovaná aplikácia A4 odmietnutá. | A2 CI |
| AT71 | Endpoint v Preview: prijme iba token pre tick z tela; 401 pre token iného ticku, iný secret, diagnostický secret, `Bearer CRON_SECRET`, prázdny a zmenený podpis. Diagnostika iba s vlastným secretom. V CI tick token → 401. | backend CI |
| AT72 | Súbežné doručenie toho istého tokenu dvakrát cez reálny HTTP worker v režime Preview tokenu: obe 200, presne 1 claim pre (test, tick), 1 run. Opakovanie zachyteného tokenu v okne: bez nových zápisov. | P3 CI |
| AT73 | Hranice okna s platným tokenom: tick 117 s spracovaný; 121 s, z budúcnosti a neexistujúci → `invalid_tick` bez zápisu; ten istý token po prekročení 120 s → `invalid_tick` bez zápisu. Bearer a cudzie tokeny → 401 bez zápisu. | P3 CI |
| AT74 | Uložený a odoslaný tick bez DB dôkazu je `undelivered` aj pri sfalšovanej odpovedi 200 od `authenticated`/`anon` (`pg_net_conflict`); doručený tick ostane `delivered` aj pri sfalšovanej 401; mladý tick `pending`; bez bežiaceho testu `unverifiable`. | A2 CI |
| AT70 | Skutočné doručenie na Preview cez vlastnú doménu: odpoveď 200 `processed`, žiadna 401/403, `2_verify_dispatch.sql` bez `undelivered` a bez `pg_net_conflict`. | Iba krátka skúška na testovacom projekte, po schválení nasadenia |

Idempotenciu a vek ticku na strane DB pokrývajú aj P2 (`tick_already_used`,
AT43d, AT44) a P3 (AT8). Lokálna emulácia PG16 s pgcrypto (náhrady za Vault,
pg_net a P2 tabuľky) potvrdila pevný vektor cez `send_tick`, hranice 9/11 s,
budúci tick, ACL a `delivery_report()`; nenahrádza CI so skutočným Supabase.

## A4.5 Blokujúci nález (review 30. 10. 2026)

Na testovacom projekte majú `anon`, `authenticated`, `service_role` aj
`paper_cron_owner` na `net.http_request_queue` okrem SELECT aj **UPDATE
a INSERT**. Kým pg_net požiadavku neodošle, dá sa zmeniť jej `url` a statické
hlavičky (`CRON_SECRET`, celoprojektový bypass) odísť na cudzí server. Pevná URL
v `send_tick` chráni iba v okamihu zápisu do fronty a Vault nechráni hodnotu
po vložení do hlavičky. Bod 1 v §A4.3 riziko podhodnotil (opisoval iba čítanie).
Celoprojektový bypass preto nebol vytvorený a A4 rev1 sa nenasadzuje.

Smer opravy (rev2, samostatný návrh): do fronty nepôjde žiadna statická tajná
hodnota. Iba token viazaný na jeden tick (HMAC) a Preview bez SSO bypassu.

## A4.6 Rozhodnutia pre Romana

1. Schváliť rev2 (§A4.1–A4.4) na aplikáciu **iba** na `nofomo-paper-validation`.
2. Názov subdomény. Návrh `p4-preview.haukro.com` (DNS u Českého hostingu,
   CNAME na Vercel). Iný názov → upravím `a4_dispatch.sql`, testy a hash.
3. Ručné kroky (hodnoty nikdy do chatu): `PAPER_SUPABASE_SERVICE_ROLE_KEY`
   v Preview vetvy, DNS záznam a priradenie domény k vetve, `vault.create_secret`
   podľa §A4.2.

## Záznam zmien

| Dátum | Zmena | Stav |
| --- | --- | --- |
| 30. 10. 2026 | Návrh A4 (rev1). CI 52/52 (`414f9cb`). | — |
| 30. 10. 2026 | Review: UPDATE/INSERT na frontu pg_net pre aplikačné roly (§A4.5). | **BLOKOVANÉ**, neaplikované; pripravuje sa rev2. |
| 31. 10. 2026 | Rozpracovaná rev2 v kóde: `a4_dispatch.sql` posiela iba `PaperTick v1.<tick_id>.<HMAC-SHA256>`, bez bypassu, cieľ `https://p4-preview.haukro.com/api/paper/measure/tick` (subdoména čaká na Romanovo rozhodnutie). `paper_routes.py`: v Preview tick iba s tokenom viazaným na telo, Bearer iba CI. Text §A4.1–A4.4 ešte opisuje rev1. | ROZPRACOVANÉ, CI zatiaľ neoverené. |
| 31. 10. 2026 | CI na `e616b94`: 1 zlyhanie v A4 teste (`body` vo fronte pg_net je `bytea`) a backend job. Oprava v `8b43a2f` (test číta `convert_from(body)::jsonb`, backend test s explicitnou odpoveďou diagnostiky, anotácie backend jobu); `a4_dispatch.sql` bez zmeny. CI `8b43a2f`: 5/5 jobov zelených, A2 52/52, backend 318 (7 skipped). | Overené CI. |
| 31. 10. 2026 | Text §A4.1–A4.4 prepísaný na rev2, pridané §A4.6 a AT71. | NÁVRH, čaká na schválenie. |
| 31. 10. 2026 | Príčina zlyhania `paper-a2-supabase` na `e616b94`: test čítal `->>` z `bytea` (`net.http_request_queue.body`). Hypotéza chýbajúceho pgcrypto vyvrátená: `a4_dispatch.sql` vyžaduje `extensions.hmac`, fixture ho aplikoval a AT67 v tom istom behu prešiel (51/52, jediné zlyhanie AT68). Doplnené §A4.1.1 (presná špecifikácia HMAC, pevný vektor), §A4.1.2 a `delivery_report()`, `2_verify_dispatch.sql`, AT72–AT74. | NÁVRH, čaká na schválenie. |
