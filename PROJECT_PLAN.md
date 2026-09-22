# Projektový plán – Revoltis Bot Strategy

## Cieľ

Vytvoriť bezpečnú webovú aplikáciu na návrh, testovanie a porovnávanie algoritmov nákupu a predaja kryptomien. Aplikácia pracuje iba so simulovaným kapitálom a výslovne neobsahuje reálne burzové príkazy ani burzové API kľúče.

## Výsledok prvej použiteľnej verzie

Používateľ v aplikácii:

- vyberie coiny a altcoiny;
- nastaví sviečky, kapitál, veľkosť obchodu a denný limit;
- nastaví vstup po poklese: Bollinger Bands, RSI, ATR, objem a potvrdenie otočenia;
- nastaví výstup: stop-loss a trailing profit;
- uloží verziu stratégie;
- spustí backtest alebo dry-run simuláciu;
- porovná zisk po poplatkoch, počet obchodov, úspešnosť a maximálny drawdown;
- exportuje výhradne dry-run konfiguráciu pre Freqtrade.

## Fázy

| Fáza | Obsah | Výstup | Stav |
|---|---|---|---|
| 1. Základ | React rozhranie, FastAPI, databázová schéma, bezpečný dry-run model | Nastavovací panel a API | hotové |
| 2. Prepojenie dát | Import simulovaných obchodov z Freqtrade SQLite do Supabase | História obchodov a aktuálny stav | nasleduje |
| 3. Backtest | Spustenie a archivácia backtestu pre konkrétnu verziu parametrov | Porovnateľné výsledky | nasleduje |
| 4. Analytika | Kapitálová krivka, drawdown, dôvody nevstúpenia, výsledky podľa coinu | Rozhodovací panel | nasleduje |
| 5. Riadenie verzií | Uložené verzie, poznámky, klonovanie stratégie, porovnanie A/B | História experimentov | nasleduje |
| 6. Účty účastníkov | Prihlásenie e-mailom, vlastné menu a oddelené údaje každého účastníka | Osobný pracovný priestor | nasleduje |
| 7. Nasadenie | Bezplatný webový frontend a zabezpečené API | Prístupná aplikácia | po dokončení fáz 2–6 |

## Pravidlá simulácie

1. Každé nastavenie je verzia stratégie a nikdy neprepíše výsledok staršieho testu.
2. Backtest musí započítať poplatok a prípadne konzervatívny odhad spreadu.
3. Výsledok sa zobrazuje za každý coin samostatne aj za celé portfólio.
4. Ak sa parametre zmenia, výsledok predchádzajúcej verzie ostáva označený pôvodným nastavením.
5. Export má pevne nastavené `dry_run: true`; aplikácia nemá obrazovku na live trading.
6. Každá stratégia, simulácia, backtest a vybraný zoznam coinov patrí presne jednému prihlásenému účastníkovi.

## Priorita parametrov

Prvá obrazovka ponechá len parametre, ktoré majú najväčší dopad: coin, sviečka, kapitál, suma na obchod, denný limit, RSI limit, minimálna ATR volatilita, stop-loss a trailing profit. Rozšírené nastavenia zostanú dostupné po rozbalení, aby sa stratégia dala upraviť bez preťaženia formulára.
