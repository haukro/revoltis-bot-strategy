# GitHub + Vercel

## Čo je pripravené

- GitHub uchová zdrojový kód a pri každej zmene overí zostavenie aplikácie.
- Vercel zostaví React web a FastAPI rozhranie ako jednu verejnú aplikáciu.
- Každý push do vetvy `main` vytvorí novú produkčnú verziu.
- Ostatné vetvy a pull requesty dostanú samostatnú testovaciu adresu.
- Verejná adresa bude bezplatná vo formáte `nazov-projektu.vercel.app`.

## Nastavenia Vercelu

Pri importe repozitára nastav Framework Preset na **Services**. Root Directory nechaj na koreň repozitára.

Do Environment Variables vlož iba hodnoty zo svojho Supabase projektu:

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`

Bez nich web a burzové sviečky fungujú, ale používateľské dáta sa na serveri trvalo neuchovajú.

## Produkcia a testovanie

- Produkcia: vetva `main`
- Testovanie: nová vetva, napríklad `test`
- Stabilná adresa: Vercel priradí `revoltis-bot-strategy.vercel.app`, ak je názov voľný
- Vlastná doména sa dá pripojiť neskôr; nie je potrebná na spustenie

## Bezpečnosť

Repozitár neobsahuje API kľúče burzy ani nastavenia reálneho obchodovania. Aplikácia používa verejné Binance spot dáta a simulačný režim.
