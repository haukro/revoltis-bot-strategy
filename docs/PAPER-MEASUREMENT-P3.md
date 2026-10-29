# P3 — endpointy merania, iba CI

Roman 29. 10. 2026 schválil dodatok A1 a pokračovanie P3 výlučne v CI.
Táto vetva nad P2 pridáva endpointy, worker a pripravenú migráciu. Nevykonáva
preview deploy, merge, prístup k spravovanej DB ani nový strategický test.
Pôvodná FINAL rev3, R1–R5, engine, costs, P1 evaluator a migrácie 001–029
aj pôvodný súbor migrácie P2 zostávajú nezmenené.

## Správanie

- `POST /api/paper/measure/tick`: iba `tick_id`, Bearer `CRON_SECRET`.
- `POST /api/paper/diagnostic/run`: iba `test_id`, samostatný
  Bearer `PAPER_DIAGNOSTIC_SECRET`; automatické tajomstvo nestačí.
- Cudzie polia vrátane `test_end_ms` a nastavení HTTP odmietne. Zlé alebo
  chýbajúce tajomstvo nespustí worker ani DB požiadavku.
- Endpointy sú predvolene vypnuté. Aktuálna aktivácia povoľuje iba GitHub CI,
  lokálny Supabase na porte 54321 a samostatné PAPER prihlasovacie nastavenia.
  Nepoužijú legacy SUPABASE URL/key. Vercel prostredie je odmietnuté.
- Worker načíta running testy po stránkach cez úzke serverové RPC. Najprv
  posúdi čakajúce behy v seq poradí, potom zastaví skončený shadow, až potom
  rieši novú claim. `origin_pending` blokuje ďalšiu claim.
- Opakovaný tick nemôže zopakovať výpočet. DB serializuje aj rozdielne
  súbežné ticky. Claim určuje horizont, persist ho znovu preberá z claim.
- Nový loader zachováva `confirm` a duplicity pre validáciu. Legacy loader
  sa nemení. OKX 5m pole ts je open time; raw close je rovnaká normalizovaná
  konvencia ako doteraz, open + 300000 − 1; OKX samostatný close timestamp
  neposiela. B sa odvodzuje výhradne z open time.
- Jeden nákladový profil na beh, nezmenený `book_costs`, obojstranná kontrola
  veku knihy. Simulácia používa archivované normalizované hodnoty, zmrazené
  nastavenia, S a `force_close_at_end=False`.
- Zlyhanie vstupov nevytvorí falošnú prázdnu pásku. Null count a presence
  flags označujú chýbajúce dôkazy; skutočne prázdny výsledok má count 0.
- `raw` zaznamenáva Python, verzie runtime balíkov a pomenované dôvody.
  Chýbajúca identita evaluatora blokuje posúdenie, kým nasledujúci tick
  nezíska platnú identitu; worker nevydáva vlastný verdikt.

## Pripravená migrácia P3

`20261029161155_paper_measurement_p3_worker.sql` vznikla cez Supabase CLI
2.122.0 `migration new`. Aplikuje sa iba v jednorazovom CI stacku po P2 sade.

Pridáva `paper_worker_page`, `paper_diagnostic_context`,
`paper_worker_outcome`. EXECUTE má iba service_role a vlastník, pevný
search_path, žiadne nové granty na tabuľky ani členstvo HTTP roly vo vlastníkovi.
Installer členstvo je dočasné a odobrané pred commitom. Page overí čerstvý
existujúci tick pred čítaním; outcome serializuje zápis pod testovým zámkom.
Pri existujúcej claim vyžaduje jej token, opakovanie neprepisuje heartbeat.

Dve úpravy existujúcich RPC sú v novej migrácii, nie v súbore P2:

1. Persist zachová explicitný dôvod neplatnosti vstupov pred všeobecným
   chýbaním pásky. Globálne kontroly identity/nákladov zostávajú pred ním.
2. Diagnostický persist pripúšťa chýbajúcu pásku/pozície iba pri invalid
   behu, s príslušnými presence flags a null count. Nezamieňa ich za nulu.

