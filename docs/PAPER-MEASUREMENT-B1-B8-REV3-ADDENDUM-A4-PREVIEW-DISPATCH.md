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
- `paper_dispatch.trial_ticks(test_ids, from, to)` a
  `paper_dispatch.trial_verdict(test_ids, from, to)`: SECURITY INVOKER,
  `search_path = ''`, EXECUTE iba `postgres` (vlastník). Vyhodnotenie krátkej
  skúšky podľa §A4.1.2.

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

### A4.1.2 Vyhodnotenie krátkej skúšky (PASS/FAIL)

Doručenie nie je dokončenie. Claim dokazuje iba to, že worker začal; worker môže
po claime spadnúť alebo spracovať len niektoré testy. Vyhodnotenie preto beží
pre **každý pár (shadow test, tick)** v okne skúšky `[from, to)` a rozhodujú iba
DB dôkazy. Stav pg_net (`net._http_response`, `net.http_request_queue`, oba
zapisovateľné pre PUBLIC) je iba informatívny a nikdy nedáva PASS.

| Stav páru | Podmienka | PASS? |
| --- | --- | --- |
| `run_valid` | platný run uložený pre tento tick | áno |
| `completed_no_run` | outcome `no_new_candle`, `lease_held`, `evaluation_pending`, `boundary_race` | áno |
| `run_invalid` | run uložený so statusom `invalid` | nie |
| `test_ended` | outcome `test_closed` / `shadow_ended` | nie |
| `lease_expired`, `worker_error`, `inconsistent` | príslušný outcome | nie |
| `claimed_unfinished` | claim bez runu a bez outcome, lease vypršal (pád po claime) | nie |
| `in_progress` | claim bez runu, lease ešte platí | nie (nekonečné) |
| `pending` | bez dôkazu, tick mladší ako 120 s | nie (nekonečné) |
| `undelivered` | bez dôkazu, tick starší ako 120 s | nie |
| `not_dispatched` | tick bez záznamu v `paper_dispatch.sent` | nie |

`trial_verdict` vráti `PASS` iba ak nemá žiadny dôvod. Dôvody:

- `no_tests`, `unknown_test`, `not_shadow`, `invalid_window`;
- `test_not_running_whole_window`: test vznikol po `from`, skončil pred `to`
  alebo `shadow_until_ms < to`. Rozhoduje `created_at`/`completed_at` testu,
  **nie** jeho aktuálny `status`, takže výsledok platí aj po zastavení testu;
- `no_ticks` (prázdne okno), `missing_ticks` (medzera > 90 s od `from`,
  medzi tickmi alebo do `to`);
- názov každého neúspešného stavu páru (tabuľka vyššie);
- `no_valid_run`: niektorý test nemá v okne ani jeden platný run (okno má
  pokryť aspoň jednu 5-minútovú hranicu, odporúčané ≥ 10 min);
- `pg_net_conflict`: pg_net hlási 2xx pri neúspešnom páre;
- `requests_in_flight`: požiadavka z okna je ešte vo fronte pg_net;
- `not_final`: teraz < `final_at` = max(`to` + 120 s, tick + 120 s, koniec
  každého lease).

Konečnosť: tick starší ako 120 s už nemôže dostať claim ani outcome bez claimu
(P2 `paper_claim_run`, P3 `paper_worker_outcome`). Po vypršaní lease už nevznikne
run. K claimnutému ticku môže worker neskôr dopísať iba `error`, čo stav
`claimed_unfinished` mení na `worker_error`, teda FAIL zostáva FAIL.

**Zastavenie cronu (`3_pause` + `3b_wait_idle`) nie je dôkaz dokončenia.**
Odoslané HTTP požiadavky a workery môžu bežať ďalej. Postup po skúške:

1. `3_pause.sql`, `3b_wait_idle.sql` (žiadny nový tick, cron nečinný);
2. počkať do `final_at` (`trial_verdict` ho vráti; aspoň `to` + 120 s
   a do konca všetkých lease, najviac ~5 min);
3. spustiť `a4/2_verify_dispatch.sql` s ID shadow testu a oknom skúšky.
   Skript vypíše neúspešné páry a pri FAIL **skončí chybou** (aj pri prázdnom
   okne, nevyplnených hodnotách alebo inej role ako `postgres`). Iba PASS je
   úspešná skúška.

Príkaz jobu (bez tajných hodnôt):
`select paper_dispatch.send_tick(public.paper_tick_start())`. Job vytvorí
`a4/1_schedule_dispatch.sql` (zhodný s A3 `1_schedule.sql` až na príkaz).
Zastavenie a overenie bez zmeny z A3 (`3_pause.sql`, `3b_wait_idle.sql`,
`2_verify.sql`, `2b_verify_roles.sql`).

