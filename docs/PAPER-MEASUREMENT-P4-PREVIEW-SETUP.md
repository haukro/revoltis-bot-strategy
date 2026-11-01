# P4 — izolovaný Vercel Preview pre testovací projekt

**Návrh schválený Romanom 30. 10. 2026.** Platí iba pre testovací Supabase
projekt `nofomo-paper-validation` (ref `zioazncbennqmscfnivv`). Produkcia,
merge do `main` a 24 h beh P4 nie sú súčasťou tohto kroku.

## 1. Ochrany v kóde (`backend/app/paper_routes.py`)

- P3 endpointy sú predvolene vypnuté (`PAPER_MEASUREMENT_ENABLED` ≠ `1` → 503).
- Mimo CI sa aktivujú iba pri `VERCEL_ENV = preview`, `VERCEL_GIT_COMMIT_REF =
  claude/paper-p4-preview` **a** `PAPER_SUPABASE_URL =
  https://zioazncbennqmscfnivv.supabase.co`. Vetva aj URL sú pevne v kóde,
  premenné prostredia ich nemôžu rozšíriť. Production a iné vetvy → 503
  `paper_environment_not_authorized`.
- Žiadne náhradné hodnoty: produkčné `SUPABASE_URL` ani
  `SUPABASE_SERVICE_ROLE_KEY` sa nepoužijú. Ak chýba ktorákoľvek `PAPER_*`
  hodnota alebo tajná hodnota → 503 `paper_configuration_missing`.
- V Preview: `CRON_SECRET` a `PAPER_DIAGNOSTIC_SECRET` majú aspoň 32 znakov,
  líšia sa a nezhodujú sa s kľúčom DB. Kľúč DB sa nesmie zhodovať
  s produkčným `SUPABASE_SERVICE_ROLE_KEY` a URL s produkčným `SUPABASE_URL`.
- Bypass ochrany Vercelu (`x-vercel-protection-bypass`) iba prepustí požiadavku
  cez SSO. Endpoint naďalej vyžaduje `Authorization: Bearer <CRON_SECRET>`
  (tick), diagnostika vlastný `PAPER_DIAGNOSTIC_SECRET`. Chybové odpovede
  obsahujú iba kód, nikdy hodnotu.
- Testy: `backend/tests/test_paper_worker.py` (`test_preview_*`).

## 2. Kroky, ktoré robí Roman (tajné hodnoty nejdú cez chat, logy, anotácie ani PR)

1. Vygenerovať dve rôzne hodnoty, napr. `openssl rand -hex 32` (64 znakov):
   jednu pre `CRON_SECRET`, druhú pre `PAPER_DIAGNOSTIC_SECRET`.
2. Vercel → projekt `revoltis-bot-strategy-haukro` → Settings → Environment
   Variables. Pre každú premennú: Environment **iba Preview**, Branch
   **`claude/paper-p4-preview`**, typ Sensitive. Nikdy Production ani All:

   | Premenná | Hodnota |
   | --- | --- |
   | `PAPER_MEASUREMENT_ENABLED` | `1` |
   | `PAPER_SUPABASE_URL` | `https://zioazncbennqmscfnivv.supabase.co` |
   | `PAPER_SUPABASE_SERVICE_ROLE_KEY` | `service_role` kľúč projektu **nofomo-paper-validation** (Supabase → projekt → Settings → API Keys) |
   | `CRON_SECRET` | hodnota 1 |
   | `PAPER_DIAGNOSTIC_SECRET` | hodnota 2 |

3. Vercel → Settings → Deployment Protection → Protection Bypass for
   Automation: vytvoriť secret. Pozor: platí pre celý projekt, teda pre všetky
   Preview. Ostatné Preview však nemajú `PAPER_*` premenné, takže endpointy
   v nich ostanú vypnuté.
4. Napísať do chatu iba „premenné nastavené“, bez hodnôt.

## 3. Po potvrdení

1. Push vetvy `claude/paper-p4-preview` → Preview deploy.
2. Overenie bez tajných hodnôt: bez `Authorization` → 401, s nesprávnym
   secretom → 401, diagnostika s `CRON_SECRET` → 401.
3. Otvorené pred krátkou skúškou: **dispatch z testovacej DB**. Cron job beží
   ako `paper_cron_owner` a musí poslať tick cez `pg_net` s `CRON_SECRET`
   a bypass secretom. Hodnoty sa uložia do Vault testovacieho projektu (zadá
   ich Roman v SQL editore). Prístup jobu k nim pôjde cez úzku SECURITY DEFINER
   funkciu, bez priameho čítania Vault rolou cronu. Je to samostatný návrh.
4. Krátka skúška cez Preview vrátane AT65 a AT66 (A3), zastavenie
   `3_pause.sql` + `3b_wait_idle.sql`. Až jej výsledok rozhodne o 24 h P4.
