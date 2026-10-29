# P3 — endpointy merania, iba CI

Roman 29. 10. 2026 schválil dodatok A1 a pokračovanie P3 výlučne v CI.
Táto vetva nad P2 pridáva endpointy, worker a pripravenú migráciu. Nevykonáva
preview deploy, merge, prístup k spravovanej DB ani nový strategický test.
Pôvodná FINAL rev3, R1–R5, costs, P1 evaluator a migrácie 001–029
aj pôvodné súbory migrácií P2/P3 zostávajú nezmenené. Roman následne
29. 10. 2026 výslovne povolil voliteľný výstup otvorených pozícií zo simulátora;
obchodné rozhodovanie, výpočty a pôvodný výstup legacy volaní sa nemenia.

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
- P3 volá `simulate(..., include_open_positions=True)`. K pôvodnému výsledku
  pribudne `open_position_rows`, hlboká kópia finálnych otvorených pozícií.
  Predvolene je tento výstup vypnutý, vrátane legacy a P1 replay volaní.
  Mapper preverí počet, úplnosť, pár, časy, ceny a duplicity; zachová celý
  pozorovaný stav v `raw` a pridelí vlastné `run_id` + `ordinal`.
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
| AT5 uložená uzavretá páska | HTTP beh s uzavretým obchodom, run-owned páska a read-only replay_match |
| AT28 zotavenie | Persist bez assess; ďalší HTTP tick posúdi uložený beh s reálnym cron pôvodom |
| Shadow, chyby, RPC ACL | Stop pred claim, trvalý error outcome, zakázané RPC pre anon/authenticated |
| Otvorené pozície | HTTP diagnostika aj automatický beh: úplné riadky pred/po bráne, rovnaké ako pri replayi; otvorené pozície sa nerátajú ako uzavreté obchody |
| Neúplné pozorovania pozícií | Unit: chýbajúci výstup/nesúlad počtu, nesprávny pár/čas, neúplné alebo duplicitné riadky sa odmietnu |
| Regresia simulátora | 12 scenárov porovnáva celý legacy výsledok s hashmi zachytenými pred zmenou, plus rovnosť default/False/True bez nového poľa |

Unit testy nevolajú živý trh. Integračné testy spúšťajú skutočný Uvicorn
na loopback, reálny PostgREST, presné migrácie a krátky skutočný cron.
Sviečky a kniha sú syntetické; obchodná logika enginu je pôvodná, pozície
sa čítajú cez schválený voliteľný výstup. Toto nie je P4 24h beh.
CI checkout pre lokálny stack je pripnutý na head SHA a porovnaný s git HEAD;
toto SHA sa nastaví do `VERCEL_GIT_COMMIT_SHA` iba ako explicitná CI fixture.
Nepreukazuje to väzbu budúceho Vercel deployu na commit.

## Prvé overenie

[CI #127](https://github.com/haukro/revoltis-bot-strategy/actions/runs/40910823443)
na `ebb9c95046c8374ab89c5804d1414cb4f0cf161d` prešlo: backend 260,
P2 transakcie/adapter 67, inštalácia 1, P2 Supabase 17, P3 HTTP 12.
P3 HTTP sada trvala 58,90 s vrátane čakania na skutočný cron. Nová migrácia
sa aplikovala cez lokálny CLI bez superuser eskalácie. Frontend je zelený.

Následne doplnené dva unit prípady (timeout bez neskorého persist a INVALID
kandidát bez ďalšieho výpočtu), HTTP páska s uzavretým obchodom a replayom,
a lokálni bezpečnostní advisors. Aktuálny výsledok je v popise PR pre presný head.

## Schválené doplnenie výstupu pozícií

Roman 29. 10. 2026 povolil rozšíriť pozorovateľný výstup simulátora bez zmeny
obchodnej logiky a legacy výsledku. Implementácia pridáva keyword-only
`include_open_positions=False`; pri True sa až po dokončení všetkých
výpočtov pripojí `open_position_rows = deepcopy(positions)`.

Pozície sa nezatvárajú a nevytvára sa druhý engine. Samotný nenulový počet
už nie je dôvodom neplatnosti. Chýbajúce alebo neúplné riadky stále vedú
k invalid, s null count/presence flag podľa existujúceho kontraktu.

Referenčný súbor `backend/tests/fixtures/paper_p3_engine_5ca2d896.json`
vznikol pred editáciou enginu; jeho zdroj je commit `5ca2d896`, engine blob
`920de5e33e617ab42d71e24ac13694087c301a0d`. Obsahuje hashe kompletných
výsledkov pre nulovú, otvorenú, uzavretú a zmiešanú pásku, náklady,
vynútené zatvorenie, viac párov, vstupný filter, časový exit a prázdne dáta.
Pôvodné AT18 golden fixtures sa neprepisujú.

CI okrem pôvodného P1 replay kontraktu (páska, summary, počet pozícií)
porovnáva **všetky nové riadky pozícií** s výsledkom opt-in replayu z archívu.
Po 30-dňovej bráne je úplný beh s nulou uzavretých obchodov a jednou
skutočne otvorenou pozíciou `proven_ineligible`; nevznikne ekonomický verdikt.

**Identita kódu sa mení:** aj výlučne výstupné rozšírenie mení source bytes,
a teda `engine_sha256`. Existujúce záznamy a hashe sa neopravujú. Replay
starších záznamov vyžaduje zodpovedajúcu pôvodnú verziu enginu; súčasný
P1 kontrakt iný hash správne odmietne ako `engine_mismatch`. P1 evaluator
ani jeho replay implementácia sa nemenia.

## Otvorené pred uzavretím P3

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
| 29. 10. 2026 | Roman povolil voliteľný výstup pozícií. Pridaný opt-in snapshot, run-owned mapovanie a regresné/HTTP kontroly; bez novej migrácie alebo zmeny obchodnej logiky. |
