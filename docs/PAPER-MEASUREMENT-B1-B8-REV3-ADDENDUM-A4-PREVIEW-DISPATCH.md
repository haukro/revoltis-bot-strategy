# B1–B8 rev3 — dodatok A4: odoslanie ticku z testovacej DB na izolovaný Preview

**NÁVRH NA SCHVÁLENIE ROMANOM — 30. 10. 2026. NIE JE SCHVÁLENÝ.**

Platí iba pre testovací projekt `nofomo-paper-validation` a Preview vetvu
`claude/paper-p4-preview` ([postup Preview](PAPER-MEASUREMENT-P4-PREVIEW-SETUP.md)).
Nadväzuje na A2 (overenie pôvodu) a A3 (vytvorenie a zastavenie jobu).
Nemení FINAL rev3, A1–A3 ani migrácie P2/P3/A2. Produkcia je mimo rozsahu.

## A4.0 Zistenia na testovacom projekte (30. 10. 2026, iba čítanie)

- Vault 0.3.1: `postgres` číta `vault.decrypted_secrets`, `paper_cron_owner` nie.
- pg_net 0.20.4: USAGE na `net` a EXECUTE na `net.http_post` majú `anon`,
  `authenticated`, `service_role`, `authenticator` aj obe roly `paper_*`.
- **`net.http_request_queue` (stĺpec `headers`) a `net._http_response` majú
  všetky práva pre PUBLIC.** Vlastník je `supabase_admin`, takže `postgres`
  ich neodoberie (rovnako ako pri crone v A2). `pg_net.ttl` je 6 h, frontu
  pg_net vyprázdni hneď po odoslaní.

## A4.1 Mechanizmus

Súbor `ci/paper-supabase/a4/a4_dispatch.sql` (spúšťa `postgres`).
**Zámerne nie je v `supabase/migrations`**, aby sa nikdy nedostal do produkcie
cez `db push`:

- schéma `paper_dispatch` (vlastník `postgres`, USAGE iba `paper_cron_owner`);
- `paper_dispatch.target`: jediný riadok s URL pevne danou obmedzením
  `https://revoltis-bot-strategy-haukro-git-claude-paper-p4-preview-haukro.vercel.app/api/paper/measure/tick`,
  predvolene `enabled = false`;
- `paper_dispatch.sent`: každý tick sa odošle najviac raz;
- `paper_dispatch.send_tick(tick_id)`: SECURITY DEFINER, vlastník `postgres`,
  `search_path = ''`, EXECUTE iba `paper_cron_owner`. Odošle iba, ak:
  `session_user = paper_cron_owner`, tick vznikol v **tej istej session**
  (`backend_pid = pg_backend_pid()`) najviac pred 10 s, ešte nebol odoslaný,
  cieľ je zapnutý a oba secrety vo Vault majú aspoň 32 znakov. Inak skončí
  chybou a do fronty nič nezapíše;
- hlavičky: `Authorization: Bearer <CRON_SECRET>` a
  `x-vercel-protection-bypass: <bypass>`. Endpoint naďalej overuje `CRON_SECRET`
  (bypass nenahrádza autentifikáciu).

Príkaz jobu (bez tajných hodnôt):
`select paper_dispatch.send_tick(public.paper_tick_start())`. Job sa vytvorí
súborom `a4/1_schedule_dispatch.sql`, ktorý je zhodný s A3 `1_schedule.sql`
až na príkaz. Zastavenie a overenie sú bez zmeny z A3 (`3_pause.sql`,
`3b_wait_idle.sql`, `2_verify.sql`, `2b_verify_roles.sql`).

| Súbor | SHA-256 |
| --- | --- |
| `ci/paper-supabase/a4/a4_dispatch.sql` | `c047c7e7bb73bfe1aa7383881c4fac99aaf4554aa5f878dd5ad266fcf5fce05d` |
| `ci/paper-supabase/a4/1_schedule_dispatch.sql` | `09bbcbcecc39a6183e7ebe7ee21a3a5abcfa68a00ffdb0129a371d872d5bbd02` |

## A4.2 Tajné hodnoty

Zadáva ich Roman v SQL editore testovacieho projektu (nie cez chat, logy, PR):

```sql
select vault.create_secret('<CRON_SECRET z Vercelu>', 'paper_p4_cron_secret', 'P4 preview tick');
select vault.create_secret('<Protection Bypass for Automation>', 'paper_p4_vercel_bypass', 'P4 preview bypass');
```

## A4.3 Prijaté riziká (iba testovací projekt)

1. **Fronta pg_net je čitateľná pre PUBLIC.** Kým pg_net požiadavku neodošle
   (zvyčajne milisekundy), hlavičky so secretmi vidí každá rola, ktorá vie
   spustiť vlastné SQL nad `net`. PostgREST schému `net` nevystavuje, preto ide
   prakticky o `postgres`/`supabase_admin` a session cron jobu s pevným príkazom.
2. **Dopad úniku `CRON_SECRET` je malý.** Endpoint prijme iba existujúci tick
   nie starší ako 120 s a jeho opakované spracovanie je idempotentné
   (`tick_already_used`).
3. **Bypass secret platí pre celý Vercel projekt** (všetky Preview). Ostatné
   Preview nemajú `PAPER_*` premenné, takže meracie endpointy v nich ostanú
   vypnuté. Ostatný obsah Preview by však bol dostupný bez SSO.
4. Po P4 sa oba secrety rotujú, bypass sa zruší a `paper_dispatch.target`
   sa vypne.
5. Pre produkciu tento model nie je vhodný (verejne čitateľná fronta pg_net).

## A4.4 Testy

| ID | Scenár a očakávaný výsledok | Zaradenie |
| --- | --- | --- |
| AT67 | Definícia a ACL `send_tick`, pevná URL (iná → `target_url_check`), aplikačné roly a `paper_rpc_owner` bez prístupu, `paper_cron_owner` nečíta `target`, `sent` ani Vault. | A2 CI |
| AT68 | Pozitívny prípad v transakcii s ROLLBACK: požiadavka iba na pevnú URL, POST, obe hlavičky správne. Po ROLLBACK fronta bez zmeny (CI nič neodošle). | A2 CI |
| AT69 | Odmietnutia bez zápisu do fronty: session `postgres`, tick z iného PID, tick starší ako 10 s, neznámy tick, opakované odoslanie, vypnutý cieľ, chýbajúci alebo krátky secret. Opakovaná aplikácia A4 odmietnutá. | A2 CI |
| AT70 | Skutočné doručenie na Preview: odpoveď 200 `processed`, žiadna 401/403. | Iba krátka skúška na testovacom projekte |

Overenie: emulácia PG16 (náhrady za Vault a `net.http_post`) prešla pre
AT67–AT69. CI so skutočným Vaultom a pg_net: pozri PR #75.

## Záznam zmien

| Dátum | Zmena | Stav |
| --- | --- | --- |
| 30. 10. 2026 | Návrh A4. | NÁVRH, čaká na CI a schválenie. |
