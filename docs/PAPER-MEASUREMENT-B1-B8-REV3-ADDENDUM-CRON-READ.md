# B1–B8 rev3 — dodatok A1: čítanie dôkazu z pg_cron

**NÁVRH NA SCHVÁLENIE ROMANOM — 29. 10. 2026.**

Tento samostatný dodatok dopĺňa §4.2, §5.7 a overenie v §8 dokumentu
[FINAL B1–B8 rev3](PAPER-MEASUREMENT-B1-B8-REV3.md).
Pôvodný FINAL súbor, R1–R5 a AT1–AT46 sa nemenia. Schválenie dodatku samo
nepovoľuje produkčné DDL, merge, deploy, cron ani nový strategický test.

## Dôvod a rozsah dôkazu

CI #124 na commite `9f74ffd71595db78b9c802e827e04f7604834a60` overilo skutočný
lokálny Supabase, PostgREST, `pg_cron 1.6.4` a `pg_net 0.20.4`. SELECT granty
na cron tabuľky nestačili: RLS skrylo záznam pred vlastníkom assess RPC.
Po doplnení testovacích politík assess pôvod overil.

Migráciu aplikoval skutočný lokálny `postgres` s `rolsuper=false`.
**Testovacie cron granty a RLS politiky vytvoril `supabase_admin`, superuser.**
To nedokazuje, že rovnaké DDL bude povolené spravovanou platformou Supabase.
Pôvodné politiky CI #124 filtrovali iba `username`; nasledujúce pravidlá
vyžadujú aj konkrétny job a zohľadnenie ostatných politík.

Rozšírené CI #125 zachytilo aj zdedené zápisové oprávnenie: 15 prípadov
prešlo, AT50 zlyhal. pg_cron udeľuje DELETE na `cron.job_run_details`
cez PUBLIC. Navyše USAGE na `cron` sprístupňuje funkcie s existujúcim
PUBLIC EXECUTE. Samotná SELECT politika preto nevytvorí rolu iba na čítanie.

## A1.1 Príjemca a oprávnenia

Prístup k dôkazom sa pridáva iba vlastníkovi assess RPC, aktuálne
`paper_rpc_owner`. Pre priame čítanie dostane iba potrebné `USAGE` na schému
`cron` a `SELECT` na `cron.job` a `cron.job_run_details`. Nedostane zápisové
oprávnenia k týmto tabuľkám ani `SUPERUSER`, `BYPASSRLS` či ich vlastníctvo.
Runtime rola zostáva `NOLOGIN`; nevytvorí sa cesta, ktorou by sa na ňu mohol
prepnúť HTTP worker alebo používateľská rola.

Politiky sú `FOR SELECT TO paper_rpc_owner`. Tento dodatok nepridáva čítanie
rolám `PUBLIC`, `anon`, `authenticated`, `service_role`, `authenticator`
ani iným aplikačným rolám. `service_role` má BYPASSRLS, preto jej zákaz
priameho prístupu musí zabezpečiť ACL; samotná RLS politika nie je postačujúca.
Granty, členstvá a existujúce politiky sa posudzujú spoločne.

Ide o obmedzenie prístupu aplikačných rolí a rozsahu nového oprávnenia.
Neodoberá existujúce administrátorské práva PostgreSQL ani existujúce oprávnenia
vlastníka rozšírenia. Tvrdenie „žiadna iná rola“ sa nevzťahuje na superusera.
Čítacia rola nesmie získať ani EXECUTE na plánovanie alebo správu cron jobov
(`schedule`, `schedule_in_database`, `alter_job`, `unschedule`).

Per-role REVOKE nezruší oprávnenie zdedené z PUBLIC. Kandidát v CI preto
odoberá PUBLIC DELETE na logoch a PUBLIC EXECUTE uvedených správcovských
funkcií; potrebné správcovské oprávnenia výslovne ponecháva `postgres`.
**To je širšia zmena ACL rozšírenia, nie iba nová RLS politika.** Pred
spravovaným nasadením sa musí identifikovať každý existujúci oprávnený
správca a používateľ cronu, posúdiť dopad a schváliť presný zoznam grantov.
CI nastavenie sa nesmie bez tejto kontroly preniesť do existujúceho projektu.
Ak sa bezpečné obmedzenie nedá dosiahnuť, použije sa až samostatne schválený
alternatívny mechanizmus podľa A1.4.

## A1.2 Povolené záznamy

V `cron.job` je viditeľný iba job spĺňajúci naraz:

- `jobname = 'paper_tick'`,
- `username = 'paper_cron_owner'`,
- `database = current_database()`.

V `cron.job_run_details` je viditeľný iba riadok s rovnakým používateľom
a databázou, ktorého **`jobid` odkazuje na tento povolený job**.
Samotný názov ani samotné `username` nestačia. Iný job tej istej roly,
rovnaký názov pod inou rolou, iná databáza ani job patriaci čítacej role
nesmú rozšíriť viditeľnosť.

Vlastník assess RPC musí tieto obmedzenia spĺňať aj pri súbehu s existujúcimi
politikami. Permisívne politiky sa skladajú cez OR; nová úzka permisívna
politika sama nezaručuje úzky výsledný prístup. Kandidát pre lokálne CI
používa úzku permisívnu politiku a zodpovedajúcu reštriktívnu politiku pre
každú tabuľku. Výsledný prístup sa overuje dotazom pod reálnou cieľovou rolou.

