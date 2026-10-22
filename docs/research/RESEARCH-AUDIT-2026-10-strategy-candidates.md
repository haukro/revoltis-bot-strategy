# NoFomo — audit, výskum a kandidátne stratégie (október 2026)

Status: **VÝSKUMNÝ DOKUMENT — nič nemení, nič nespúšťa**

Tento dokument nemení HYPE paper testy, Strategy B, ich nastavenia, databázu,
nákladový model ani produkčné nasadenie. Nespúšťa žiadny backtest ani
optimalizáciu a nesťahuje trhové dáta. Všetky čísla o NoFomo pochádzajú
z existujúcich dokumentov a kódu v repozitári (`main` @ `b200950` a vetva
`docs/strategy-d-and-hype-decision`). Odvodené čísla sú výslovne označené ako
**výpočet z dokumentovaných hodnôt**. Tvrdenia o ziskovosti, ktoré nemajú
merateľný podklad, sú označené ako **HYPOTÉZA**.

---

## 0. Zhrnutie na jednu obrazovku

1. **Problém NoFomo nie je v indikátoroch, ale v ekonomike frekvencie.**
   Strategy A mala podľa dokumentovaných nákladov hrubú výhodu približne
   **+0,12 % na obchod**, ale náklady na celý obchod (round-trip) boli
   **0,217 %**. Pri ~46 obchodoch mesačne a vklade 50 USDT na obchod to
   predstavuje **~60 % kapitálu ročne len na nákladoch**. Žiadny 5m signál
   s takouto hrubou výhodou nemôže prežiť taker poplatky na retail úrovni.
2. **BTC TSMOM C nemal ani hrubú výhodu** (~-0,03 % na obchod pred nákladmi).
   Problém nebol len v poplatkoch, intradenný breakout na BTC nič nepredpovedal.
3. **HYPE v8 (interim 18 obchodov):** priemer -0,41 % na obchod po nákladoch
   a ~-0,21 % pred nákladmi. 95 % interval je približne [-0,95 %; +0,13 %].
   Ani 30 obchodov nebude stačiť na dôkaz výhody. Môžu stačiť nanajvýš na
   vyvrátenie hypotézy.
4. **Najsilnejšie dôkazy v literatúre** existujú pre pomalý *time-series
   momentum / trend* s riadením podľa volatility. Platí to na futures
   (Moskowitz–Ooi–Pedersen 2012, Hurst–Ooi–Pedersen 2017) aj na krypto
   (Liu–Tsyvinski–Wu 2022, Han–Kang–Ryu WP, Zarattini–Pagani–Barbon 2025 WP).
   Ide o denné až týždenné horizonty s nízkym obratom, teda presne opak
   doterajších 5m pokusov.
5. **Odporúčaný prvý experiment:** dlhý historický test (2018 až 2025)
   pevne predpísaného dlhodobého (long-only) trend ensemble na BTC/ETH
   s volatilitným škálovaním. Parametre sa vezmú z literatúry a nebudú sa ladiť.
   **Povinným benchmarkom** je volatility-targeted buy-and-hold. Ak trendový
   signál neprekoná samotné škálovanie volatility, hypotéza padá.
6. **Šesťmesačný forward test (ako v draft Strategy D) nemá štatistickú
   silu.** Pri ročnom Sharpe 1,0 treba na t ≈ 1,96 približne **3,8 roka**
   dát (výpočet: (1,96/SR)²). Forward test preto môže slúžiť len ako kontrola
   implementácie a nákladov, nie ako dôkaz výhody. Dôkaz musí niesť dlhá história
   s vopred pevnými pravidlami.

---

## 1. Zhodnotenie súčasného NoFomo

### 1.1 Čo je naozaj implementované

| Komponent | Súbor | Stav | Poznámka |
|---|---|---|---|
| 5m mean-reversion simulátor (BB + RSI + rebound + SL/trailing/timeout) | `backend/app/simulation.py` | implementované, beží | jadro HYPE v8 aj Strategy A |
| Nákladový model z aktuálnej knihy príkazov (fee + polovica spreadu + dopad) | `backend/app/costs.py` | implementované | kvalitný základ, ale ide o snímku, nie o historické L2 |
| Optimalizátor (náhodné vzorky z mriežky, 5 WF okien + 20 % holdout, policy v4) | `backend/app/optimizer.py`, `main.py` | implementované | pozri riziká v 1.4 |
| Auto Market Scanner (výber trhov z celého OKX USDT universe) | `main.py` ~2100–2204 | implementované | zdroj selekčného skreslenia |
| Strategy B (ZEC 1h breakout N=24, 2×ATR chandelier, long+short) | `backend/app/tsmom_b_v1.py` | implementované, **zmrazený forward fold do 2026-12-23** | nemenené |
| Strategy C (rovnaký predpis na BTC) | `backend/app/tsmom_c_v1.py` | implementované, **CLOSED FAIL** | |
| Paper execution, risk core, kill switch, rekonciliácia (SPEC-004/005/006) | `paper_execution.py`, `paper_ops.py`, `spec006_adapter.py`, migrácie 006–027 | implementované | dobrá znovupoužiteľná infraštruktúra |
| HYPE v8 Official 100/50 a Scale 300/150 paper forward | `frontend/src/main.tsx` + `/api/simulations/run` | **beží** | pozri 1.2 |
| Append-only paper ledger | vetva `claude/pokrtacujme-appke-a3o8m0` (migrácie 028/029) | **nezlúčené** | |
| Strategy D (BTC/ETH denný long-only trend, vol-targeting) | `docs/TEST-SPEC-004-…` na vetve `docs/strategy-d-and-hype-decision` | **iba návrh R2**, nič neimplementované | |
| Freqtrade integrácia | `scripts/sync_freqtrade.py`, `import_backtest.py` | len import, nepoužíva sa | |

Backend testy: **151 passed** (lokálne overené, `python -m pytest -q`).

### 1.2 Aká stratégia beží v paper testoch

V oboch paper testoch beží **HYPE/USDT 5m v8**. Ide o mean-reversion: close
pod spodným Bollingerovým pásmom, RSI v prepredanej zóne, potvrdenie otočenia
a rebound v pásme. Výstupy tvorí stop-loss, trailing profit a max-no-trail
timeout. Testy majú kapitál a vklad 100/50 a 300/150 USDT s nákladovým modelom
`current_okx_book`.

**Dôležité zistenie o povahe „paper forward“:** nejde o paper obchodovanie
na úrovni príkazov. Frontend periodicky volá `/api/simulations/run`
s `trading_start_time` a `use_current_cost_model=true`. Backend:

