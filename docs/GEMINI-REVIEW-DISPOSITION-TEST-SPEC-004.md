# Gemini Review Disposition — PR70 / TEST-SPEC-004 Strategy D v1

Status: **ROADMAP REVIEW PROCESSED — D DRAFT, METHODOLOGY BLOCKERS OPEN**
Date: 2026-10-14 Europe/Bratislava.
Consistency-check baseline: `d4895dafefc78719548759d1f96cc04952a7ac6b`.
Source: Gemini response returned by Roman in chat, reproduced below with
its supplied wording (including Roman's inline attribution).

The returned response contains no reviewed SHA. Its scanner complaint and line
numbers describe the initial proposal, superseded by the Grok update at this
baseline. They are checked against current documents, not assumed to prove a
review of the current revision. No external-account login or submission occurred.

## ACCEPTED

| Point | Disposition | Documentation action / evidence | Remaining limit |
|---|---|---|---|
| Suitable draft D family | ACCEPTED | Add daily long-only multi-horizon spot-basket hypothesis to the roadmap as an appropriate draft research candidate, distinct from another C-prescription coin replication. | Suitable research design is not profitable alpha, final lock or methodology clearance. |
| M3 Spot-vs-Futures | ACCEPTED | Roadmap example now includes spot or futures; D remains strictly spot-only, no shorts/leverage/perpetuals. Spec links the explicit scope resolution. | No runtime instrument/product change or trading approval. |
| M6 terminal gate | ACCEPTED | Roadmap explicitly keeps HYPE running, no terminal verdict, own30days AND30trades; references the existing first-observation/accounting requirements. | Interim panel/document figures remain observations. The review triggers no read of new paper results or simulation. |
| Workstream naming/isolation | ACCEPTED | Distinguish structural metadata/archive repair from trading Strategy B; separate runner/version/run/dataset/cost identities and behavior isolation. | No shared code changed and no B official forward metric inspected. |
| Explicit staged permissions | ACCEPTED | Roadmap stage table separates docs, future repair, final lock, implementation/synthetic checks, data-only certification and one-shot evaluation. | Every later execution stage remains unauthorized by this review. |

## DISAGREED-MODIFIED

| Point | Accepted concern | Modification / reason | Result |
|---|---|---|---|
| M4 central policy-v4 gate | Selection-trial history and policy-scope consistency matter. | The unsupported trial-count/backtest-length comparison and QUALIFIED-blocking effect were already withdrawn at the Grok baseline. No operational formula or new policy approval exists. Moving the undefined rule into central qualification docs would introduce a new gate instead of resolving it. | Keep future OVERFIT_RISK diagnostic-only; central policy-v4 docs/runtime untouched. Any future mandatory gate requires separate definition/review and Roman's explicit approval. |
| HYPE described as closed | A and C are closed and the mechanisms should be clearly separated. | HYPE is still running and NOT_EVALUATED at the terminal criterion. Interim losses do not close it. | Roadmap explicitly states HYPE running/no final verdict; no early-stop or family-closure action. |
| "Genuinely different economic mechanism" | D differs in daily multi-horizon signal, basket, direction and sizing from the exact failed C prescription. | Accept appropriate draft-family placement, not an established empirical economic edge. Changing timeframe/sizing alone does not demonstrate profitability; broader trend-family trial history must remain inventoried. | Hypothesis remains provisional and Grok blockers still apply. |
| "12 independent observations", non-blocking label | Twelve-month monthly holdout has at most 12 full monthly observations and needs methodological scrutiny. | Independence is not guaranteed by calendar aggregation. Small-sample/inference design was already a pre-lock blocker, not something Gemini's approval can waive. No statistical power calculation or new thresholds adopted. | No independence claim; DSR/frequency/holdout choice remains unresolved. |
| Repair dependencies / PR69 wording | Required storage/read infrastructure must be safe and isolated from B. | PR69 is PARKED(C), not an approved merge target; future structural metadata/archive repair replaces it after freeze release. No-new-optimizer-before-repair rule remains. A standalone D runner's specific storage needs must be declared, not inferred from the export defect. | No PR69 changes; any repair implementation/backfill/DDL/deploy requires separate authorization. |
| Proposed roadmap patch / evaluation status | A visible D draft section and explicit later-stage permissions are useful. | Replace "separately authorized one-shot run" phrasing with a requirement for FUTURE separate authorization; no authorization has been granted. PR70 docs merge is neither currently approved nor automatically a final D lock with blockers open. | Adds draft section and permission table, without a lock, implementation, data job, performance run or production approval. |

## IMPLEMENTED — DOCUMENTS ONLY

Changes:
- `RESEARCH-ROADMAP.md`: spot/futures research direction, D draft entry,
  HYPE still-running status, explicit permission/dependency sequence and
  diagnostic-only policy boundary.
- `TEST-SPEC-004-basket-tsmom-d-v1.md`: Gemini processed, roadmap alignment
  clarified, suitable-draft versus methodology-clearance distinction.
- `proposal-scanner-liquidity-filter.md`: Gemini disposition link and explicit
  reason central policy v4 is not changed.
- `GROK-REVIEW-DISPOSITION-TEST-SPEC-004.md`: historical "Gemini not received"
  wording annotated and current next-state updated; original Grok response and
  dispositions retained.
- This file records the Gemini response and individual dispositions.

HYPE decision document's numeric thresholds/observation/accounting requirements
are unchanged by this Gemini update. ZEC B's spec and official-fold roadmap
paragraph are unchanged. All runtime, tests, ledger, migration and configuration
blobs are unchanged. No application endpoint, performance dataset, optimizer job,
simulation or B metric inspection was requested.

No merge or production deploy initiated. Existing GitHub integration may
automatically build a branch Preview; no Preview application endpoint is used.
PR66/67 unchanged; PR69 stays parked; freeze remains.

## Current verdict and next action

**D is an appropriate DRAFT roadmap candidate, NOT CLEAN ENOUGH TO LOCK.**
Choose and finalize the open universe/statistical design, causal chronology,
warmup, costs/sizing and fold-provenance requirements before a final lock.
Those decisions cannot be inferred from reviewer agreement or runtime results.
The next work stays within separately authorized scope; no request in this
response approves a merge, runtime change, data job or official run.
HYPE remains without a final verdict until its own verified time/trade gates
and accounting validity; ledger cutover is still both-tests30/30 plus new
Roman instruction.

## Gemini response as supplied by Roman

**1. ROADMAP VERDICT (Verdikt k roadmape)**

* **Vhodná návrhová nová rodina (Appropriate draft new family).**
* Návrh Stratégie D predstavuje skutočne odlišný ekonomický mechanizmus a nejedná sa o rehabilitáciu zlyhaného receptu C.

* Kým uzavreté stratégie A a HYPE v8 testovali 5-minútový mean reversion a stratégia C testovala 1-hodinový breakout, stratégia D prechádza na denný časový rámec (1D) s multi-horizontovým momentum signálom... toto je geminiho posudok

* Stratégia D taktiež mení smerovanie výlučne na dlhé (long-only) pozície.

* Prístup k veľkosti pozície prechádza z fixného vkladu na škálovanie podľa inverznej volatility a cielenie volatility portfólia.

* Tieto zmeny spĺňajú podmienku roadmapy pre vytvorenie novej rodiny pred spustením akýchkoľvek výkonnostných testov.

**2. BLOCKING roadmap/architecture conflicts (Blokujúce konflikty v architektúre a roadmape)**

* **ID: M3-Spot-vs-Futures | Zdroj: RESEARCH-ROADMAP.md, riadok 74**.

* *Konflikt:* Roadmapa aktuálne uvádza ako príklad prípustnej novej rodiny pomalší trend na „vopred deklarovanom koši futures“ (predeclared futures basket). Návrh D však špecifikuje „spotový kôš“ (spot basket) a výslovne vylučuje pákový efekt a futures/perpetuals.

* *Náprava:* Text roadmapy musí byť upravený tak, aby explicitne povoľoval aj „spotové“ trhy pre tento typ multi-market trendu, aby sa predišlo nesúladu medzi schváleným smerovaním a špecifikáciou D.

* **ID: M4-Scanner-Policy-Scope | Zdroj: proposal-scanner-liquidity-filter.md, riadky 26-28**.

* *Konflikt:* Návrh skenera zavádza nový stav `OVERFIT_RISK`, ktorý blokuje stratégiu získať štatút `QUALIFIED` bez nového forward testu, ak počet pokusov prekročí dĺžku backtestu. Tento návrh vytvára napätie s existujúcou Politikou v4.

* *Náprava:* Toto nové pravidlo pre trial ledger (evidenciu pokusov) musí byť textovo integrované priamo do centrálnych brán Politiky v4, aby išlo o globálne architektonické pravidlo, nie iba o sprievodný návrh k skeneru.

* **ID: M6-HYPE-Terminal-Gate | Zdroj: preregistered-hype-v8-paper-decision.md, riadky 20 a 46**.

* *Konflikt:* Dokument zaznamenáva priebežný stav 18 a 17 uzavretých obchodov. Zastavovacie kritérium a brána pre vyhodnotenie však striktne vyžadujú splnenie oboch podmienok: minimálne 30 dní a zároveň minimálne 30 uzavretých obchodov.

* *Náprava:* Priebežná strata nemôže byť považovaná za finálny verdikt. Je nutné striktne dodržať pravidlo, že ekonomické vyhodnotenie (Official PASS/FAIL) a diagnostika (Scale) sa môžu vykonať až v momente reálneho dosiahnutia definovaného terminálneho bodu (30 dní + 30 obchodov).

**3. NON-BLOCKING caveats (Neblokujúce upozornenia)**

* Stratégia D navrhuje vyhodnocovaciu jednotku na báze mesačných výnosov portfólia a konečný holdout (testovacie obdobie) v dĺžke 12 mesiacov. Tento prístup poskytne maximálne 12 nezávislých mesačných pozorovaní v holdout vzorke, čo je extrémne malý štatistický súbor pre vyhodnotenie brán, najmä pre Deflated Sharpe Ratio (DSR). Bude nutné spoliehať sa na metodologické posúdenie od Groku, či je takáto vzorka postačujúca.

* Existuje riziko terminologickej zámeny medzi štrukturálnou opravou „B“ (PR69) a zmrazenou obchodnou „Stratégiou B“ (TEST-SPEC-002). Pri implementácii infraštruktúry je nutné udržiavať prísnu mennú a kódovú izoláciu týchto dvoch vetiev.

**4. A minimal proposed roadmap patch as TEXT (Minimálna textová záplata pre roadmapu)**

Do súboru `RESEARCH-ROADMAP.md` pod sekciu "Open research branch" pridať:

```text
### TEST-SPEC-004 — Strategy D v1
Status: **DRAFT FOR REVIEW — NOT LOCKED, NOT IMPLEMENTED**

- Hypothesis: Daily long-only volatility-scaled multi-horizon trend on a predeclared spot basket.
- Roadmap alignment: Qualifies as a distinct new economic family (slower multi-market trend, spot limits explicitly permitted).
- Dependencies: Requires continuous source inventory, Grok methodology clearance, and explicit Roman lock approval before synthetic verification.
- Evaluation status: Separately authorized one-shot development/holdout run. No market-data jobs, optimizer runs, or production deployments are authorized by this draft inclusion.

```

**5. Isolation/dependency checklist and distinct permissions required (Zoznam závislostí a požadovaných povolení)**

* **Fáza 1: Schválenie dokumentácie (Aktuálny stav)**
* *Požadované povolenie:* Schválenie a zlúčenie PR70 (iba textové zmeny). Zlúčenie tohto PR neoprávňuje spustenie implementácie, optimalizátora ani trhových dát.

* **Fáza 2: Infraštruktúrne a dátové opravy**
* *Závislosť:* Vyriešenie PR69 pre opravu metadát/archívu.

* *Požadované povolenie:* Explicitné oprávnenie na nasadenie architektonických opráv, ktoré nesmú nijako zmeniť správanie zmrazeného forward testu ZEC TSMOM B.

* **Fáza 3: Vývoj a syntetická verifikácia Stratégie D**
* *Závislosť:* Vyriešenie pripomienok od Groku (Part A) týkajúcich sa look-ahead biasu, nákladov a čistoty dátových zostáv.

* *Požadované povolenie:* Súhlas na napísanie čistých funkcií kódu a spustenie izolovaných syntetických testov.

* **Fáza 4: Oficiálne vyhodnotenie Stratégie D**
* *Závislosť:* Zamknutie špecifikácie s jasným historickým výberom koša a inventárom kontaminácie.

* *Požadované povolenie:* Oddelene autorizované jednorazové spustenie na vývojových a holdout dátach (one-shot evaluation).
