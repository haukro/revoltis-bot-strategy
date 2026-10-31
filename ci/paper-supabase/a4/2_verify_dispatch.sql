-- A4 rev2: overenie doručenia tickov (iba čítanie, spúšťa postgres v SQL editore testovacieho projektu).
-- delivery: delivered = DB dôkaz (claim/outcome); pending = tick mladší ako 120 s;
-- undelivered = bez DB dôkazu pri bežiacom teste; unverifiable = žiadny bežiaci test.
-- pg_net_conflict = pg_net hlási 2xx, ale DB dôkaz chýba. Stav pg_net nikdy nerozhoduje.
begin;
set transaction read only;
do $$ begin
  if current_user <> 'postgres' then raise exception 'a4_verify_requires_postgres'; end if;
  if to_regprocedure('paper_dispatch.delivery_report()') is null then raise exception 'a4_verify_requires_a4'; end if;
end $$;
select delivery, count(*) as ticks, count(*) filter (where pg_net_conflict) as pg_net_conflicts
  from paper_dispatch.delivery_report() group by delivery order by delivery;
select * from paper_dispatch.delivery_report() where delivery <> 'delivered' or pg_net_conflict;
rollback;