- znova stiahne sviečky od začiatku testu,
- **znova načíta aktuálnu knihu príkazov** (`load_pair_cost_model`,
  `main.py` ~2233),
- prepočíta celú históriu cez `simulate()`.

Dôsledky:

1. Náklady minulých obchodov sa menia s každým prepočtom. História teda
   nie je nemenná, čo potvrdzuje aj dokument
   `preregistered-hype-v8-paper-decision.md`.
2. Zmrazené nastavenia a čas štartu sú uložené v **localStorage prehliadača**
   (`nofomo-paper-forward-v1`, `nofomo-paper-scale-v1`, `main.tsx` 85–89).
   Server nie je autoritatívnym zdrojom.
3. Ide o „forward-walk backtest na živých sviečkach“, nie o paper trading
   s modelovaním latencie, plnenia, minimálnych objemov a odmietnutí.
   SPEC-004/005/006 infraštruktúra na to existuje, ale HYPE ju nepoužíva.

### 1.3 Silné a slabé stránky HYPE v8, Strategy B a Strategy D

**HYPE v8 (5m mean reversion):**

| | |
|---|---|
| Silné | Jednoduchá logika, náklady zo skutočnej knihy, vopred zapísané pravidlo PASS/FAIL (PF ≥ 1,10, expectancy > 0, DD ≤ 15 %, ≥ 30 obchodov + 30 dní). |
| Slabé | Pomer výhry k strate 0,48 vyžaduje 67,5 % úspešnosť (dosiahnutá 50 %). Výber cez scanner a optimalizátor z mnohých trhov a variantov znamená silný winner's curse. Rodina 5m BB/RSI/rebound už zlyhala na ZEC (Strategy A). Pri ~1 obchode denne a 50 % kapitálu na obchod predstavujú náklady ~40 % kapitálu ročne (výpočet: 365 × 0,22 % × 0,5). |
| Interim (z dokumentu, nie nové overenie) | 18 obchodov, PnL -3,673 USDT, expectancy -0,204 USDT = **-0,41 % na obchod**. Odhad pred nákladmi je ~**-0,21 %**. Štatistický interval: SE ≈ 0,27 % → 95 % CI ≈ [-0,95 %; +0,13 %] (**výpočet z dokumentovaných hodnôt**, smerodajná odchýlka aproximovaná z avg win/loss). |
| Verdikt | **Neočakávam PASS.** Rozhodnutie však patrí až do vopred určeného termínu. Nič nemeniť, nezastavovať. |

**Strategy B (ZEC TSMOM, zmrazený forward):**

| | |
|---|---|
| Silné | Výborná metodická disciplína: zámok pred prvým behom, certifikát kontinuity dát, žiadny look-ahead pri chandelier stope v rámci sviečky (5.3a), oddelené náklady pre long a short. |
| Slabé | (1) **Shorty na OKX spote nie sú vykonateľné**. Aj PASS by bol výskumný verdikt pre polovicu stratégie (spec to sama priznáva). (2) Identický predpis na BTC (C) nemal ani hrubú výhodu (~-0,03 % na obchod). (3) ZEC prešiel pre-screenom idiosynkratického podielu len o 0,9 p. b. a blok C bol pod prahom. (4) 1h breakout s 2×ATR stopom má krátke držanie (~7,5 h pri C), takže náklady sú stále významné. |
| Verdikt | Nechať dobehnúť bez zásahu. Pravdepodobnosť ekonomicky použiteľného výsledku na spote je **nízka**. |

**Strategy D (BTC/ETH denný long-only trend, návrh R2):**

| | |
|---|---|
| Silné | Mechanizmus má najsilnejšie dôkazy v literatúre. Nízky obrat, long-only, bez páky, a teda realizovateľné na spote. Výborne vyriešená časová kauzalita (signál v nedeľu, exekúcia v utorok). Nákladový model zahŕňa aj lot rounding a rezervu na poplatky. |
| Slabé | (1) BTC a ETH majú koreláciu ~0,88 (z dokumentu o pre-screene), takže prakticky ide o **jednu stávku**. (2) **6-mesačný forward nemá štatistickú silu** (pozri 0.6). D to čiastočne rieši cez DSR a bootstrap, ale pri ~180 denných pozorovaniach ani dobrá stratégia nedosiahne signifikanciu. Vysoké riziko výsledku INSUFFICIENT/FAIL aj pri skutočnej výhode. (3) Ťažký schvaľovací proces (R2, Grok/Gemini kolá) skôr oneskoruje získanie dát. (4) Chýba **benchmark volatility-scaled buy-and-hold**, ktorý by oddelil prínos trendového signálu od prínosu samotného škálovania (Kim–Tse–Wald kritika TSMOM). |
| Verdikt | **Správny smer.** Odporúčam ho upraviť: hlavný dôkaz z dlhej histórie s pevným, literatúrou daným predpisom a forward iba ako kontrola implementácie (kap. 5–7). |

### 1.4 Poplatky, spread, slippage, likvidita, stop-loss, veľkosť pozície

| Oblasť | Stav v kóde | Hodnotenie |
|---|---|---|
| Poplatky | `costs.py`: taker 0,10 %, maker 0,08 % (OKX regular). Pri cost modeli `net_return = R·(1−exit)/(1+entry)−1`. | ✅ správne a multiplikatívne. Fallback bez cost modelu používa `2 × fee` (0,2 %) bez spreadu. |
| Spread a dopad | Prechod cez aktuálnu L2 knihu pre zadanú hodnotu (notional). | ✅ dobrý odhad pre malé objednávky. ⚠️ Ide o **jednu snímku** aplikovanú na celú históriu, takže v čase stresu (keď stratégie typu MR vstupujú) je spread systematicky podhodnotený. |
| Slippage a latencia | Nemodelované okrem dopadu na knihu. | ⚠️ Vstup sa plní **na close tej istej sviečky**, ktorá dala signál (`simulation.py`: vstup = `close` aktuálnej sviečky). Reálne by to bol next-open plus latencia. Pri 5m MR, ktorý kupuje odraz, je to mierne optimistické. |
| Stop-loss | Kontroluje sa **iba na close** 5m sviečky (`close <= entry·(1−SL)`). | ⚠️ Knôt cez stop, po ktorom cena zavrie nad stopom, sa nepočíta ako výstup (optimistické). Close hlboko pod stopom sa plní na close (pesimistické). Reálny stop príkaz sa správa inak. Strategy B/C to rieši správne (open-gap a intrabar logika). |
| Trailing | High watermark vrátane aktuálnej sviečky, potom porovnanie s close. | OK, close je posledný bod sviečky. |
| Veľkosť pozície | Pevná suma `min(stake, cash)`, 1 pozícia. | ⚠️ Nerieši sa lot size ani minimálny objem pre OKX inštrument. 50 % kapitálu na obchod znamená koncentrované riziko na jeden alt. |
| Likvidita | Scanner filtruje podľa objemu a spreadu. `thin_L1` príznak v cost modeli. | ✅ pre 50–150 USDT je likvidita na HYPE a hlavných pároch dostatočná. |
| Účtovanie | Marked equity s nákladmi na jednu stranu, DD z udalostí. | ✅ korektné. ⚠️ Pole `raw.net_return_percent` sa pri legacy ceste počíta s `2×fee`. Pri ceste s cost modelom ho prepíše `cost_log` (konzistentné). |

