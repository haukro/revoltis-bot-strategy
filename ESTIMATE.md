# Odhad náročnosti a času

Odhad predpokladá jednu osobu, existujúci Freqtrade dry-run projekt a rozhodovanie bez dlhých prestojov. Čas neobsahuje čakanie na vytvorenie účtov ani samotné trvanie veľkých backtestov.

| Časť | Náročnosť | Odhad |
|---|---:|---:|
| Základné rozhranie, API a databázová schéma | stredná | hotové |
| Import obchodov z Freqtrade do Supabase | stredná | 1–2 dni |
| Ukladanie verzií stratégií a export dry-run konfigurácie | stredná | 1 deň |
| Spúšťanie backtestu a ukladanie výsledkov | vyššia | 2–4 dni |
| Graf kapitálu, drawdown a porovnanie verzií | stredná | 2–3 dni |
| Diagnostika filtrovania a výsledky podľa coinu | vyššia | 2–3 dni |
| Prihlásenie a základné zabezpečenie aplikácie | stredná | 1–2 dni |
| Nasadenie a kontrola | stredná | 1–2 dni |

## Celkový odhad

- **Prvá použiteľná aplikácia:** 5–8 pracovných dní.
- **Plná verzia s backtestmi, grafmi a porovnávaním stratégií:** 10–17 pracovných dní.
- **Kvalitné overovanie stratégií:** priebežne aspoň 2–4 týždne simulácie a testovania na dátach; nejde o čas vývoja, ale o čas potrebný na dôveryhodné vyhodnotenie.

## Čo najviac ovplyvní čas

Najväčší vplyv má automatizované spúšťanie Freqtrade backtestov a spracovanie historických dát. Formulár parametrov a dashboard sú rýchlejšie. Najrozumnejšie je dodať najprv údaje z existujúceho dry-run bota, potom až automatizovať rozsiahle optimalizácie.
