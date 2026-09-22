# Priebeh vývoja – Revoltis Bot Strategy

## 2026-09-19 – základ aplikácie vytvorený

### Hotové

- samostatný projekt aplikácie;
- React rozhranie s výberom coinov a formulárom parametrov;
- FastAPI rozhranie pre načítanie a uloženie stratégie;
- Supabase databázová schéma pre nastavenia a simulované obchody;
- pevne vynútený simulačný režim: export obsahuje iba `dry_run: true`;
- nastavenia pre sviečky, vstup, výstup, Bollinger Bands, RSI, ATR, objem, otočenie, stop-loss, trailing profit, kapitál a denné limity;
- Docker konfigurácia pre lokálne spustenie.
- projektový plán, štúdia realizovateľnosti a odhad náročnosti.
- importér Freqtrade SQLite → FastAPI → Supabase a záznam poslednej synchronizácie.
- databázové API pre verzie stratégií a archivované výsledky backtestov.
- rozhranie pre uloženie a zobrazenie verzií stratégie pred backtestom.
- analytické API: kapitálová krivka, maximálny drawdown a agregované dôvody odmietnutých vstupov.
- React analytický panel s kapitálovou krivkou, drawdownom a tabuľkou odmietnutých vstupov.
- API a rozhranie na porovnanie dvoch uložených backtestov.
- lokálny trvalý demo režim bez Supabase a finálne overenie API aj React produkčného zostavenia.
- opravené Docker zostavenie: lokálny `node_modules` a výstup `dist` sa už nekopírujú do webového kontajnera.
- tri preddefinované profily stratégie: Konzervatívna, Rastúca a Riziková; jedným kliknutím prenastavia obchodné parametre.
- samostatné simulačné jadro: aplikácia číta verejné sviečky priamo z Binance a vykoná backtest bez Freqtrade.
- výrazný panel spustenia simulácie so stavom Ne beží / Beží / Dokončená, uloženým aj po obnovení stránky.
- osi X a Y na cenovom aj kapitálovom grafe, väčšie texty, tlačidlo zastavenia simulácie a voľba testovacieho časového okna s predvoľbami 6 h, 24 h, 7 dní a 30 dní.
- živý graf zobrazuje posledných 24 hodín sviečok; raster rozlišuje hodiny a 15-minútové intervaly.
- priebeh každej dokončenej simulácie, jej časové okno a kapitálová krivka sa ukladajú lokálne alebo do Supabase podľa konfigurácie.
- časovo limitovaný režim teraz beží priebežne až do zvoleného budúceho dátumu a času; počas celej doby zobrazuje stav PREBIEHA a umožňuje ručné zastavenie.
- samostatná voľba Historické dáta ponúka zrýchlený test spätne za 1 hodinu, 24 hodín alebo 7 dní a je jasne oddelená od živej simulácie.
- historické obdobia fungujú pre 1m, 3m, 5m aj 15m sviečky; limit bol zvýšený tak, aby týždeň fungoval aj pri 1m sviečkach.
- živý cenový graf zachováva posledných 24 hodín pri každej stratégii; zmena profilu mení hustotu sviečok, nie dĺžku zobrazeného obdobia.
- výsledky simulácie sa ukladajú a zobrazujú samostatne pre každý coin; zmena páru prepne zisk, portfólio, úspešnosť, drawdown, kapitálovú krivku aj zoznam obchodov.
- historický výber bol rozšírený na 1 h, 6 h, 24 h, 7 dní, 14 dní a 30 dní; limit pokrýva aj 43 200 jednominútových sviečok za 30 dní.
- do zoznamu boli pridané XEC/USDT a ZEC/USDT; všetkých 14 ponúkaných párov bolo overených ako aktívne Binance Spot páry.
- pridaná automatická read-only kontrola zberu dát: overuje 14 coinov × 4 intervaly, všetkých 6 historických rozsahov, OHLC hodnoty, časovú nadväznosť a stránkovanie Binance API.
- historický výpočet bol optimalizovaný na lokálne okná indikátorov; kontrolný 30-dňový 1m prepočet jedného coinu trvá približne 1,9 sekundy na testovacom počítači.
- ku každému výsledku sa ukladá pokrytie dát a rozhranie ukazuje počet prijatých/očakávaných sviečok pre aktuálny coin.
- do projektového plánu pridaná fáza účtov účastníkov: prihlásenie, vlastné menu a oddelené stratégie.

### Najbližšie kroky

1. Otestovať importér s existujúcou Freqtrade databázou po lokálnom spustení API.
2. Pripojiť Freqtrade backtest a ukladať výsledky k verzii stratégie.
3. Zaviesť Supabase Authentication a účty účastníkov.
4. Oddeliť v databáze stratégie, coinové menu, simulácie a backtesty podľa prihláseného účastníka.
5. Po vytvorení Supabase projektu doplniť jeho adresu a potrebné kľúče do `.env`.

### Bezpečnostný stav

- Reálne obchodovanie: vypnuté.
- Kľúče burzy: nepoužívajú sa.
- Počiatočný simulačný kapitál: 100 USDT.
- Odporúčaný limit: jedna otvorená pozícia a najviac 10 vstupov za deň.

## AI optimization agent
- Added bounded search across every supported coin and 1m/3m/5m/15m candles.
- Added 70/30 train-validation split, transaction-cost reserve, profit/drawdown/stability scoring and saved jobs.
- Added a dedicated UI result panel with progress, best parameters and copyable Freqtrade strategy.
- Fixed rebound thresholds so the simulator now enforces the configured minimum and maximum reversal.
- Frontend build and Python compile/import checks pass.

