# P4 — doplnková kontrola automatického reťazca v CI

Roman 29. 10. 2026 poveril asistenta samostatnými technickými opravami,
testovaním a vyhodnotením v oddelenom prostredí bez potvrdzovania každého
kroku. Nové náklady a zásahy do produkcie tým nie sú povolené.

Táto vetva nad PR #73 pridáva iba CI, testové fixtures, tento záznam a zákaz
deployu novej vetvy. Aplikačný kód, engine, náklady, SQL migrácie, FINAL rev3,
R1–R5 a predchádzajúce PR sa nemenia. Žiadny strategický test sa nespúšťa.

## Čo kontrola robí

Samostatný job `paper-p4-supabase` vytvorí nový nepripojený lokálny stack.
P2 integračná sada najprv preverí presnú inštaláciu a pripraví rovnaké CI
cron ACL/RLS podľa A1; potom CLI aplikuje presnú existujúcu migráciu P3.
Bezpečnostní advisors zostávajú súčasťou jobu.

Skutočný `paper_tick` beží každú minútu pod `paper_cron_owner`. V jednej
transakcii vytvorí tick, zaradí `net.http_post` a zaznamená request ID.
`pg_net` po commite doručí požiadavku priamo Uvicornu, pôvodnému P3 workeru
a skutočnému PostgREST. Python nespúšťa jednotlivé automatické ticky.
Hodiny, intervaly ani cron katalógy sa nesimulujú.

HTTP server počúva iba na privátnej bráne Docker siete CI. Nastavenia a
kľúče smerujú výlučne na loopback lokálneho Supabase. Náhodné jednorazové
tajomstvo je v privátnej fixture tabuľke, nie v texte cron príkazu alebo
logoch. Prevádzkové tajomstvá, Supabase projekt ani Vercel sa nepoužijú.
Job, server aj stack sa po skúške odstránia.

Trhové vstupy sú syntetické a stabilné podľa času sviečky: dlhší horizont
neprepisuje skoršie OHLC. Engine, výpočet nákladov, claim, persist, assess,
stop, archív a replay sú skutočné. Testovacia scale páska obsahuje 40
stratových uzavretých obchodov; DIAGNOSTIC preto nemožno zameniť za PASS.

## Pokrytie

| Kontrola | Dôkaz |
| --- | --- |
| AT38(b), doručenie | 11 po sebe idúcich minútových tickov, session_user, PID, succeeded interval a HTTP 200 pre každý request |
| AT27 | Celá skupina piatich tickov v jednom B: jeden beh a štyri no_new_candle; ďalšie B vytvoria nové seq bez duplicity |
| AT27 stará sviečka | Oneskorený zdroj v každom ticku: boundary_race bez claim a bez behu |
| AT29(a) | Prvý platný scale kandidát s ≥30 obchodmi: jeden DIAGNOSTIC, completed, ďalšie ticky už nevytvoria beh |
| AT29(b) | Úplná nulová páska po časovej bráne: proven_ineligible, bez verdiktu a test pokračuje |
| AT29(c) | Neúplné sviečky/páska: INVALID; DIAGNOSTIC neprekryje chýbajúci dôkaz |
| Shadow | Skutočný časový limit, stopped bez verdiktu; žiadna claim ani persist po limite |
| AT5 | Read-only replay troch automatických snapshotov a celej scale pásky z archívu |
| Idempotencia a autorizácia | Dve dodatočné pg_net požiadavky: opakovaný posledný tick a nesprávne tajomstvo; bez ďalších zápisov |

Sedem integračných kontrol zdieľa jeden nepretržitý experiment. Zoznam
minút musí byť bez medzery a rozpätie prvého až jedenásteho ticku približne
600 sekúnd. Interval merania sa počíta z uložených DB časov, nie z trvania
CI jobu. Predchádzajúce P2/P3 joby zostávajú samostatne spustené.

## Čo zostáva otvorené

Toto **nie je uzavretie P4** podľa FINAL rev3. Pôvodná brána vyžaduje
spravovanú branch + preview a najmenej 24 hodín nepretržitého merania.
Jeden GitHub-hosted job má [limit 6 hodín](https://docs.github.com/en/actions/reference/limits);
spájanie nesúvisiacich CI behov tento dôkaz nenahrádza. Aktuálne pripravená
skúška má iba 11 minútových tickov, nie 24-hodinový beh.

Spravované oprávnenia a upgrade rozšírení, AT49(d)/AT51, identita skutočne
nasadeného kódu, preview ochrana, Vercel životný cyklus a dlhodobá kontinuita
zostávajú otvorené. Fixture používa lease aj grace 120000 ms a HTTP timeout
100000 ms. Tým sa neschvaľujú produkčné návrhy 240000 a 600000 ms.

Zelený výsledok overuje reťazec merania v uvedenom prostredí. Nehovorí nič
o ziskovosti stratégie. HYPE v8 zostáva INVALID, Strategy D zostáva DRAFT R2.

## Záznam zmien

| Dátum | Zmena |
| --- | --- |
| 29. 10. 2026 | Pripravený samostatný P4 CI job a 7 kontrol so skutočným minútovým schedulerom. Výsledok bude doložený v PR pre presný commit. |

Dokumentácia overená pri príprave: [Supabase changelog](https://supabase.com/changelog),
[pg_net](https://supabase.com/docs/guides/database/extensions/pg_net),
[cron](https://supabase.com/docs/guides/cron/quickstart). HTTP sa odosiela po
commite; úspech cron jobu sám neznamená úspešné HTTP doručenie, preto sa
overujú obidva dôkazy osobitne.
