# Lessons learned pre Revoltis simulátor

## Čo ukázali doterajšie testy

1. **Príliš silný filter nie je bezpečnejší, ak nevygeneruje žiadne obchody.** V3/V4 kombinácia Bollinger + RSI + ATR + objem + potvrdenie otočenia bola správne konzervatívna, ale pri viacerých coinoch viedla k nule vstupov. Preto musí aplikácia pri každej zmene ukázať počet signálov, počet uskutočnených obchodov a dôvod odmietnutia.
2. **Jedna stratégia nemá sedieť každému coinu.** Likvidita a bežná volatilita PEPE, DOGE, WIF, BONK a SUI sú rozdielne. Simulátor má ukladať samostatný výsledok pre každý pár a neskôr umožní vlastný profil parametrov pre coin.
3. **1-minútové sviečky zvyšujú šum aj náklady.** Pre prvé porovnania je rozumný základ 3m. Aplikácia dovolí 1m, 3m, 5m a 15m, ale výsledok musí počítať poplatky a spread.
4. **Trailing zisk neznamená garantovaný zisk.** Chráni už dosiahnutý pohyb až od nastavenej hranice. Stop-loss a limit otvorených pozícií ostávajú nezávislé bezpečnostné brzdy.
5. **Denný limit a pevná veľkosť obchodu sú dôležitejšie než naháňanie frekvencie.** Základ je 100 USDT, maximálne 50 USDT na obchod, 1 otvorená pozícia a 10 vstupov denne. Zvýšenie frekvencie sa bude posudzovať iba podľa výsledku po poplatkoch a maximálneho drawdownu.

## Postup, ktorý má aplikácia vynútiť

1. Navrhnúť parametre a vybrať coiny.
2. Spustiť backtest na rovnakom časovom období pre pôvodnú aj zmenenú verziu.
3. Porovnať zisk po poplatkoch, win rate, max drawdown, počet obchodov a priemernú dobu držania.
4. Až keď zmenená verzia prejde porovnaním, pustiť ju v dry-run.
5. Žiadna konfigurácia vytvorená aplikáciou nesmie vypnúť `dry_run` ani obsahovať burzový kľúč.

## Metriky úspechu

- výsledok po poplatkoch, nie iba hrubý zisk;
- maximálny drawdown;
- počet obchodov za deň a podiel ziskových obchodov;
- výsledok samostatne pre každý coin;
- porovnanie s jednoduchým držaním daného coinu za rovnaké obdobie.
