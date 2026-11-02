-- Zaznam rucnej zmeny produkcie (vdglyxhlmukxeqynguke), 2026-11-01.
-- Na produkcii uz vytvorene v SQL editore samostatnymi prikazmi CREATE INDEX CONCURRENTLY
-- a nasledne ANALYZE (schvalil Roman). Tento subor iba zosuladuje repo so stavom produkcie:
-- je idempotentny (IF NOT EXISTS), na produkcii nic nemeni, inde indexy vytvori.
-- Vecne: /api/dashboard a /api/optimizer-latest padali na statement_timeout (8 s).
-- Podrobnosti: docs/OPS-2026-11-01-PROD-READ-PATH-INDEXES.md
create index if not exists simulation_runs_finished_at_idx
  on public.simulation_runs (finished_at desc);
create index if not exists optimizer_runs_kind_version_finished_idx
  on public.optimizer_runs ((request->>'kind'), (result->>'version_id'), finished_at desc);