| Súbor | SHA-256 |
| --- | --- |
| `ci/paper-supabase/a4/a4_dispatch.sql` (rev2) | `56196b4e94ddd35ca133c1abb915385f9cd999b69eaea8563fec8117899614ec` |
| `ci/paper-supabase/a4/1_schedule_dispatch.sql` | `09bbcbcecc39a6183e7ebe7ee21a3a5abcfa68a00ffdb0129a371d872d5bbd02` |
| `ci/paper-supabase/a4/2_verify_dispatch.sql` | `46bab1dfedc9bdeee2675d3efbbe8f225c2dd12a9959e428afe290c9cfa5f79c` |

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
     `trial_verdict()` takú stratu ukáže ako `undelivered` a skúška je FAIL;
   - sfalšovaná odpoveď v `net._http_response` (napr. 200 `processed`) nezmení
     výsledok, iba pridá `pg_net_conflict` (AT76);
   - zmazanie riadku z fronty pg_net môže skryť `requests_in_flight`, ale nie
     `not_final`: konečnosť sa počíta z ticku a lease, nie z fronty;
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
| AT73 | **Okolie** hranice okna cez reálne HTTP (nie presná hranica): tick 117 s spracovaný; 121 s, z budúcnosti a neexistujúci → `invalid_tick` bez zápisu; ten istý token po prekročení 120 s → `invalid_tick`. Bearer a cudzie tokeny → 401 bez zápisu. | P3 CI |
| AT73b | **Presná** hranica so zmrazeným `paper_private.now_ms()` (ROLLBACK, bez HTTP latencie): `paper_worker_page` 0 a 119 999 ms `ready`, 120 000 ms a −1 ms `invalid_tick`; `paper_worker_outcome` 119 999 zapíše, 120 000 `invalid_tick`; `paper_claim_run` 119 999 prejde kontrolou veku, 120 000 `invalid_tick`. | A2 CI |
| AT75 | Úplné okno (8 tickov, 1 platný run, ostatné `no_new_candle`) → PASS; rovnaký výsledok aj keď je test už zastavený (`completed_at` po okne). | A2 CI |
| AT76 | FAIL s presným dôvodom: pád po claime (`claimed_unfinished`), platný lease (`in_progress`, `not_final`), chyba workera, iba neplatný run (`run_invalid`, `no_valid_run`), sfalšovaná 200 pri nedoručenom ticku (`undelivered`, `pg_net_conflict`), chýbajúci tick, neodoslaný tick, požiadavka vo fronte, test zastavený v okne, test vytvorený po začiatku okna, čiastočné spracovanie dvoch testov (`undelivered`, 15/16), nulový počet tickov (`no_ticks`), prázdny zoznam / neznámy test / obrátené okno, čerstvé okno (`not_final`). Overovací skript zlyhá pri nevyplnených hodnotách aj pri prázdnom okne. | A2 CI |
| AT70 | Skutočné doručenie na Preview cez vlastnú doménu: odpoveď 200 `processed`, žiadna 401/403, `2_verify_dispatch.sql` skončí PASS (po `final_at`). | Iba krátka skúška na testovacom projekte, po schválení nasadenia |

Idempotenciu a vek ticku na strane DB pokrývajú aj P2 (`tick_already_used`,
AT43d, AT44) a P3 (AT8). Lokálna emulácia PG16 s pgcrypto (náhrady za Vault,
pg_net a P2 tabuľky) potvrdila pevný vektor cez `send_tick`, hranice 9/11 s,
budúci tick, ACL, `trial_verdict()` v 14 scenároch a zlyhanie overovacieho
skriptu; nenahrádza CI so skutočným Supabase.

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
| 31. 10. 2026 | Príčina zlyhania `paper-a2-supabase` na `e616b94`: test čítal `->>` z `bytea` (`net.http_request_queue.body`). Hypotéza chýbajúceho pgcrypto vyvrátená: `a4_dispatch.sql` vyžaduje `extensions.hmac`, fixture ho aplikoval a AT67 v tom istom behu prešiel (51/52, jediné zlyhanie AT68). Doplnené §A4.1.1 (presná špecifikácia HMAC, pevný vektor), report doručenia, `2_verify_dispatch.sql`, AT72–AT74. | NÁVRH, čaká na schválenie. |
| 31. 10. 2026 | CI na `35a47c5` (kód zhodný s výsledným HEAD, ďalší commit mení iba tento záznam): 5/5 jobov zelených; A2 56/56 (vrátane pevného vektora, hranice 9 s, budúceho ticku, AT74), P3 17/17 (vrátane AT72, AT73), backend 319 (8 skipped). P3 job má odteraz anotácie. Overenie cez Preview (AT70) čaká na samostatné schválenie nasadenia. | Overené CI, NÁVRH čaká na schválenie. |
| 31. 10. 2026 | Review `f395fa6`: report doručenia považoval claim za doručenie, závisel od aktuálne bežiaceho testu a overovací skript nemal PASS/FAIL. Nahradený `trial_ticks`/`trial_verdict` (§A4.1.2): stav pre každý pár (test, tick), okno viazané na `created_at`/`completed_at` testu, explicitný PASS/FAIL, konečnosť z ticku a lease; skript pri FAIL skončí chybou. AT74 nahradený AT75/AT76, pridaný AT73b (presná hranica), AT73 označený ako test okolia hranice. Postup po skúške: zastavenie cronu nie je dôkaz dokončenia. | NÁVRH, čaká na schválenie. |