Cron RLS/ACL z A1 sa týmto do aplikačnej migrácie neprenášajú. Sú stále
samostatným CI nastavením a otvorenou otázkou spravovaného prostredia.

## Testy a fázy

| P3 scenár | Overenie |
| --- | --- |
| AT3 chýbajúci commit/profil, nezhodné nastavenia | Unit worker; chýbajúci profil aj HTTP + skutočný DB persist |
| AT8 súbežné ticky | Dva HTTP requesty, dve skutočné cron tick ID, jeden DB beh |
| AT9 tajomstvo | Unit + skutočný HTTP endpoint, nezmenené počty DB riadkov |
| AT11 medzera sviečok | Unit + HTTP/DB, invalid incomplete_candles a chýbajúca páska |
| AT24 diagnostika | HTTP diagnostika a automatický beh pri rovnakom B v oddelených tabuľkách |
| AT35 horizont | HTTP zakázané extra pole, worker neposiela horizont do persist, zhoda DB claim/run |
| AT28 zotavenie | Persist bez assess; ďalší HTTP tick posúdi uložený beh s reálnym cron pôvodom |
| Shadow, chyby, RPC ACL | Stop pred claim, trvalý error outcome, zakázané RPC pre anon/authenticated |
| Chýbajúce otvorené pozície | Invalid missing_open_positions, nikdy falošné open_count=0 |

Unit testy nevolajú živý trh. Integračné testy spúšťajú skutočný Uvicorn
na loopback, reálny PostgREST, presné migrácie a krátky skutočný cron.
Sviečky a kniha sú syntetické, engine je pôvodný. Toto nie je P4 24h beh.
CI checkout pre lokálny stack je pripnutý na head SHA a porovnaný s git HEAD;
toto SHA sa nastaví do `VERCEL_GIT_COMMIT_SHA` iba ako explicitná CI fixture.
Nepreukazuje to väzbu budúceho Vercel deployu na commit.

## Otvorené pred uzavretím P3

**Blokujúca medzera úplnosti:** existujúci `simulate()` vracia iba počet
otvorených pozícií. Ak je nenulový, worker uloží `missing_open_positions`.
Bez schváleného rozšírenia pozorovateľného výstupu simulátora sa taký beh
nemôže certifikovať. Engine sa v tomto PR nemení; nepoužíva sa kopírovaný
engine, introspekcia jeho lokálnych premenných ani vynútené zatvorenie.

Potrebný následný návrh: pridať čisto výstupné údaje o otvorených pozíciách
bez zmeny rozhodovania a výsledkov legacy volaní, overiť regresiu, potom
namapovať run-owned riadky. Vyžaduje samostatné rozhodnutie k zmrazenému enginu.

Pôvodná preview/managed brána P3 zostáva otvorená. Pripnutý lokálny stack
ju nenahrádza. Worker má rozpočet 90 s (+ najviac 3 s na timeout heartbeat),
CI testy lease 120000 ms. Návrh lease 240000 ani grace 600000 nie je týmto
schválený; skutočný Vercel limit a životný cyklus CPU výpočtu zostávajú na
preview overenie. Pri zrušení coroutine sa už persist nevykoná; výpočtové
vlákno môže dobehnúť iba v pamäti. Tvrdý pád zotaví DB lease.

AT49(d), AT51, oprávnenia spravovanej platformy/upgrady, 24h P4 a disposition
Vercel deploy guardov zostávajú otvorené. P3 branch má guard ešte pred svojím
prvým publikovaním. Žiadna z týchto brán nie je zeleným CI automaticky uzavretá.

## Záznam zmien

| Dátum | Zmena |
| --- | --- |
| 29. 10. 2026 | A1 označené schválené, oddelená P3 vetva a endpointy, CI-only aktivácia, úzke worker RPC a testy. |
| 29. 10. 2026 | Zdokumentovaná medzera výstupu otvorených pozícií; zachované invalid správanie bez zásahu do enginu. |
