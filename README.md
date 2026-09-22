# Revoltis Bot Strategy

Webové rozhranie pre **simulačný** Freqtrade bot. Umožňuje vybrať coiny a meniť všetky parametre stratégie: sviečky, vstup po poklese, RSI, Bollinger Bands, ATR, volume filter, potvrdenie otočenia, stop-loss, trailing profit, kapitál a denné limity. Neobsahuje prístup k burze ani live trading.

Aplikácia funguje samostatne: načítava verejné spotové OHLCV sviečky z Binance a vlastné FastAPI jadro nad nimi spúšťa simuláciu. Freqtrade už nie je potrebný na načítanie dát ani na spustenie simulácie.

Každá dokončená simulácia uloží priebeh hodnoty portfólia, testované časové okno a výsledné metriky. V grafe možno priebeh zobraziť po hodinách, dňoch alebo týždňoch.

## Spustenie lokálne

1. V Supabase vytvor bezplatný projekt, otvor **SQL Editor** a vlož obsah súborov `supabase/migrations/001_revoltis_simulation.sql` a potom `supabase/migrations/002_analytics.sql`.
2. Ak chceš Supabase, skopíruj `.env.example` na `.env` a doplň `SUPABASE_URL` a `SUPABASE_SERVICE_ROLE_KEY`. Tento kľúč ostáva iba na serveri, nikdy vo fronte. Tento krok môžeš preskočiť a používať lokálny demo režim.
3. V tomto priečinku spusti `docker compose up --build`, potom otvor `http://localhost:8080`.

Bez `.env` API funguje v lokálnom demo režime: nastavenia, verzie a importované simulované výsledky sa ukladajú do `runtime_data/revoltis-demo.json`. Po pripojení Supabase sa tieto údaje ukladajú do databázy Supabase.

## Prepojenie s Freqtrade

Táto aplikácia je ovládací a hodnotiaci panel. Freqtrade ostáva samostatný **dry-run** proces. Ďalší krok je pridať synchronizáciu jeho databázy `revoltis-v5-dryrun.sqlite` do tabuľky `simulated_trades`; aplikácia je na túto tabuľku pripravená. Žiadne API kľúče Binance sa sem nevkladajú.

Synchronizácia je už pripravená v `scripts/sync_freqtrade.py`. Po spustení API ju spustíš napríklad takto:

```text
python scripts/sync_freqtrade.py --db "C:\cesta\k\revoltis-v5-dryrun.sqlite"
```

Ak je vyplnený `REVOLTIS_SYNC_TOKEN` v `.env`, zadaj ho aj do prostredia počítača alebo pridaj parameter `--token`. Tento token chráni iba prenos simulačných výsledkov; nie je to burzový kľúč.

## Backtest a porovnanie verzií

1. V Revoltis najprv ulož nastavenie ako verziu stratégie cez API `POST /api/strategy-versions`.
2. Spusť Freqtrade backtest v jeho samostatnom projekte a exportuj výsledok do JSON.
3. Ulož výsledok k verzii cez pripravený importér:

```text
python scripts/import_backtest.py --result "C:\cesta\backtest-result.json" --version "ID-VERZIE" --timerange "20260101-20260301"
```

Následne API `GET /api/backtests` vráti výsledky zoradené od najnovšieho. Každý výsledok ostáva spojený s konkrétnou verziou parametrov.

## Bezpečnostné hranice

- `simulation` režim je viditeľný v rozhraní aj API.
- Formulár nenastavuje burzové príkazy; ukladá iba parametre simulácie.
- Nastavenia obchodu obmedzia rozhranie na 1–5 otvorených pozícií a denný limit 1–100; odporúčaná hodnota je 1 pozícia a 10 obchodov denne.

## Overenie výpočtového jadra

```text
python -m unittest discover -s backend/tests -v
python scripts/stress_test_simulation.py
```

Historický test povinne uzatvorí otvorené pozície poslednou dostupnou cenou,
zahrnie obidve strany transakčných nákladov a počíta drawdown priebežne z
mark-to-market hodnoty. Optimalizátor neoznačí variant za overený, ak má menej
ako 5 validačných obchodov, záporný zisk/skóre alebo drawdown nad 15 %.