Cron job a jeho historické záznamy potrebné pre certifikáciu sa počas testu
neodstraňujú. Samotná viditeľnosť záznamu nenahrádza kontrolu pôvodu podľa
§4.2: naďalej platí väzba na PID, časový interval, používateľa, databázu
a konečný stav jobu.

## A1.3 Správanie bez prístupu

Chýbajúci alebo nečitateľný dôkaz nikdy nesmie znamenať overený pôvod.
Zostáva existujúce pravidlo rev3: počas pôvodnej lehoty `origin_grace_ms`
je výsledok `origin_pending`, bez posúdenia a verdiktu, s blokovaním novej
claim. Po lehote alebo pri preukázanom `failed` sa uplatní
`tick_origin_unverified` a príslušný INVALID. Retry lehotu nepredlžuje.
Už vydané posúdenie a verdikt sa spätne nemenia.

## A1.4 Spravovaná platforma a upgrade

Pred uzavretím príslušnej brány sa v povolenom izolovanom **spravovanom**
Supabase prostredí overí:

1. Kto vlastní cron tabuľky a či dostupná administračná rola môže vytvoriť
   potrebné granty a politiky bez superuser prístupu.
2. Efektívne ACL/RLS, PUBLIC a zdedené práva, členstvá rolí, obmedzenie na
   jediný povolený job a zachovanie schválených práv existujúcich správcov.
3. Zachovanie alebo riadená obnova grantov a politík po podporovanom upgrade
   alebo obnove rozšírenia. Zaznamenajú sa verzie a výsledky kontrol pred/po.

**Toto riziko zostáva otvorené.** Lokálne úspešné DDL ani pripnutá verzia
rozšírenia nepreukazujú podporu či trvácnosť nastavení na spravovanej platforme.
Ak priame politiky alebo potrebné obmedzenie ACL nie sú možné, samostatne sa navrhne a preskúma napríklad
privátna SECURITY DEFINER funkcia s legitímnym prístupom ku cron dôkazu,
pevným `search_path`, úzkym výstupom a EXECUTE iba pre vlastníka assess RPC.
Nesmie sprístupniť všeobecné čítanie cron logov HTTP workerovi. Tento fallback
nie je týmto dodatkom automaticky schválený ani implementovaný.

## Navrhované akceptačné testy a fázy

Čísla AT47–AT51 sú návrh; pôvodné AT sa neprečíslujú.

| ID | Scenár a očakávaný výsledok | Zaradenie a limit |
| --- | --- | --- |
| AT47 | Vlastník assess RPC prečíta skutočný `paper_tick` a jeho log; assess overí pôvod. | Doplnkové P2 CI; zopakovať v spravovanom prostredí pred uzavretím P4. |
| AT48 | `anon`, `authenticated`, `service_role` a `authenticator` neprečítajú cron dôkaz a nemajú SET/USAGE členstvo čítacej roly. | Doplnkové P2 CI + spravované prostredie. |
| AT49 | (a) iný job rovnakého vlastníka, (b) rovnaký názov pod iným vlastníkom, (c) job čítacej roly a (d) iná databáza sú neviditeľné. | CI overuje (a)–(c); (d) zostáva samostatnou kontrolou v povolenom prostredí. |
| AT50 | (a) Čítacia rola nemá INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER na cron tabuľkách; DELETE je odmietnutý. (b) Nemá EXECUTE na správu jobov; administrátorovi zostanú explicitné potrebné práva. | Doplnkové P2 CI + kontrola dopadu ACL v spravovanom prostredí. |
| AT51 | Granty/politiky možno nainštalovať oprávnenou administrátorskou rolou na spravovanej platforme; po upgrade/obnove zostáva úzky prístup a odmietnutie ostatných rolí. | Otvorená brána spravovaného prostredia; lokálne CI ju nenahrádza. |

## CI a ďalší postup

CI sa pripína na `ubuntu-24.04`; Supabase CLI už je pripnuté na `2.122.0`.
Tým sa fixuje verzia OS a CLI, nie celý nemenný image ani všetky transitívne
závislosti. Presný runner image je v logu CI; verzie rozšírení a rolové
oprávnenia vypisuje integračný test. Node.js 20 upozornenia akcií sú oddelená
údržba a nemenia výsledok týchto testov.

Návrh ďalšieho kroku po schválení dodatku: pripraviť P3 endpointy a overiť
ich v CI proti lokálnemu Supabase, bez Vercel preview. Takéto overenie bude
opäť iba doplnkové; nemení §8 ani neuzatvára brány P2/P3/P4. P4 naďalej
vyžaduje aspoň 24 h v schválenom izolovanom prostredí s preview.

## Záznam zmien

| Dátum | Zmena | Stav |
| --- | --- | --- |
| 29. 10. 2026 | Návrh A1: čítanie cron dôkazov, rozsah jobu, ACL/RLS, testy AT47–AT51 a riziko spravovanej platformy. | Čaká na Romanovo schválenie; FINAL rev3 zostáva nezmenená. |
| 29. 10. 2026 | CI #125 odhalilo PUBLIC zápisové právo. Návrh doplnený o zdedené DELETE/EXECUTE, kontrolu administrátorských grantov a nutnosť posúdiť širší dopad ACL. | Kandidát iba v CI; nejde o povolenie meniť ACL spravovaného projektu. |

Podklady: [CI #124](https://github.com/haukro/revoltis-bot-strategy/actions/runs/40883584779),
[PostgreSQL 17 CREATE POLICY](https://www.postgresql.org/docs/17/sql-createpolicy.html),
[Supabase Cron](https://supabase.com/docs/guides/cron).