### 1.5 Look-ahead, overfitting a účtovanie obchodov

- **Look-ahead v simulátore:** priamy look-ahead som nenašiel. Signál používa
  `previous["close"] < lower_band`, kde pásmo obsahuje aktuálnu sviečku. Ide
  o miernu nekonzistenciu (pásmo t vs. close t−1), nie o look-ahead. Vstup na
  close t je na hranici: signál vzniká po uzavretí sviečky t a plnenie na tej
  istej cene predpokladá nulovú latenciu.
- **Overfitting (najväčšie riziko):** `SEARCH_SPACE` má 14 parametrov × 3
  hodnoty = **3¹⁴ ≈ 4,8 mil. kombinácií**. Optimalizátor k tomu vzorkuje až 8
  variantov × 4 timeframy × až 20 párov, ktoré predtým vybral scanner
  **z celého OKX universe podľa nedávnej volatility a BB crossov**. Holdout
  tvorí 20 % z okna s dĺžkou 7–90 dní. Efektívny počet pokusov je teda
  rádovo stovky až tisíce a nikde sa nepočíta PBO ani deflated Sharpe.
  Policy v4 (≥ 40 OOS obchodov, 3/5 ziskových okien, holdout bez opakovania)
  je slušná, ale **nekoriguje viacnásobné testovanie naprieč trhmi a behmi**.
  HYPE v8 je presne typ víťaza, pri ktorom treba čakať regresiu k nule.
- **Kontaminácia:** ZEC bloky A/B/C sú už preskúmané (Strategy A, pre-screen,
  rebound filter). BTC jeseň 2025 je spotrebovaná na C. Nové výskumné vetvy
  nesmú tieto okná použiť ako „nedotknuté“.
- **Účtovanie:** HYPE paper prepočíta náklady pri každom behu (1.2). Ledger
  vetva to rieši, ale nie je zlúčená.

### 1.6 Čo sa dá opätovne použiť

| Komponent | Použitie v novom výskume |
|---|---|
| `costs.book_costs`, `costs.net_return` | Náklady na exekúciu pre denný a týždenný rebalans. Pridá sa iba stresový násobok. |
| `tsmom_b_v1.pack_ohlc_snapshot` / `unpack_ohlc_snapshot`, `trade_audit.digest` | Nemenné snímky dát s hashom a certifikát kontinuity. |
| `simulation.payoff_statistics` | Jednotné metriky na úrovni obchodov. |
| SPEC-004/005/006 (`paper_execution.py`, `paper_ops.py`, migrácie) | Neskorší paper beh na úrovni príkazov s kill switchom a rekonciliáciou. |
| `docs/RESEARCH-REVIEW-PROTOCOL.md`, štruktúra TEST-SPEC | Šablóna preregistrácie. |

---

## 2. Open-source algoritmy: porovnanie

Licencie a fakty sú overené z GitHub stránok projektov (október 2026).
**Žiadny z projektov neposkytuje nezávisle overenú ziskovosť.** Autorské
backtesty nie sú dôkaz.

