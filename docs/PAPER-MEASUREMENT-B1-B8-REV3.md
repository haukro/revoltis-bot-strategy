# Meranie paper testov: implementačná špecifikácia B1–B8, rev3

Oct 27, 2026 · @Haukro

Revízia 3 po druhom review (body V1 až V6 a chýbajúce testy), doplnená po treťom review (jednotný čas claim a spresnenia). Obsahuje aj rev2: nálezy N1 až N8 a rozhodnutia R1 až R5, ktoré sa neotvárajú. **FINAL — schválené Romanom 28. 10. 2026.** Neschvaľuje implementáciu, migráciu, deploy, nový beh ani zápis do DB. Vychádza z kódu na `main` `b200950`, z protokolu HYPE v8 rev13 a z [rev1 tejto špecifikácie](https://claude.ai/artifact/1iyF7MTJmyKadb7KVF77nq). Metodika variantu B sa nemení. Rev13 sa nemení.

## 0. Zmeny oproti rev1

| Nález | Riešenie v rev2 | Sekcia | Testy |
| --- | --- | --- | --- |
| N1 význam `as_of` | Variant B zachovaný: `as_of = test_end`. Posledná použitá sviečka je samostatné pole. M1 zamietnutá (R1). | 2, 3, 5 | AT19 |
| N2 beh bez pásky vypadne z výberu | Trojstavová oprávnenosť po časovej bráne. Neoveriteľný beh je kandidát s verdiktom INVALID. | 5.3 | AT20 |
| N3 zámok a chronológia | Lease rezervácia, najviac jedna aktívna na test. `seq` sa prideľuje v transakcii zápisu pod zámkom riadku testu. Pád a neskorý zápis sú definované. | 4.3 | AT21–AT23 |
| N4 ručný beh blokuje automatický, pôvod | Ručné behy v samostatnej tabuľke. Automatický pôvod dokazuje tick zapísaný v DB funkciou spúšťanou `pg_cron`. | 2, 4.2 | AT24, AT25 |
| N5 dodatočné dopĺňanie pásky | Beh je uzavretý transakciou, ktorá ho vytvorila. Neskorší INSERT obchodu alebo pozície sa odmietne. | 2 | AT26 |
| N6 Vercel Cron nevyhovuje | `pg_cron` + `pg_net`, kontrola každú minútu, beh iba pri novej uzavretej 5m sviečke (R2). | 4.1 | AT27 |
| N7 dokončenie a opakované hodnotenie | Jeden autoritatívny verdikt na test. Scale končí `DIAGNOSTIC` (R5). Verdikt a prechod stavu v jednom RPC, idempotentne po páde. | 4.5, 5.7 | AT28, AT29 |
| N8 P6 vs. pravidlá testovania | Úplná sada AT iba v izolovanom prostredí. Produkčná kontrola má vlastný rozsah S1 až S3. | 7, 8 | – |
| Doplnky | `gate_ms`, zarovnanie sviečok a warmupu, vek knihy (R3), platný uzavretý obchod, PF bez strát (R4). | 2, 3, 5 | AT30–AT32 |

### 0b. Zmeny v rev3

| Bod review | Riešenie v rev3 | Sekcia | Testy |
| --- | --- | --- | --- |
| V1 hranica sviečky | Hranica sa počíta z `open_time`: B = `last_candle_open_ms` + 300 000. Nezávisí od konvencie `close_time` (v referenčnom kóde `open_time` + 300 000 − 1). Surový `close_time` sa ukladá osobitne. | 2, 3 | AT34 |
| V2 expirácia lease | Všetky časové kontroly v RPC po získaní zámku používajú `clock_timestamp()`, nie `now()`. | 4.3 | AT36 |
| V3 zápis po dokončení | Persist pod zámkom znova overí `status = running` a neprítomnosť autoritatívneho verdiktu. Claim vyžaduje, aby každý uložený beh po bráne bol posúdený. | 4.3, 4.5 | AT37, AT43 |
| V4 automatický pôvod | `paper_tick_start()` smie spustiť iba rola cron jobu. Väzba ticku na záznam `cron.job_run_details` je povinná a overuje sa pri posúdení každého behu po bráne. | 4.2 | AT38 |
| V5 nemenný `test_end` | `test_end_ms` vzniká v claim RPC a je uložený v `paper_run_claims`. Persist ho preberá z claim, worker ho neposiela. | 2, 3, 4.3 | AT35 |
| V6 ukončenie shadow | `shadow_until_ms` a stav `stopped`. Po limite claim nevznikne a stop RPC ukončí test bez autoritatívneho verdiktu. | 2, 4.5, 8 | AT41 |
| Drobné | Zátvorky v podmienke PASS, AT33 v P1, výnimka v AT2, rozdelenie AT21, rozlíšenie výpadku ticku, pádu a neoveriteľného behu, `pf_infinite` s DD > 15 %. | 5, 7, 8 | AT21, AT39, AT40, AT42 |
| Predbežný tretí review | Assess vynucuje poradie seq (assessment\_out\_of\_order). Oneskorený dôkaz cron: origin\_pending s lehotou origin\_grace\_ms, potom tick\_origin\_unverified. Persist shadow po limite vracia shadow\_ended aj pred stop RPC. | 4.2, 4.3, 5.7 | AT44–AT46 |
| Tretí review | Blokujúce: jediný čas t\_claim po zámku určuje test\_end\_ms, hranicu, vek ticku, shadow limit aj lease\_expires\_at; pri INSERT sa čas znovu neodoberá. Spresnenia: pôvod ticku sa overuje pre každý beh po bráne, bez neho nie je proven\_ineligible; idempotentný assess; stavy cron záznamu starting/running/succeeded/failed; finalize premenované na assess; close\_time\_raw\_ms v candle\_archive; AT35 a AT44–AT46 priradené do fáz. | 2, 3, 4.2, 4.3, 5.3, 5.7, 8 | AT28, AT34, AT38, AT41, AT43 |
| Cielená kontrola | origin\_grace\_ms potvrdené. Výnimka origin\_pending premietnutá do 5.3 a 5.7: kontrola pôvodu pred klasifikáciou, bez zápisu. origin\_grace\_ms je nemenné nastavenie testu, lehota od pôvodného tick.created\_at, retry ju neobnovuje, failed ukončí lehotu, neskorý dôkaz nemení vydaný verdikt. | 2, 4.2, 5.3, 5.7 | AT38 (e1, e2), AT46 (c–f) |

## 1. Rozsah a zásady

Cieľ: každý budúci paper test musí mať pre každý beh preukázateľnú vlastnú pásku, vstupné sviečky, náklady, konfiguráciu, verziu výpočtu, automatický pôvod a chronológiu uloženia. Hodnotenie podľa variantu B musí byť vykonateľné iba z uložených podkladov.

Zásady:

- **Nová, oddelená cesta merania.** Paper testy dostanú vlastné tabuľky, endpointy a scheduler. Legacy `/api/simulations/run`, `simulated_trades` a `simulation_runs` zostávajú pre historické testy a optimizer bez zmeny správania.
- **Stratégia sa nemení.** Funkcia `simulate()` v `simulation.py` zostáva logicky rovnaká. Mení sa iba príprava vstupov a ukladanie výstupov.
- **Variant B zostáva metodikou.** `as_of = test_end`. Náklady `current_book_at_as_of` pre každý beh. Verdikt z obchodov s S ≤ closed\_at ≤ T\*. DD zo `summary.max_drawdown_percent` za \[S, as\_of\]. Iná časová alebo nákladová konvencia je zmena metodiky a patrí do protokolu budúceho testu.
- **Iba vkladanie.** Uložený beh, jeho páska, náklady a sviečky sa nikdy neprepisujú, nemažú ani nedopĺňajú.
- **Chýbajúci dôkaz znamená INVALID** s pomenovaným dôvodom, nikdy tichý náhradný postup. Chýbajúca páska nie je nula obchodov.

Mimo rozsahu a bez zmeny: ledger a tabuľky SPEC-006, migrácie 001–029, ZEC TSMOM B, PR66/67/69, optimizer, stratégia HYPE v8, real trading.

## 2. Dátový model (B1, B2, B3, B4, B6)

Nová migrácia s číslom nasledujúcim po najvyššej aplikovanej (číslo potvrdiť read-only pred zápisom). Všetky tabuľky majú RLS a zápis iba cez serverové RPC (`SECURITY DEFINER`, granty iba pre tieto funkcie). Trigger odmietne UPDATE a DELETE na všetkých tabuľkách (vzor `025_spec006_immutable_rows.sql`). Jedinou výnimkou je prechod stavu `paper_tests` cez assess RPC alebo stop RPC (sekcia 4.5).

| Tabuľka | Účel | Kľúčové stĺpce | Pravidlá |
| --- | --- | --- | --- |
| `paper_tests` | Identita testu | `id`, `label` (official / scale), `pair`, `timeframe`, `variant`, `strategy_version_id`, `settings` (jsonb vrátane prahov a `warmup_bars`), `settings_sha256`, `start_ms` (S), `gate_ms` (celé číslo, 2 592 000 000), `gate_trades` (30), `max_book_age_ms` (60 000), `lease_ms`, `origin_grace_ms` (nemenné, návrh 600 000), `shadow` (bool), `shadow_until_ms` (povinné pri shadow, inak null), `created_at`, `status` (running / completed / invalid / stopped), `authoritative_verdict_id`, `completed_at` | Unikátne (`label`, `start_ms`). Kontrola `gate_ms = 2 592 000 000` pre tento protokol. Prechody iba running → completed / invalid (assess RPC) a running → stopped (stop RPC, iba shadow). Stav `stopped` nemá autoritatívny verdikt. |
| `paper_measure_ticks` | Dôkaz automatického spustenia | `tick_id`, `source` (iba `pg_cron`), `cron_job_name`, `created_at` (čas DB) | Vkladá iba `paper_tick_start()`. Oprávnenia a väzba na `cron.job_run_details` v sekcii 4.2. |
| `paper_tick_outcomes` | Výsledok ticku pre test | `tick_id`, `test_id`, `outcome` (run\_created / no\_new\_candle / boundary\_race / lease\_held / lease\_expired / test\_closed / evaluation\_pending / shadow\_ended / error), `detail`, `lag_ms`, `created_at` | Iba vkladanie. Medzery a chyby sú viditeľné. |
| `paper_run_claims` | Rezervácia výpočtu | `id`, `test_id`, `tick_id`, `last_candle_open_ms`, `boundary_ms` (B = `last_candle_open_ms` + 300 000), `test_end_ms` (čas DB pri claime), `attempt`, `lease_token`, `claimed_at`, `lease_expires_at` | Unikátne (`test_id`, `boundary_ms`, `attempt`) a (`test_id`, `tick_id`). Kontrola `boundary_ms = last_candle_open_ms + 300 000`. `test_end_ms` je nemenný a worker ho nikdy neposiela. Podmienky v sekcii 4.3. |
| `paper_runs` | Jeden automatický beh | `id`, `test_id`, `claim_id`, `tick_id`, `seq`, `test_end_ms` (prevzaté z claim), `as_of_ms` (= `test_end_ms`), `last_candle_open_ms`, `last_candle_close_raw_ms` (surový `close_time` zo zdroja), `boundary_ms`, `persisted_at`, `created_txid`, `code_commit`, `engine_sha256`, `settings_sha256`, `candles_sha256`, `candle_first_open_ms`, `candle_last_open_ms`, `candle_count`, `expected_count`, `cost_profile`, `cost_profile_sha256`, `book_ts_ms`, `cost_fetched_at_ms`, `tape_sha256`, `trade_count`, `open_count`, `summary`, `status` (valid / invalid), `invalid_reason` | Unikátne (`test_id`, `seq`), (`test_id`, `boundary_ms`), (`claim_id`). Kontroly `as_of_ms = test_end_ms` a zhoda `test_end_ms`, `boundary_ms`, `tick_id` s claim. |
| `paper_run_trades` | Páska behu | (`run_id`, `ordinal`) ako PK, `pair`, `opened_at_ms`, `closed_at_ms`, `entry_rate`, `exit_rate`, `stake`, `profit_usdt`, `exit_reason`, `raw` | Uzavretie obsahu behu (nižšie). |
| `paper_run_open_positions` | Otvorené pozície k as\_of | rovnaký kľúč ako páska | Uzavretie obsahu behu (nižšie). |
| `candle_archive` | Archív uzavretých sviečok | (`source`, `pair`, `timeframe`, `open_time_ms`) ako PK, `close_time_raw_ms` (surový `close_time` zo zdroja), OHLCV, `quote_volume`, `confirm` (iba 1), `fetched_at` | Opätovné vloženie s inými hodnotami vráti `candle_conflict`. |
| `paper_diagnostic_runs` | Ručné diagnostické behy | rovnaké stĺpce ako `paper_runs` bez `claim_id`, `tick_id`, `seq`; páska v `paper_diagnostic_trades` | Evaluator ich nikdy nečíta. Neobsadzujú kľúče `paper_runs`. |
| `paper_verdicts` | Výsledok hodnotenia | `id`, `test_id`, `kind` (authoritative / audit), `refers_to_verdict_id`, `candidate_run_id`, `eligibility_trace` (jsonb), `c30_ms`, `t_star_ms`, `trades_count`, `net_pnl`, `expectancy`, `gross_profit`, `gross_loss`, `profit_factor`, `pf_infinite`, `dd_field`, `dd_value`, `verdict` (PASS / FAIL / INVALID / DIAGNOSTIC), `reason`, `evaluator_commit`, `evaluator_sha256`, `created_at` | Čiastočný unikátny index (`test_id`) pre `kind = authoritative`. Audit riadok stav testu nemení. |
| paper\_run\_assessments | Posúdenie behu po bráne | run\_id, test\_id, eligibility (proven\_ineligible / eligible / unverifiable), valid\_trade\_count, tick\_origin\_verified, evaluator\_commit, created\_at | Unikátne (run\_id). Vkladá iba assess RPC. Bez posúdenia uloženého behu po bráne nevznikne ďalšia claim. |

Uzavretie obsahu behu (N5):

- `paper_runs.created_txid = txid_current()` pri vložení behu.
- Trigger BEFORE INSERT na `paper_run_trades` a `paper_run_open_positions` odmietne riadok, ak `run_id` neexistuje alebo jeho `created_txid` sa nerovná `txid_current()`. Chyba `run_sealed`.
- Odložený constraint trigger pri commite overí, že počet riadkov sa rovná `trade_count` a `open_count`. Inak sa zruší celá transakcia.

Poznámky:

- ID obchodu je (`run_id`, `ordinal`), takže rovnaký pár, `close_time` a poradie v rôznych behoch alebo testoch nekolidujú (B1).
- `candle_archive` uchováva obsah vrátane warmupu. Pri 5m a 30 dňoch je to približne 8 800 riadkov na pár, nie na beh (B3).
- Nákladový profil je uložený v behu celý, vrátane `book_ts` z OKX (B4).
- `NOT_EVALUATED` sa neukladá. Je to odvodený stav testu, kým neexistuje kandidát.

## 3. Výpočet behu (B2, B3, B4, B6)

Postup jedného automatického behu, v tomto poradí:

1. **Nová uzavretá sviečka.** Tick načíta z OKX poslednú sviečku s potvrdeným uzavretím (`confirm` = 1). B = jej `open_time` + 300 000. Hranica sa nikdy neodvodzuje z `close_time`, lebo zdroj ho uvádza ako `open_time` + 300 000 − 1. Ak pre test už existuje beh s `boundary_ms = B`, outcome `no_new_candle` a koniec.
2. **Claim (sekcia 4.3).** Claim RPC po získaní zámku odoberie **jediný čas** `t_claim = clock_timestamp()`. Z neho sa určí nemenný `test_end_ms = t_claim` a vyhodnotia všetky časové podmienky claim: hranica floor(`t_claim` / 300 000) · 300 000 = B, vek ticku a shadow limit. Pri INSERT sa čas znovu neodoberá, takže uložená dvojica B a `test_end_ms` vždy spĺňa rovnicu. `as_of_ms = test_end_ms`. Ak hranica nesedí, outcome `boundary_race` a claim nevznikne. Worker dostane `test_end_ms` iba na čítanie a persist ho prevezme z claim.
3. **Sviečky.** Rozsah: `first_open` = floor(S / 300 000) · 300 000 − `warmup_bars` · 300 000, `last_open` = B − 300 000. `expected_count` = (`last_open` − `first_open`) / 300 000 + 1. Berú sa iba sviečky s `confirm` = 1 a `open_time` + 300 000 ≤ as\_of. Nové sa vložia do `candle_archive` vrátane surového `close_time`. Chýbajúca alebo duplicitná sviečka, prípadne `open_time` > `last_open`, znamená beh `invalid` s dôvodom `incomplete_candles`. Beh sa aj tak uloží.
4. **Náklady raz.** Jeden profil `load_pair_cost_model(pair, stake)` pred simuláciou. Uloží sa celý profil, `book_ts_ms` a `cost_fetched_at_ms`. Musí platiť 0 ≤ `cost_fetched_at_ms` − `book_ts_ms` ≤ `max_book_age_ms` (60 000). Chýbajúci čas: `invalid`, `cost_profile_unavailable`. Staršia kniha: `invalid`, `book_too_old`. Kniha z budúcnosti: `invalid`, `book_from_future`. Splnený limit potvrdzuje iba čerstvosť, nie kvalitu nákladového modelu.
5. **Simulovať.** `simulate()` s archivovanými sviečkami, zmrazenými nastaveniami testu, `trading_start_time = S`, `force_close_at_end = False` a jediným profilom.
6. **Hashovať.** Kanonický JSON (zoradené kľúče, pevný formát čísel) pre `settings_sha256`, `candles_sha256`, `cost_profile_sha256` a `tape_sha256`. `engine_sha256` = hash `simulation.py` a `costs.py`. `code_commit` z `VERCEL_GIT_COMMIT_SHA`. Chýbajúci commit: `invalid`, `unknown_code_version`. Nastavenia s iným hashom než test: `invalid`, `settings_mismatch`.
7. **Uložiť cez persist RPC (sekcia 4.3).** Beh, páska a otvorené pozície v jednej transakcii. Čiastočný zápis nesmie zostať.

**Platný uzavretý obchod.** Riadok pásky s `closed_at_ms` nie null, `opened_at_ms ≥ S`, `exit_reason` nie je vynútené zatvorenie na konci dát a `profit_usdt` nie je null.

**Replay.** `replay_run(run_id)` načíta sviečky z archívu, nastavenia a profil z behu, spustí `simulate()` a porovná `tape_sha256` a `summary`. Nezapisuje do DB a nevolá OKX.

**Determinizmus.** Poradie spracovania určuje kľúč udalostí (`close_time`, `pair`, `index`). Hashujú sa uložené hodnoty, nie medzivýsledky. Verzia Pythonu a závislostí sa zaznamená v `raw` behu. Zhoda replayu sa vyžaduje iba pri rovnakom `engine_sha256`.

## 4. Scheduler, pôvod a chronológia (B5)

### 4.1 Spúšťač

`pg_cron` raz za minútu volá DB funkciu `paper_tick_start()`. Funkcia vloží riadok do `paper_measure_ticks` a cez `pg_net` pošle POST na `/api/paper/measure/tick` s `tick_id` a `CRON_SECRET`. Endpoint je idempotentný. Beh vznikne iba pri novej uzavretej 5m sviečke (sekcia 3, krok 1). Oneskorenie `test_end_ms − B` sa zaznamená v `lag_ms`. Je to monitorovaný údaj, nie dôvod neplatnosti. Dostupnosť `pg_cron` a `pg_net` v projekte a na branchi nie je overená.

### 4.2 Pôvod

- `CRON_SECRET` iba autorizuje volanie. Pôvod nedokazuje.
- Automatický beh je platný iba vtedy, ak jeho claim nesie `tick_id`, ktorý existuje v `paper_measure_ticks` so `source = pg_cron`. Tick musí byť pri claime (`t_claim`) mladší než 120 s a pre daný test ešte nepoužitý.
- **Oprávnenie na tick.** `paper_tick_start()` vlastní rola `paper_cron_owner`, pod ktorou beží iba cron job. `EXECUTE` je odobratý pre `PUBLIC`, `anon`, `authenticated` aj `service_role`, ktorú používa HTTP worker. Funkcia odmietne volanie, ak `session_user` nie je `paper_cron_owner`. Skutočné správanie `session_user` v cron jobe sa overí v P4. **Povinná väzba na cron pre každý posudzovaný beh po bráne**, nielen pre kandidáta, teda aj pre beh s menej než 30 obchodmi: assess RPC overí, že v `cron.job_run_details` existuje záznam jobu `paper_tick` so `status = succeeded` a `start_time ≤ tick.created_at ≤ end_time`. Bez overeného pôvodu nemôže byť beh `proven_ineligible`. **Oneskorenie dôkazu:** ak má záznam stav `starting` alebo `running`, prípadne ešte chýba, a platí čas assessu − `tick.created_at` ≤ `origin_grace_ms`, assess vráti `origin_pending`, nič nezapíše a posúdenie sa zopakuje v ďalšom ticku. Nová claim medzitým nevznikne, pretože beh zostáva neposúdený. Lehota sa vždy počíta od pôvodného `tick.created_at` a opakovaný assess ju neobnovuje. `origin_grace_ms` je nemenné nastavenie testu v `paper_tests` (návrh 600 000; primeranosť hodnoty sa overí v P4). Ak lehota uplynie bez záznamu `succeeded`, alebo má záznam konečný stav `failed` (aj počas lehoty), beh je neoveriteľný s dôvodom `tick_origin_unverified`. Záznamy `cron.job_run_details` sa počas testu nemažú.
- Ručné behy idú cez `/api/paper/diagnostic/run` iba do `paper_diagnostic_runs`. Neobsadzujú kľúč automatického behu a nikdy nie sú kandidátom.

### 4.3 Súbeh, poradie a pád

**Claim RPC** v jednej transakcii:

1. `SELECT … FROM paper_tests WHERE id = … FOR UPDATE`.
2. Po zámku odoberie jediný čas `t_claim = clock_timestamp()` a s ním overí: `status = running`, žiadny autoritatívny verdikt, pri shadow `t_claim` < `shadow_until_ms`, platný tick (4.2), podmienku hranice (sekcia 3, krok 2), neexistenciu behu pre (test, B) a to, že každý uložený beh po bráne má posúdenie v `paper_run_assessments`. Inak outcome `test_closed`, `shadow_ended` alebo `evaluation_pending`.
3. Overí, že test nemá inú aktívnu claim (`lease_expires_at > t_claim` a bez behu). Inak outcome `lease_held`.
4. Vloží claim s novým `lease_token`, `test_end_ms = t_claim`, `attempt` = počet predchádzajúcich pokusov pre (test, B) + 1 a `lease_expires_at = t_claim + lease_ms`. Pri INSERT sa `clock_timestamp()` znovu nevolá. Persist používa vlastný aktuálny čas, ktorý odoberie po získaní svojho zámku, iba na kontrolu lease a shadow limitu.

**Persist RPC** v jednej transakcii:

1. `SELECT … FROM paper_tests WHERE id = … FOR UPDATE`.
2. Po zámku a s časom `clock_timestamp()` overí: token, `lease_expires_at > clock_timestamp()`, `status = running`, žiadny autoritatívny verdikt, pri shadow čas < `shadow_until_ms`, neexistenciu behu pre `claim_id` aj (test, B). Hodnoty `test_end_ms`, `boundary_ms` a `tick_id` prevezme z claim. Pri expirovanej lease vráti `lease_expired`, pri uzavretom teste `test_closed`, pri shadow po limite `shadow_ended`, aj keď stop RPC ešte neprebehol. Vo všetkých týchto prípadoch sa nezapíše nič.
3. `seq` = max(`seq`) + 1 v rámci testu.
4. Vloží beh, pásku a pozície. Commit.

Keďže na test existuje najviac jedna aktívna claim a `seq` vzniká pod zámkom riadku testu v transakcii zápisu, poradie `seq` sa zhoduje s poradím commitov aj s poradím B.

**Pád procesu** počas výpočtu: lease vyprší. Ak hranica B ešte platí, ďalší tick vytvorí novú claim pre to isté B (`attempt + 1`). Ak medzitým uzavrela nová sviečka, claim vznikne pre nové B a staré B zostane bez behu. **Neskorý zápis** s expirovanou lease sa odmietne aj vtedy, keď persist čakal na zámok, lebo čas sa kontroluje až po jeho získaní. Hodnota `lease_ms` musí byť kratšia než 300 000 a dlhšia než limit trvania funkcie. Návrh 240 000 sa potvrdí v P3.

### 4.4 Heartbeat

Každý tick má riadok v `paper_measure_ticks` a pre každý test riadok v `paper_tick_outcomes`. Existujúca `worker_heartbeats` zo SPEC-005 sa nepoužíva.

### 4.5 Dokončenie

Každý tick najprv pre testy so stavom `running` posúdi všetky uložené behy po bráne, ktoré ešte nemajú posúdenie. Posudzujú sa v poradí `seq`, každý cez assess RPC (5.7). Pre shadow test po `shadow_until_ms` zavolá stop RPC. Až potom rieši novú claim. Pád medzi uložením behu a posúdením dorieši ďalší tick, pretože bez posúdenia nová claim nevznikne. Testy mimo `running` sa nespracúvajú.

## 5. Hodnotenie podľa variantu B (B6, B8)

Evaluator je čistá funkcia nad uloženými dátami. Nevolá OKX ani simuláciu.

1. **Vstup.** Automatické behy testu z `paper_runs`, zoradené podľa `seq`.
2. **Časová brána.** Beh ju splní, ak `as_of_ms` (= `test_end_ms`) ≥ S + `gate_ms`. Behy pred bránou sa neposudzujú a hodnotenie neblokujú. Stav behu `invalid` pred bránou nie je verdikt testu INVALID. Kým žiadny beh nesplní časovú bránu, test je NOT\_EVALUATED aj pri úplných dôkazoch a 30 a viac obchodoch.
3. **Oprávnenosť** behov po bráne, v poradí `seq`:
   - **preukázateľne neoprávnený:** `status = valid`, všetky dôkazy prítomné vrátane overeného pôvodu ticku (4.2), hashe overené a počet platných uzavretých obchodov s S ≤ closed\_at ≤ as\_of je menší než `gate_trades`. Pokračuje sa ďalším behom.
   - **oprávnený:** to isté, ale počet je aspoň `gate_trades`. Tento beh je kandidát.
   - **neoveriteľný:** `status = invalid`, chýba ktorýkoľvek dôkaz alebo nesedí hash. Platí to bez ohľadu na počet obchodov. **Výnimka pre pôvod ticku:** samotné chýbajúce potvrdenie pôvodu vedie k `unverifiable` až po uplynutí `origin_grace_ms` alebo pri konečnom stave `failed` (4.2). Dovtedy beh nie je klasifikovaný a assess vracia `origin_pending`. Neoveriteľný beh je kandidát a verdikt testu je INVALID s dôvodom. Skorší potenciálne oprávnený beh s chýbajúcimi dôkazmi sa nikdy nepreskočí v prospech neskoršieho kompletného. Chýbajúca páska po bráne nie je dôkaz, že obchodná brána nebola splnená. Posúdené behy a ich stav sa zapíšu do `eligibility_trace`.
4. **c30 a T\*.** c30 = `closed_at` tridsiateho platného uzavretého obchodu kandidátovej pásky, zoradené podľa (`closed_at_ms`, `ordinal`). T\* = max(S + 2 592 000 000, c30). Pri `gate_ms = 2 592 000 000` má oprávnený kandidát as\_of ≥ T\*.
5. **Metriky** iba z platných uzavretých obchodov kandidátovej pásky orezanej na S ≤ closed\_at ≤ T\*. Všetko sa počíta z čistého PnL obchodu po nákladoch (`profit_usdt`): počet, čisté PnL, expectancy (nulové obchody zostávajú v menovateli), `gross_profit` = súčet kladných čistých PnL, `gross_loss` = |súčet záporných čistých PnL|.
   - PF = `gross_profit` / `gross_loss`, ak `gross_loss` > 0.
   - `gross_loss` = 0 a `gross_profit` > 0: `profit_factor = null`, `pf_infinite = true`, PF podmienka splnená.
   - `gross_loss` = 0 a `gross_profit` = 0: PF nedefinované, `pf_infinite = false`, PF podmienka nesplnená, PASS nevznikne.
   - Ostatné brány a prahy platia vždy.
6. **DD (B8).** `summary.max_drawdown_percent` kandidátového behu za \[S, as\_of\], zaokrúhlené na 2 desatinné miesta. Názov poľa a presnosť sa zapíšu do `dd_field`. Nie je to DD k T\*.
7. **Verdikt a dokončenie.**
   - Official, oprávnený kandidát: PASS iba ak platí expectancy > 0 **a** DD ≤ 15 % **a** (konečné PF ≥ 1,10 **alebo** `pf_infinite = true`). Inak FAIL. Teda `pf_infinite = true` s DD > 15 % je FAIL. Samé nulové výsledky (expectancy = 0, `pf_infinite = false`) PASS nikdy nedajú. Prahy sú v `paper_tests.settings`.
   - Scale: DIAGNOSTIC iba pri prvom certifikovanom kandidátovi, teda platnom behu s úplnými dôkazmi, splnenou časovou aj obchodnou bránou. Samotné úplné dôkazy bez splnených brán DIAGNOSTIC nedávajú.
   - Neoveriteľný kandidát (Official aj Scale): INVALID. DIAGNOSTIC ho nesmie prekryť.
   - Assess RPC v jednej transakcii zamkne riadok testu. **Idempotencia:** ak posúdenie behu už existuje, vráti existujúce posúdenie (a autoritatívny verdikt, ak vznikol) a nič nezapíše. To platí aj pre opakovaný alebo súbežný assess toho istého behu. Inak overí `status = running` a neprítomnosť autoritatívneho verdiktu. Ak test nie je `running`, nič nemení. **Poradie:** odmietne posúdenie behu, ak existuje uložený beh po bráne s nižším `seq` bez posúdenia (`assessment_out_of_order`). **Kontrola pôvodu pred klasifikáciou:** pred definitívnou klasifikáciou a vložením posúdenia sa uplatní kontrola pôvodu podľa 4.2. Pri `origin_pending` assess vráti tento výsledok bez zápisu posúdenia, verdiktu aj zmeny stavu testu a nová claim zostáva blokovaná. Až potom vloží posúdenie behu. Pri preukázateľne neoprávnenom behu tým končí a ďalšia claim je povolená. Pri oprávnenom alebo neoveriteľnom behu v tej istej transakcii vloží verdikt s `kind = authoritative` a vykoná prechod: PASS, FAIL, DIAGNOSTIC → `completed`, INVALID → `invalid`. Po neoveriteľnom kandidátovi je test `invalid`, takže claim aj persist ďalej vracajú `test_closed`. Neskorší dôkaz pôvodu už vydaný verdikt ani stav nemení. Pre shadow test sa autoritatívny verdikt nevkladá, posúdenie sa iba zaznamená. Stop RPC (iba shadow, čas ≥ `shadow_until_ms`) vykoná prechod `running` → `stopped` bez verdiktu.
   - Opakované hodnotenie je riadok `kind = audit` s odkazom na autoritatívny. Stav testu nemení.
   - INVALID nie je FAIL.

## 6. UI a dashboard (B7, B8)

Prehliadač iba zobrazuje stav a nijako neriadi meranie.

- **`GET /api/paper/tests`.** Vráti testy s identitou (`label`, `start_ms`), stavom, posledným behom podľa `test_id` a autoritatívnym verdiktom. Žiadne okno posledných 30 behov (T1).
- **Zdroj pravdy je server.** `localStorage` sa pre nové testy nepoužíva na stav ani časy.
- **Bez slučky v prehliadači.** Pre nové testy sa odstráni `runPaperOnce`, `runScaleOnce` a automatické obnovenie. Staré HYPE testy zostanú iba na čítanie.
- **Tlačidlá.** „Pokračovať“ sa pre nové testy nezobrazuje. Spustenie testu je samostatná akcia s potvrdením. Ručný diagnostický beh je jasne odlíšený a zapisuje iba do `paper_diagnostic_runs`.
- **Časy.** Zobraziť `test_end` (= as\_of) aj poslednú použitú sviečku, v UTC a lokálnom čase s názvom pásma.
- **DD.** Pomenované pole „Max DD (summary, \[S, as\_of\])“ (T9).
- **Stav merania.** Posledný tick, outcomes, `lag_ms`, dôvod posledného neplatného behu. Verdikt DIAGNOSTIC pre Scale zobraziť bez ekonomického hodnotenia.

## 7. Akceptačné testy

Všetky AT bežia bez živého trhu, so syntetickými sviečkami a knihou, lokálne alebo na Supabase branch. Nikdy nie na produkčnej DB. Pozitívny aj negatívny prípad musí prejsť. Produkčná kontrola je v sekcii 8 (P6) a nie je opakovaním AT.

| ID | Oprava | Test | Očakávaný výsledok |
| --- | --- | --- | --- |
| AT1 | B1 | Dva behy s rovnakým párom, `close_time` a poradím obchodu, rôzny beh alebo test | Dve oddelené sady riadkov, žiadny prepis |
| AT2 | B1 | UPDATE a DELETE na všetkých nových tabuľkách | Odmietnuté triggerom. Jediná výnimka: povolený prechod stavu `paper_tests` cez assess RPC alebo stop RPC. |
| AT3 | B2 | Beh bez `code_commit`, bez profilu alebo s iným hashom nastavení | Beh `invalid` s pomenovaným dôvodom |
| AT4 | B3 | Posledná sviečka zo zdroja má `confirm = 0` | Nespracuje sa; žiadny `closed_at` po as\_of |
| AT5 | B3 | `replay_run` nad uloženým behom | Zhodný `tape_sha256` aj `summary` |
| AT6 | B3 | Vloženie archivovanej sviečky s inými hodnotami | `candle_conflict`, pôvodná nezmenená |
| AT7 | B4 | Beh N, potom beh N+1 s inou knihou | Beh N bez zmeny |
| AT8 | B5 | Dva súbežné ticky pre ten istý test | Práve jeden beh; druhý `lease_held` alebo `no_new_candle` |
| AT9 | B5 | Tick bez `CRON_SECRET` | 401 bez zápisu |
| AT10 | B5 | Ten istý B dvakrát | Jeden beh |
| AT11 | B6 | Chýbajúca sviečka v rozsahu | `invalid`, `incomplete_candles` |
| AT12 | B6 | Neoveriteľný kandidát, potom kompletný beh | Verdikt testu INVALID; neskorší beh sa nepoužije |
| AT13 | B6 | Behy pred časovou bránou, niektoré `invalid` | Stav behov `invalid`; verdikt testu nevznikne, neblokujú |
| AT14 | B6 | Obchod uzavretý medzi T\* a as\_of | Nezapočíta sa do metrík; DD ho zahŕňa |
| AT15 | B7 | 100 Scale behov po poslednom Official behu | Official zobrazuje svoj posledný beh |
| AT16 | B7 | Dokončený test, obnovenie, iné zariadenie | `completed` zo servera; žiadne Pokračovať |
| AT17 | B8 | Verdikt a UI pre ten istý beh | Rovnaké pole a hodnota DD |
| AT18 | Regresia | Optimizer a historický test cez legacy endpoint | Zhodné s `b200950` |
| AT19 | N1 | Beh s `test_end_ms` ≠ B tesne pri bráne | Obe polia uložené; brána sa vyhodnotí z `test_end_ms`; T\* = max(S + 30 d, c30) |
| AT20 | N2 | Po bráne beh bez pásky, potom platný beh so 40 obchodmi | Beh bez pásky má stav `invalid`; zostáva v `eligibility_trace` ako kandidát; verdikt testu INVALID `unverifiable_candidate`; neskorší beh sa nepoužije |
| AT21 | N3 | Claim, pád pred persist, expirácia lease, retry v tom istom B | Jeden beh, `attempt = 2`, `seq` bez medzery |
| AT22 | N3 | Persist s expirovanou lease | `lease_expired`, nič zapísané |
| AT23 | N3 | Druhý claim počas aktívnej lease | `lease_held`; poradie `seq` = poradie commitov = poradie B |
| AT24 | N4 | Ručný diagnostický beh pre B, potom automatický pre B | Oba vzniknú v oddelených tabuľkách; evaluator vidí iba automatický |
| AT25 | N4 | Claim s neexistujúcim, starým alebo už použitým `tick_id` | Odmietnutý |
| AT26 | N5 | INSERT obchodu alebo pozície k uloženému `run_id`; nesúlad `trade_count` pri commite | `run_sealed`; celá transakcia zrušená |
| AT27 | N6 | Päť tickov počas jednej sviečky; tick tesne po hranici so starou sviečkou | Jeden beh, ostatné `no_new_candle`; `boundary_race` bez behu |
| AT28 | N7 | Pád po persist pred assess; opakovaný assess; audit riadok | Jeden autoritatívny verdikt; stav sa nemení |
| AT29 | N7 | Scale: (a) prvý certifikovaný kandidát s oboma bránami; (b) úplné dôkazy po časovej bráne, ale menej než 30 obchodov; (c) neoveriteľný kandidát | (a) DIAGNOSTIC a `completed`, ďalšie behy nevznikajú; (b) žiadny verdikt, test pokračuje; (c) INVALID a `invalid` |
| AT30 | R3 | Vek knihy 0 ms, presne 60 000 ms, 60 001 ms, −1 ms, chýbajúci čas | valid, valid, `book_too_old`, `book_from_future`, `cost_profile_unavailable` |
| AT31 | R4 | (a) `gross_loss = 0` a zisk > 0; (b) všetky obchody s nulovým čistým PnL; (c) konečné PF 1,09 a 1,10 | (a) `pf_infinite = true`; (b) `pf_infinite = false`, expectancy = 0, Official nedostane PASS; (c) FAIL a PF podmienka splnená |
| AT32 | Doplnky | S mimo hranice 5m, rôzne `warmup_bars` | Správny `first_open` a `expected_count` |
| AT33 | R1 | Úplné dôkazy, aspoň 30 platných uzavretí, ale test\_end < S + 30 dní | NOT\_EVALUATED; žiadny riadok verdiktu, test zostáva running |
| AT34 | V1 | (a) Zdroj vráti close\_time = …59.999 (open\_time + 300 000 − 1); (b) clock\_timestamp() prekročí 5m hranicu medzi kontrolou a INSERT claim | (a) B = open\_time + 300 000; správny last\_open a expected\_count; podmienka hranice prejde; (b) uložený test\_end\_ms = t\_claim, rovnica s B platí; žiadny druhý odber času |
| AT35 | V5 | Claim pred časovou bránou, výpočet a persist až po nej; worker pošle iný test\_end | test\_end\_ms z claim, beh je pred bránou; hodnota od workera sa ignoruje alebo odmietne |
| AT36 | V2 | Lease vyprší počas čakania persist na zámok | lease\_expired, nič zapísané |
| AT37 | V3 | Assess vydá autoritatívny verdikt počas rozpracovanej claim | Persist test\_closed; po dokončení nepribudne beh |
| AT38 | V4 | (a) service\_role alebo iná rola volá paper\_tick\_start(); (b) skutočný cron tick so záznamom succeeded; (c) záznam chýba aj po origin\_grace\_ms; (d) záznam running, v lehote prejde na succeeded; (e) beh s menej než 30 obchodmi a neovereným pôvodom: (e1) počas lehoty, (e2) po uplynutí lehoty alebo pri failed | (a) permission denied; (b) pôvod overený (pozitívny prípad v P4); (c) tick\_origin\_unverified, verdikt testu INVALID; (d) origin\_pending, potom platné posúdenie; (e1) origin\_pending, bez zápisu, claim blokovaná; (e2) neoveriteľný, nikdy proven\_ineligible, verdikt testu INVALID |
| AT39 | N3 | Pád, lease vyprší a medzitým uzavrie nová sviečka | Claim pre nové B; staré B bez behu; nič neblokuje |
| AT40 | V3, sekcia 9 | (a) výpadok ticku po bráne; (b) claim bez commitu; (c) uložený neoveriteľný beh | (a) medzera v outcomes, neblokuje; (b) žiadny riadok v paper\_runs, neblokuje; (c) verdikt testu INVALID |
| AT41 | V6 | Shadow, test ešte running: claim po limite; persist presne v čase shadow\_until\_ms a po ňom | Claim aj persist shadow\_ended, žiadny zápis (podmienka čas < shadow\_until\_ms, hranica je vylučujúca); stop RPC vedie k stopped; žiadny autoritatívny verdikt ani ďalší beh |
| AT42 | R4 | (a) pf\_infinite = true a DD > 15 %; (b) preukázateľne prázdna úplná páska po bráne | (a) FAIL; (b) beh preukázateľne neoprávnený, test NOT\_EVALUATED |
| AT43 | V3 | (a) nová claim pri uloženom, ešte neposúdenom behu po bráne; (b) claim po proven\_ineligible; (c) claim po unverifiable; (d) opakovaný alebo súbežný assess toho istého behu | (a) evaluation\_pending, claim nevznikne; (b) claim povolená; (c) test invalid, claim test\_closed; (d) jedno posúdenie, opakovanie vráti existujúce bez zápisu |
| AT44 | V3 | Súbeh: assess kandidáta, nová claim a persist rozpracovanej claim v ľubovoľnom poradí zámkov; assess behu s vyšším seq pred nižším | Najviac jeden autoritatívny verdikt; po verdikte žiadna claim ani beh; assessment\_out\_of\_order |
| AT45 | V6 | Claim pred shadow\_until\_ms, persist po ňom, stop RPC ešte neprebehol | shadow\_ended, nič zapísané; potom stop RPC vedie k stopped |
| AT46 | V4 | Záznam cron.job\_run\_details: (a) oneskorený v rámci lehoty; (b) chýba aj po uplynutí origin\_grace\_ms; (c) assess presne v čase tick.created\_at + origin\_grace\_ms a 1 ms po ňom; (d) failed počas lehoty; (e) succeeded doručený až po vydanom INVALID; (f) opakované retry počas lehoty | (a) origin\_pending, bez zápisu, claim blokovaná, neskôr platné posúdenie; (b) neoveriteľný, tick\_origin\_unverified, verdikt testu INVALID; (c) na limite origin\_pending, po ňom tick\_origin\_unverified; (d) okamžite tick\_origin\_unverified; (e) test zostáva invalid, verdikt bez zmeny; (f) lehota sa neobnovuje, počíta sa od pôvodného tick.created\_at |

## 8. Poradie implementácie a schvaľovacie brány

Každá fáza je samostatne overiteľná a vyžaduje samostatné Romanovo povolenie. Žiadna fáza nemení zmrazené časti.

| Fáza | Obsah | Kde beží | Overenie | Potrebné povolenie |
| --- | --- | --- | --- | --- |
| P1 | Čistý kód bez DB: loader `closed_only`, zarovnanie rozsahu, hashovanie, `replay_run`, evaluator | Lokálne, CI | AT4, AT5, AT12–AT14, AT18–AT20, AT30–AT34 (a), AT40 (evaluator), AT42 | Commit do novej vetvy, PR bez merge |
| P2 | Migrácia: tabuľky, triggery, claim, persist, assess a stop RPC | Supabase branch | AT1, AT2, AT6, AT7, AT10, AT21–AT23, AT25, AT26, AT28, AT34 (b), AT35 (claim a persist), AT36–AT39, AT41, AT43–AT46 | DDL na branch |
| P3 | Endpoint tick a diagnostický endpoint, ručné volanie | Preview + branch | AT3, AT8, AT9, AT11, AT24, AT35 (vstup od workera) | Preview deploy |
| P4 | `pg_cron` + `pg_net` na branchi volajúci preview | Preview + branch, aspoň 24 h | AT27, AT29; bez medzier v tickoch; AT5 nad reálnymi behmi; AT38 (b) so skutočným cron jobom vrátane `session_user` | Cron na branchi, bypass ochrany preview |
| P5 | UI | Preview | AT15–AT17 | Preview deploy |
| P6 | Produkčná migrácia, merge, deploy, cron job | Produkcia | S1 až S3 nižšie | Merge, deploy, produkčné DDL, cron |
| P7 | Nový strategický test | Produkcia | Vlastný preregistrovaný protokol | Samostatné schválenie testu |

**P6, produkčná kontrola (nie opakovanie AT):**

- **S1 schéma, iba čítanie.** Z katalógu overiť tabuľky, triggery, RLS, granty, funkcie a cron job. Porovnať hash definície schémy s branchou.
- **S2 shadow test 24 h.** Test s `shadow = true` a `shadow_until_ms` = začiatok + 86 400 000. Overiť ticky bez medzier a outcomes. Assess RPC zaznamenáva posúdenia bez autoritatívneho verdiktu. Po limite claim nevznikne a stop RPC prevedie test do stavu `stopped`.
- **S3 replay, iba čítanie.** `replay_run` nad tromi shadow behmi.
- Na produkcii sa nerobia negatívne testy (pokusy o UPDATE, DELETE, dodatočný INSERT). Tie sú overené v P2.

P1 až P6 merajú iba technickú správnosť. Ekonomický výsledok z preview a shadow behov sa nehodnotí a nesmie ovplyvniť nastavenia budúceho testu.

Strategy D: B1–B6 a B8 sú podmienkou aj pre jej forward. Denné sviečky a týždenné rozhodovanie budú potrebovať vlastný typ testu, dátový model a scheduler môžu byť spoločné.

## 9. Otvorené otázky pre druhý review

- **`test_end` (uzavreté v druhom review).** Legacy `test_end` bol `request.end_time`, ktorý frontend určil pred požiadavkou, nie čas dokončenia výpočtu. Rev3 ho určuje ako `clock_timestamp()` v claim RPC, tiež pred výpočtom. Význam vopred určeného horizontu a pravidlo `as_of = test_end` sa zachovávajú. **Výslovne sa mení zdroj hodín (prehliadač → DB) a okamih merania (pred HTTP požiadavkou → pri claime).** Presná časová ekvivalencia s legacy nie je preukázaná.
- **Medzery v behoch (uzavreté).** Výpadok ticku sám nie je INVALID. Claim bez commitu nevytvorí beh a neblokuje. Uložený beh bez dôkazov je neoveriteľný a vedie k INVALID. Prvenstvo určuje poradie `seq` uložených behov (AT40).
- **Dostupnosť `pg_cron` a `pg_net`** v projekte a na branchi. Overiť read-only až na povolenie.
- **`lease_ms` vs. limit trvania funkcie Vercelu.** Navrhnuté 240 000, nepotvrdené. Overí sa v P3.
- **Číslo migrácie.** Potvrdiť zoznam aplikovaných migrácií (028/029 sú mimo `main`).
- **Ochrana preview na Verceli.** `pg_net` potrebuje bypass pre automatizáciu v P4.
- **Legacy HYPE testy.** Ponechať v DB a UI iba na čítanie, slučku vypnúť v P5.
- **Veľkosť pásky.** Približne 8 600 behov na test. Prípadne komprimovaný JSON s hashom namiesto riadkov.

## 10. Zaznamenané rozhodnutia R1 až R5

| ID | Rozhodnutie |
| --- | --- |
| R1 | M1 zamietnutá. `as_of = test_end`, posledná sviečka osobitne, T\* zo S + 30 dní a c30 vlastnej pásky. M1 môže byť iba súčasťou protokolu budúceho testu. |
| R2 | `pg_cron` + `pg_net`, kontrola každú minútu, výpočet pri novej uzavretej 5m sviečke, zákaz duplicitných behov, preukázateľný automatický pôvod. |
| R3 | 0 ≤ `cost_fetched_at_ms` − `book_ts_ms` ≤ 60 000. Chýbajúci čas, staršia kniha alebo kniha z budúcnosti znamenajú INVALID. Čerstvosť nepotvrdzuje kvalitu nákladového modelu. |
| R4 | `pf = null`, `pf_infinite = true` iba pri nulových hrubých stratách a kladnom hrubom zisku. Samé nulové výsledky: PF nie je nekonečné a PASS nevznikne. Ostatné brány platia. Chýbajúca páska nie je nula obchodov. |
| R5 | Scale zostáva diagnostický. Platné dokončenie: DIAGNOSTIC a zastavenie behov. Chýbajúce dôkazy: INVALID, DIAGNOSTIC ho neprekryje. |