| Projekt | Odkaz | Licencia | Ekonomická logika | Testovanie a reprodukovateľnosť | Hodnotenie pre NoFomo |
|---|---|---|---|---|---|
| Freqtrade | [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | GPL-3.0 | Framework, nie stratégia | Backtest s poplatkami. Nástroje `lookahead-analysis` a `recursive-analysis` (overujú vplyv budúcich sviečok a dĺžky histórie na indikátory, podľa dokumentácie, ktorú som v tejto session nevedel načítať). | **Myšlienku `lookahead-analysis` prevziať** do vlastných testov: posunúť koniec dát a overiť, že sa minulé signály nemenia. |
| freqtrade-strategies | [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) | GPL-3.0 | Zbierka indikátorových stratégií (napr. `Strategy001`) | README výslovne: „for educational purposes only“ a výsledky závisia od párov, timeframu a obdobia. | Nemajú ekonomický mechanizmus ani nezávislé testy. **Nepoužiť ako zdroj výhody.** |
| NostalgiaForInfinity | [iterativv/NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity) | GPL-3.0 | 5m dip-buying s desiatkami podmienok, 40–80 párov, 6–12 otvorených obchodov | Výsledky iba v komentároch commitov, nepretržité ladenie (X…X8). | Ide o **extrémny príklad preučenia** na krátkom timeframe (stovky ručne ladených podmienok). Je v rovnakej rodine ako uzavretá Strategy A a HYPE. **Nepoužiť.** |
| Jesse | [jesse-ai/jesse](https://github.com/jesse-ai/jesse) | MIT | Framework. Multi-timeframe backtest bez look-ahead, Optuna optimalizácia | Dobrá architektúra (sviečky sa skladajú z 1m bez look-ahead) | Architektonická inšpirácia pre multi-timeframe. Optuna optimalizáciu **nepoužiť** bez PBO. |
| Hummingbot | [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot) | Apache-2.0 | Market making (pure MM, Avellaneda–Stoikov), XEMM, arbitráž, position executor s triple barrier. OKX spot konektor existuje. | Vykonávací rámec, nie dôkaz ziskovosti | Maker stratégie pri retail poplatkoch (0,08 % maker bez rebate) a 50–150 USDT sú ekonomicky slabé (kap. 5). **Triple-barrier executor** je dobrý vzor pre výstupy. |
| QuantConnect LEAN | [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | Apache-2.0 | Framework (C#/Python), modulárne fee, slippage a fill modely | Príklady v `Algorithm.Python` | Vzor pre **oddelenie fill modelu od stratégie**. Prevziať koncept, nie kód. |
| Zarattini–Pagani–Barbon (2025) | [autorská stránka](https://abarbon.com/papers/catching-crypto-trends), [SFI WP rp2580](https://ideas.repec.org/p/chf/rpseri/rp2580.html) | článok (nie kód) | Ensemble Donchian kanálov s rôznymi lookbackmi, vol-sizing, top-20 likvidných coinov | Autori uvádzajú Sharpe > 1,5 a alfu 10,8 % p.a. voči BTC po poplatkoch na dátach bez survivorship biasu. **Nezávisle neoverené.** | **Najbližší publikovaný predpis** k odporúčanému kandidátovi 1. Použiť ako zdroj predpisu, nie ako dôkaz. |

**Záver kapitoly 2:** z populárnych open-source stratégií nemá žiadna
dokumentovaný ekonomický mechanizmus s nezávislým testom. Hodnota týchto
projektov je v **infraštruktúre** (look-ahead testy, fill modely,
triple-barrier výstupy), nie v alfe.

---

## 3. Vedecké štúdie

Stĺpec „Overenie“: **DOI ✓** = DOI overené v aspoň jednom bibliografickom
zdroji (RePEc, NBER, vydavateľ). **WP** = pracovný dokument bez recenzie.
**DOI ?** = DOI nebolo v tejto session overené.

| # | Štúdia | Overenie | Trh a dáta | Hlavné zistenie | Obmedzenie pre OKX spot |
|---|---|---|---|---|---|
| 1 | Moskowitz, Ooi, Pedersen (2012), *Time series momentum*, JFE 104:228–250 | DOI ✓ [10.1016/j.jfineco.2011.11.003](https://doi.org/10.1016/j.jfineco.2011.11.003) | 58 futures (akcie, meny, komodity, dlhopisy) | Návratnosti 1–12 mesiacov pretrvávajú, potom sa čiastočne otáčajú | Futures, long/short, mesačný horizont, nie krypto |
| 2 | Hurst, Ooi, Pedersen (2017), *A Century of Evidence on Trend-Following Investing*, JPM 44(1):15–29 | DOI ? | 67 trhov, 1880–2016 | Trend funguje naprieč storočím a režimami | Futures, diverzifikácia cez desiatky trhov, ktorú krypto nemá |
| 3 | Kim, Tse, Wald (2016), *Time series momentum and volatility scaling*, J. Financial Markets | DOI ? | futures z MOP | Veľká časť TSMOM výsledku pochádza z **volatility scaling**, nie zo samotného signálu | **Kľúčový falzifikačný test**: signál musí poraziť vol-scaled B&H |
| 4 | Moreira, Muir (2017), *Volatility-Managed Portfolios*, JF 72(4):1611–1644 | DOI ✓ [10.1111/jofi.12513](https://doi.org/10.1111/jofi.12513) | akciové faktory | Znižovanie expozície pri vysokej volatilite zlepšuje Sharpe | Akcie. Neskoršia kritika (DeMiguel a kol. 2024, JF) ukazuje slabšie OOS výsledky |
| 5 | Liu, Tsyvinski, Wu (2022), *Common Risk Factors in Cryptocurrency*, JF 77:1133–1177 | DOI ✓ [10.1111/jofi.13119](https://doi.org/10.1111/jofi.13119) | prierez krypto, týždenné dáta | Tri faktory: trh, veľkosť, **momentum** | Long-short prierez, týždenný rebalans, malé coiny sú drahé na exekúciu |
| 6 | Grobys, Sapkota (2019), *Cryptocurrencies and momentum*, Econ. Letters 180:6–10 | DOI ✓ [10.1016/j.econlet.2019.03.028](https://doi.org/10.1016/j.econlet.2019.03.028) | 143 coinov, 2014–2018 | **Žiadne signifikantné cross-sectional momentum** | Protidôkaz k #5. Výsledok závisí od universa a obdobia |
| 7 | Han, Kang, Ryu (WP), *Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market* | WP (AUT/SKKU) | krypto | TSMOM je silné, XS momentum slabé. **Po nákladoch a dennom kolísaní mnohé portfóliá zaniknú.** Porazené coiny sa často odrážajú. | Nerecenzované. Presné čísla po nákladoch som neoveril |
| 8 | Zarattini, Pagani, Barbon (2025), *Catching Crypto Trends*, SFI WP rp2580 | WP | BTC + krypto bez survivorship biasu, 2015–2025 | Donchian ensemble + vol-sizing: Sharpe > 1,5 po poplatkoch (údaj autorov) | Nerecenzované, rotácia top-20 (vyšší obrat), autorský backtest |
| 9 | Shen, Urquhart, Wang (2022), *Bitcoin intraday time-series momentum*, Financial Review 57(2) | DOI ? | BTC intraday | Prvá polhodina predpovedá poslednú (podľa objemového „času“) | Intradenné výnosy sú malé voči 0,2–0,25 % round-trip. Vysoké riziko, že výhodu zjedia náklady |
| 10 | Makarov, Schoar (2020), *Trading and arbitrage in cryptocurrency markets*, JFE 135(2):293–319 | DOI ✓ [10.1016/j.jfineco.2019.07.001](https://doi.org/10.1016/j.jfineco.2019.07.001) | BTC naprieč burzami | Arbitrážne rozdiely sú väčšie medzi krajinami. Spoločný signovaný objem vysvetľuje ~80 % výnosov BTC | Arbitráž vyžaduje kapitál na viacerých burzách a presuny. Pre 100 USDT nerealizovateľné |
| 11 | Cont, Kukanov, Stoikov (2014), *The Price Impact of Order Book Events*, J. Fin. Econometrics 12(1):47–88 | DOI ✓ [10.1093/jfinec/nbt003](https://doi.org/10.1093/jfinec/nbt003) | NYSE, 50 akcií | Order flow imbalance **súčasne** lineárne vysvetľuje zmeny ceny | Vzťah je hlavne **súčasný**, nie prediktívny na horizonte, ktorý pokryje 0,2 % náklady. Vyžaduje L2 stream |
| 12 | Albers, Cucuringu, Howison, Shestopaloff (2025), *The Market Maker's Dilemma*, arXiv 2502.18625 | preprint | Binance BTC perp, živé experimenty | **Negatívny vzťah medzi pravdepodobnosťou plnenia maker príkazu a výnosom po plnení** (adverse selection). Bežné MM stratégie boli vysoko stratové. | Perpetuals a nižšie poplatky. Na spote s 0,08 % maker bez rebate je to ešte horšie |
| 13 | Avellaneda, Stoikov (2008), *High-frequency trading in a limit order book*, Quant. Finance 8(3):217–224 | DOI ? | teoretický model | Optimálne kotovanie s ohľadom na inventár | Teória. Predpokladá exponenciálnu intenzitu plnenia |
| 14 | Novy-Marx, Velikov (2016), *A Taxonomy of Anomalies and Their Trading Costs*, RFS 29(1):104–147 | DOI ✓ [10.1093/rfs/hhv063](https://doi.org/10.1093/rfs/hhv063) | akciové anomálie | Stratégie s mesačným obratom < 50 % väčšinou prežijú náklady, s vyšším obratom málokedy. **Buy/hold pásmo** je najúčinnejšia jednoduchá úspora. | Akcie, ale princíp „obrat je nepriateľ“ je univerzálny |
| 15 | Bailey, Borwein, López de Prado, Zhu (2017), *The Probability of Backtest Overfitting*, J. Comput. Finance 20(4) | DOI ✓ [10.21314/JCF.2016.322](https://doi.org/10.21314/JCF.2016.322) | metodika (CSCV) | Odhad pravdepodobnosti, že víťaz z mriežky je preučený | Priamo aplikovateľné na NoFomo optimalizátor |
| 16 | Bailey, López de Prado (2014), *The Deflated Sharpe Ratio*, JPM 40(5):94–107 | DOI ✓ [10.3905/jpm.2014.40.5.094](https://doi.org/10.3905/jpm.2014.40.5.094) (už v Strategy D) | metodika | Korekcia Sharpe za počet pokusov a nenormalitu | Vyžaduje poctivý inventár pokusov |
| 17 | Harvey, Liu (2015), *Backtesting*, JPM (Fall 2015) | DOI ? (zdroje sa rozchádzajú v ročníku) | metodika | Haircut Sharpe za viacnásobné testovanie | |
| 18 | Lo (2002), *The Statistics of Sharpe Ratios*, FAJ 58(4):36–52 | DOI ✓ [10.2469/faj.v58.n4.2453](https://doi.org/10.2469/faj.v58.n4.2453) | metodika | Sériová korelácia skresľuje anualizovaný Sharpe | Dôležité pre denné krypto výnosy |
| 19 | Párové obchodovanie a kointegrácia v krypte (Leung–Nguyen 2019 a diplomové práce) | rôzne, väčšinou nerecenzované | denné aj intradenné páry | Výsledky po nákladoch sú zmiešané až negatívne | Vyžaduje short nohu, **nerealizovateľné na spote** |

**Rozlíšenie trhov:** #1–4, #11 a #14 sú akcie alebo futures a do krypta
prenášajú len mechanizmus, nie čísla. Krypto-špecifické dôkazy (#5–10, #12)
podporujú **TSMOM na dennej a týždennej frekvencii** a sú zmiešané
pre XS momentum. Intradenné a mikroštruktúrne prístupy narážajú na náklady
a adverse selection.

**Krypto vs. tradičné trhy:** obchoduje sa 24/7 (365 dní ročne, nie 252),
vysoká korelácia altov s BTC (ZEC–BTC 0,51, BTC–ETH 0,88 z pre-screenu),
volatilita 3–5× vyššia ako pri akciách, ťažké chvosty, štrukturálne zlomy
(2018, 2022), fragmentácia búrz a na spote nemožnosť shortu bez maržového
úveru.

---

## 4. Rebríček piatich kandidátov

Hodnotenie 1–5 (5 = najlepšie). Pri „citlivosti na poplatky“ a „riziku
overfittingu“ znamená 5 nízku citlivosť a nízke riziko.

| # | Kandidát | Mechanizmus | Dôkazy | Kód | Realizovateľnosť OKX spot | Citlivosť na poplatky | Riziko overfittingu | Technická náročnosť (5 = jednoduché) | Nízky kapitál | Nezávislosť od A/B/C/HYPE | **Spolu** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **K1** | Denný/týždenný long-only **trend ensemble** s vol-targetingom (BTC, ETH, prípadne SOL vopred) | TSMOM: oneskorená reakcia a stádovitosť + zhlukovanie volatility | 5 | 4 | 5 | 5 | 4 | 4 | 4 | 4 (C bol intradenný) | **39** |
| **K2** | Týždenná **relatívna sila (XS momentum)** long-only, top-k z vopred zadefinovaného likvidného koša, s BTC režimovým filtrom | XS momentum (Liu–Tsyvinski–Wu) | 3 (zmiešané) | 3 | 4 | 4 | 3 | 3 | 3 | 5 | **31** |
| **K3** | **Volatility-managed buy-and-hold** (BTC/ETH, škálovanie expozície podľa realizovanej volatility, bez trendového signálu) | Zhlukovanie volatility a nepriaznivý pomer výnos/riziko pri vysokej volatilite | 3 | 5 | 5 | 5 | 5 | 5 | 4 | 3 | **35** |
| K4 | Krátkodobý **reversal po extrémnom dennom poklese** (D1–D5 držanie) na likvidných coinoch | Prehnaná reakcia a odraz porazených (Han–Kang–Ryu) | 2 | 2 | 4 | 3 | 2 | 4 | 4 | 2 (príbuzné MR) | **25** |
| K5 | **Maker-based** krátkodobá likvidita alebo intradenný BTC momentum | Spread capture / intradenné momentum | 2 | 3 | 2 | 2 | 2 | 1 | 2 | 4 | **18** |

**Vyradené:** párový a štatistický arbitráž (vyžaduje short), funding/basis
carry (vyžaduje perpetuals, mimo spot), medziburzová arbitráž (kapitál
a presuny), ML modely na 5m (overfitting, žiadna výhoda pred nákladmi
v doterajších dátach).

**Poradie na hlbšiu prácu: K1, K3, K2.** K3 je zámerne vysoko, pretože
je to **minimálna nula, ktorú musí K1 prekonať**. Ak K1 neporazí K3,
trendový signál nemá hodnotu (Kim–Tse–Wald).

---

## 5. Ekonomické obmedzenia NoFomo (100/50 a 300/150 USDT, OKX spot)

### 5.1 Nákladová prekážka (výpočty z dokumentovaných hodnôt)

Round-trip náklad ≈ 0,20 % poplatky + 0,00–0,05 % polovičný spread a dopad
na hlavných pároch, čiže **0,20–0,25 %**.

| Stratégia | Obchody | Round-trip | Hrubá výhoda na obchod (odhad) | Čistá na obchod | Ročná nákladová záťaž ako % kapitálu |
|---|---|---|---|---|---|
| Strategy A (ZEC 5m MR) | 412 / 9 mes. | 0,217 % | **+0,12 %** | -0,10 % | **~60 %** (549 obch./rok × 0,217 % × 50 % kapitálu) |
| BTC C (1h TSMOM) | 92 / 3 mes. | 0,200 % | **-0,03 %** | -0,23 % | ~18 % |
| HYPE v8 interim | 18 / ~18 dní | ~0,2 % | **~-0,21 %** | -0,41 % | ~40 % |
| K1 (týždenný rebalans, 5 p. b. pásmo) | ~10–25 úprav/rok | 0,2–0,25 % na **zmenenú časť** | HYPOTÉZA | HYPOTÉZA | **~0,5–2 %** (odhad: ročný obrat 2–8× kapitálu × 0,1–0,125 % na stranu) |

**Pravidlo:** ročná nákladová záťaž sa rovná obratu × nákladom na jednu
stranu. Stratégia s očakávanou hrubou ročnou výhodou 10–20 % znesie nanajvýš
~2–5 % nákladov. To vylučuje akýkoľvek 5m alebo 1h systém s viac než ~1
obchodom týždenne pri 50 % kapitálu na obchod, pokiaľ nemá hrubú výhodu
nad ~0,5 % na obchod.

### 5.2 Pomôžu menej časté obchody, dlhšie držanie alebo maker exekúcia?

- **Menej obchodov a dlhšie držanie:** áno. Je to jediná páka, ktorá mení
  rád veľkosti (60 % → 1 % záťaže). Podporuje to Novy-Marx–Velikov.
- **Lepšie vstupy:** len ak zvýšia hrubú výhodu nad ~0,25 %. Doterajšie
  filtre (rebound, 24h low) to nedokázali (Filter B FAIL).
- **Maker exekúcia:** úspora poplatku je len **0,02 % na stranu** (0,10 → 0,08 %)
  plus polovičný spread (na BTC/ETH ~0). Pri pravdepodobnosti plnenia p < 1
  a adverse selection (Albers a kol. 2025: plnenia korelujú s nepriaznivým
  pohybom) je čistý prínos **neistý a malý**. Pre K1 (týždenné rebalansy) je
  rozumný postup **post-only limit na mid/bid s timeoutom (napr. 10 min)
  a potom fallback na taker**, modelovaný ako zmes:
  `náklad = p_fill·(maker + adverse_sel) + (1−p_fill)·(taker + half_spread + drift_počas_čakania)`.
  `p_fill` a `adverse_sel` treba **zmerať** v paper behu, nie predpokladať.
  Do historického testu ide konzervatívne **100 % taker**.
- **Nízky kapitál:** pri 100 USDT je aj 15 % ročne len 15 USDT. Zmysel tejto
  fázy je **overiť výhodu a proces**, nie zarábať. Minimálne objemy
  na OKX spote (BTC-USDT rádovo ~1 USDT, presné `minSz`/`lotSz` treba stiahnuť
  z `/api/v5/public/instruments` pred implementáciou) umožňujú K1 aj pri
  100 USDT. 5 p. b. pásmo znamená obchody ≥ 5 USDT.

---

## 6. Detailné návrhy troch najlepších kandidátov

Spoločné pre všetky tri:

- **Dáta:** OKX spot denné sviečky (UTC), BTC-USDT a ETH-USDT, s nemennou
  snímkou, hashom a certifikátom kontinuity (prevziať z `tsmom_b_v1`).
  Krížová kontrola na druhom zdroji (napr. verejné denné dáta inej burzy):
  pri odchýlke denných close > 1 % je deň INVALID.
- **Náklady:** `costs.book_costs` snímka pre zadanú hodnotu obchodu
  + **stresové scenáre 1×, 2× a 3×** (napr. 0,25 % → 0,5 % → 0,75 % na stranu).
- **Delenie (návrh):**
  - *Vývoj (DEV):* 2018-01-01 až 2021-12-31, iba na kontrolu implementácie
    a sanity. **Parametre sa neladia, sú dané literatúrou vopred.**
  - *Zapečatený OOS:* 2022-01-01 až 2025-12-31 (medvedí trh 2022 + býčí
    2023–2025), vyhodnotený **raz**.
  - *Forward:* od prvého pondelka po schválení, aspoň 6 mesiacov, iba ako
    kontrola implementácie a nákladov (nie ako PASS dôkaz, pozri 0.6).
- **Kontaminácia:** tím pozná priebeh cien BTC/ETH 2018–2025 a predpis
  je odvodený z publikácií, ktoré tieto obdobia použili (Zarattini 2025).
  OOS je preto **čiastočne kontaminovaný na úrovni literatúry**. Riešenie:
  (a) žiadne ladenie, (b) predpis zapísaný pred stiahnutím dát,
  (c) test robustnosti na susedných parametroch, (d) inventár všetkých
  pokusov do DSR (A, Filter B, pre-screen, B, C, HYPE optimizer behy, K1–K3).
- **Benchmarky:** buy-and-hold BTC, B&H 50/50 BTC/ETH, **K3 (vol-targeted
  B&H)** a hotovosť.
- **Metriky:** CAGR, ročná volatilita, Sharpe (denný, ×√365, s Lo korekciou
  na autokoreláciu), Sortino, max DD, Calmar, ročný obrat, počet obchodov,
  čistý PF na mesačnej báze, expectancy na rebalans, čas v trhu a **95 % CI
  cez stacionárny blokový bootstrap** (blok 20 dní, 10 000 replikácií,
  pevný seed).

### 6.1 K1 — Trend ensemble s vol-targetingom (BTC/ETH)

**Hypotéza (vopred, falzifikovateľná):** Long-only expozícia na BTC a ETH
riadená priemerom znamienok 20/60/120-denných výnosov a škálovaná na cieľovú
volatilitu dosahuje v zapečatenom OOS 2022–2025 po 2× nákladoch **vyšší
Sharpe a nižší max DD** než K3 (vol-targeted B&H s rovnakým cieľom
volatility). Rozdiel v Sharpe musí byť kladný v bootstrapovom 90 % intervale.

**Pravidlá (bez ladenia, zhodné so Strategy D tam, kde to dáva zmysel):**

- Signál v nedeľu po close (UTC), exekúcia v utorok na open (1 deň oneskorenia).
- `s_i = mean(sign(r20), sign(r60), sign(r120))`, `a_i = max(s_i, 0)`.
- BTC režimový filter: close ≤ SMA200 → všetko na 0.
- Volatilita: EWMA 60 dní, minimálna hodnota 10 %, cieľ 25 % ročne, cap 40 %
  na aktívum, súčet ≤ 80 %.
- Pásmo 5 p. b. (Novy-Marx–Velikov), zaokrúhlenie na lot, rezerva 0,5 %
  na poplatky.
- Žiadny stop-loss (výstup cez týždenný signál). Riziko riadi vol-targeting.

**Risk management:** maximálna expozícia 80 %, kill switch pri dennom
pohybe > 25 % alebo pri chýbajúcich dátach (prevziať SPEC-004 risk core).
Bez páky.

**PASS (všetko naraz, OOS 2022–2025):**
1. Čistý CAGR > 0 pri **2×** nákladoch.
2. Sharpe(K1) − Sharpe(K3) > 0 a dolná hranica 90 % bootstrap CI > 0.
3. Max DD(K1) ≤ 0,6 × max DD(B&H 50/50).
4. Ani jeden kalendárny rok s DD > 40 %.
5. **Robustnosť:** zo 6 susedných predpisov (lookbacky ×0,5 a ×2,
   cieľ volatility 20/30 %, pásmo 3/8 p. b.) má aspoň 5 kladný čistý
   CAGR. Nejde o výber víťaza, iba o kontrolu, že výsledok nestojí
   na jednom bode.
6. DSR > 0,90 s N = poctivý inventár pokusov.

**FAIL:** ktorákoľvek z podmienok 1–3 neplatí. Vtedy sa K1 uzatvára bez
záchranných variantov.
**INSUFFICIENT:** menej ako 150 rebalansov alebo chýbajúce dáta.

### 6.2 K3 — Volatility-managed buy-and-hold

**Hypotéza:** Expozícia `w = min(cap, cieľ_vol / realizovaná_vol_60d)`
na 50/50 BTC/ETH (týždenný rebalans, pásmo 5 p. b.) dosahuje v OOS
2022–2025 po 2× nákladoch vyšší Sharpe a **aspoň o 30 % nižší max DD**
než statický B&H 50/50 pri porovnateľnej priemernej expozícii.

**Pravidlá:** rovnaká volatilita, cap, pásmo, exekúcia a náklady ako K1,
iba bez trendového signálu a bez SMA200 filtra.

**PASS:** (1) Sharpe(K3) > Sharpe(B&H, škálovaný na rovnakú priemernú
expozíciu), dolná hranica 90 % CI > 0, (2) max DD ≤ 0,7 × max DD(B&H),
(3) čistý CAGR > 0 pri 2× nákladoch.

**Význam:** K3 je zároveň **benchmark pre K1**. Ak K3 prejde a K1 ho
neporazí, trendový signál je zbytočný a správnou „stratégiou“ je jednoduché
riadenie expozície.

### 6.3 K2 — Týždenné XS momentum (long-only, top-k)

**Hypotéza:** V **vopred pevnom koši** 10 likvidných OKX spot coinov
(zoznam zapísaný pred stiahnutím dát, iba coiny obchodované na OKX
k 2022-01-01, aby sa zmiernil survivorship bias) dosahuje držanie top-3
podľa 28-dňového výnosu (s vynechaním posledných 7 dní) s týždenným
rebalansom a BTC SMA200 filtrom v OOS 2022–2025 po 2× nákladoch vyšší
Sharpe ako rovnovážny kôš všetkých 10.

**Pravidlá:** rovnaké váhy 1/3 na top-3, pásmo „držať, kým coin neklesne
mimo top-5“ (buy/hold pásmo znižuje obrat), BTC pod SMA200 → hotovosť.
Žiadny scanner a žiadna zmena koša počas testu. Delisting sa rieši
predajom za poslednú cenu a ide do výsledku.

**PASS:** (1) Sharpe(K2) − Sharpe(rovnovážny kôš) > 0, dolná hranica
90 % CI > 0, (2) čistý CAGR > 0 pri 2× nákladoch, (3) ročný obrat
≤ 15× kapitálu, (4) výsledok nestojí na jednom coine: bez najlepšieho
coinu zostáva čistý CAGR > 0.

**Riziká:** Grobys–Sapkota nenašli XS momentum. Malé coiny majú vysoké
náklady. Pri 100 USDT ide o 3 × ~33 USDT pozície (minimálne objemy
treba overiť).

---

## 7. Odporúčanie prvého experimentu

**K1 spolu s K3 ako povinným benchmarkom v jednom behu** na dlhej histórii
s vopred zapísaným predpisom. Ide o **úpravu existujúceho draftu
Strategy D**, nie o novú paralelnú vetvu:

| Zmena oproti Strategy D R2 | Dôvod |
|---|---|
| Primárny dôkaz z histórie 2018–2025 s pevným predpisom (DEV 2018–21, zapečatený OOS 2022–25), forward iba ako kontrola implementácie | 6 mesiacov nemá štatistickú silu (pri SR 1 treba ~3,8 roka) |
| Pridať K3 benchmark (vol-targeted B&H) | Oddeliť prínos signálu od prínosu škálovania (Kim–Tse–Wald) |
| Stres 2× a 3× nákladov | Robustnosť voči podhodnoteniu spreadu |
| Kontrola susedných predpisov (bez výberu víťaza) | Odhalí krehkosť bez ladenia |
| Zachovať: časovanie Ne → Ut, pásmo 5 p. b., cap 40 %, rezervu 0,5 %, DSR | Sú správne navrhnuté |

Prečo K1:
- najsilnejšie dôkazy naprieč trhmi aj v krypte,
- nízky obrat, takže náklady nezohrávajú rozhodujúcu úlohu,
- realizovateľné na OKX spote bez shortu a páky,
- ekonomicky iný mechanizmus než A a HYPE (MR na 5m) aj B a C (intradenný breakout),
- jasný falzifikačný test proti K3.

---

## 8. Implementačný a validačný plán

### 8.1 Architektúra (izolovaná, nič existujúce sa nemení)

```
backend/research/                 # nový balík, žiadny import do main.py
  __init__.py
  data/okx_daily.py               # stiahnutie denných sviečok + snímka/hash (reuse pack_ohlc_snapshot)
  data/instruments.py             # minSz/lotSz/tickSz snímka z OKX public instruments
  data/continuity.py              # certifikát kontinuity (vzor tsmom_b_v1)
  costs_adapter.py                # obal nad costs.book_costs + stresový násobok
  engine/portfolio.py             # cieľové váhy -> objednávky (lot rounding, pásmo, rezerva, poradie sell->buy)
  engine/accounting.py            # mark-to-liquidation equity, DD z udalostí
  strategies/k1_trend_ensemble.py
  strategies/k3_vol_managed.py
  strategies/k2_xs_momentum.py
  eval/metrics.py                 # CAGR, Sharpe (Lo), Sortino, DD, turnover, monthly PF
  eval/bootstrap.py               # stacionárny blokový bootstrap, rozdiel Sharpe
  eval/dsr.py                     # Deflated Sharpe (reuse vzorca z TEST-SPEC-004)
  eval/pbo.py                     # CSCV PBO pre budúce kontroly optimalizátora
  eval/lookahead_check.py         # orezanie dát v čase t a overenie, že signály <= t sa nemenia
  report.py                       # JSON + Markdown report s hashmi vstupov
backend/tests/research/           # syntetické testy, žiadne sieťové volania
scripts/research_run.py           # CLI, zapisuje iba do runtime_data/research/<run_id>/
docs/research/TEST-SPEC-005-k1-k3-historical.md   # preregistrácia pred stiahnutím dát
```

Zásady:
- Žiadny zápis do Supabase, žiadny endpoint, žiadna zmena `simulation.py`,
  `costs.py` (iba import), `tsmom_b_v1.py`, `main.py` ani frontendu.
- Výstup tvorí nemenný adresár behu s hashom kódu (git SHA), dát a nákladov.
- Každý beh sa zapíše do inventára pokusov (vstup pre DSR).

### 8.2 Postup

| Krok | Výstup | Schválenie |
|---|---|---|
| 1 | Preregistrácia `TEST-SPEC-005` (predpis K1/K3, delenie, PASS/FAIL, inventár pokusov) | Roman: zamknutie spec |
| 2 | Implementácia `backend/research/*` + syntetické testy (look-ahead, lot rounding, náklady, pásmo, DD) | Roman: povolenie písať kód |
| 3 | Stiahnutie dát a certifikát kontinuity (bez výpočtu PnL) | Roman: povolenie stiahnuť dáta |
| 4 | DEV beh 2018–2021: iba kontrola implementácie a sanity (bez zmien predpisu) | |
| 5 | **Jednorazový** OOS beh 2022–2025 + stres + susedné predpisy + bootstrap + DSR | Roman: povolenie oficiálneho behu |
| 6 | Verdikt PASS/FAIL/INSUFFICIENT zapísaný do `docs/research/result-…` | |
| 7 | Iba pri PASS: paper beh na úrovni príkazov cez SPEC-004/006 infra (meranie `p_fill`, reálnych nákladov, latencie), min. 6 mesiacov | samostatné schválenie |

### 8.3 Kontroly, ktoré doteraz chýbajú a mali by sa pridať

1. **Lookahead check:** pre náhodné t orezať dáta na t a overiť, že všetky
   signály a cieľové váhy do t sú identické.
2. **Recursive check:** indikátory sa nesmú meniť podľa dĺžky načítanej
   histórie (EWMA inicializácia).
3. **Placebo:** náhodne posunuté signály (o 26+ týždňov) a náhodné
   znamienka. Skutočný predpis musí byť nad 95. percentilom placeba.
4. **PBO (CSCV)** pre existujúci optimalizátor ako diagnostika. Policy v4
   sa tým nemení.

---

## 9. Záverečná odpoveď

**Ak by som staval kryptomenový bot od nuly s týmito obmedzeniami (OKX spot,
100–300 USDT, ~0,10 % taker), aký mechanizmus by som vybral?**

**Pomalý long-only trend/momentum s riadením expozície podľa volatility
na malom koši najlikvidnejších coinov, s týždenným rozhodovaním
a buy/hold pásmom.** Ekonomická logika: oneskorená reakcia investorov,
stádovitosť a zhlukovanie volatility sú zdokumentované naprieč storočím
na futures aj v krypte. Pri ročnom obrate niekoľkých násobkov kapitálu
klesnú náklady z ~40–60 % ročne (súčasné 5m systémy) na ~1–2 %. Na spote
je to vykonateľné bez shortu a páky a výstupy sú jednoduché a auditovateľné.
Toto je **HYPOTÉZA**, nie dokázaná ziskovosť.

**Akými dôkazmi by som sa ju najskôr pokúsil vyvrátiť (v tomto poradí):**

1. **Porazí signál samotné škálovanie volatility?** Rozdiel Sharpe
   K1 − K3 v OOS 2022–2025 s bootstrap CI. Ak nie, trendový signál je
   ilúzia (Kim–Tse–Wald) a hypotéza padá.
2. **Prežije 2× a 3× náklady?** Ak výsledok zmizne pri 0,5 % na stranu,
   výhoda je príliš tenká pre reálnu exekúciu.
3. **Je robustný v okolí parametrov?** Ak funguje len jeden lookback
   alebo cieľ volatility a susedné nie, ide o preučenie, nie o mechanizmus.
4. **Funguje v oboch režimoch?** Medvedí rok 2022 musí ukázať výrazne nižší
   DD než B&H. Inak stratégia len kopíruje beta.
5. **Prekoná placebo?** Náhodne posunuté signály nesmú dávať porovnateľné
   výsledky.
6. **Prežije DSR s poctivým inventárom všetkých doterajších pokusov
   NoFomo?** Ak nie, ide pravdepodobne o šťastie medzi mnohými pokusmi.
7. **Zhodujú sa výsledky naprieč zdrojmi dát?** OKX vs. iný zdroj
   denných close.

Ak hypotéza prežije všetkých 7 pokusov o vyvrátenie, stojí za paper beh
na úrovni príkazov. Ak nie, správnym výsledkom je **nekupovať zložitosť**
a zostať pri jednoduchom riadení expozície (K3) alebo pri DCA.

---

## Zdroje

Open-source:
- [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade)
- [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies)
- [iterativv/NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity)
- [jesse-ai/jesse](https://github.com/jesse-ai/jesse)
- [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot)
- [QuantConnect/Lean](https://github.com/QuantConnect/Lean)

Štúdie (odkazy v tabuľke kap. 3), doplnkové:
- [Liu, Tsyvinski, Wu — SSRN](https://papers.ssrn.com/abstract=3379131)
- [Zarattini, Pagani, Barbon — Catching Crypto Trends](https://abarbon.com/papers/catching-crypto-trends)
- [Han, Kang, Ryu — WP (AUT)](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
- [Shen, Urquhart, Wang — Bitcoin intraday TSMOM](https://research.birmingham.ac.uk/en/publications/bitcoin-intraday-time-series-momentum/)
- [Albers a kol. — The Market Maker's Dilemma (arXiv 2502.18625)](https://arxiv.org/abs/2502.18625)
- [Bailey a kol. — PBO (SSRN)](https://papers.ssrn.com/abstract=2326253)
- [Hurst, Ooi, Pedersen — AQR](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing)
- [Cont, Kukanov, Stoikov — arXiv 1011.6402](https://arxiv.org/abs/1011.6402)
- [Avellaneda, Stoikov — PDF](https://math.nyu.edu/%7Eavellane/HighFrequencyTrading.pdf)
- [Novy-Marx, Velikov — NBER w20721](https://www.nber.org/papers/w20721)

Poplatky OKX (0,08 % maker / 0,10 % taker, regular) sú zhodné s `costs.py`.
Pred implementáciou ich treba overiť na oficiálnej stránke OKX, pretože som
ju v tejto session nenačítal.
