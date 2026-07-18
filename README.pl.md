# pitchprob — podsumowanie po polsku

Silnik probabilistyczny do piłki nożnej: liczy skalibrowane prawdopodobieństwa
rynków meczowych (1X2, over/under, BTTS, handicapy azjatyckie, rożne, kartki),
porównuje je z kursami bukmacherów i uczciwie mierzy, czy cokolwiek z tego ma
wartość. **Nigdy nie obiecuje zysku** — punktem odniesienia jest linia
zamknięcia Pinnacle po zdjęciu marży (najlepszy publicznie dostępny estymator
prawdopodobieństwa), a raporty mówią wprost, kiedy modele od niej odstają.
Odstają prawie zawsze — i to jest oczekiwany, raportowany wynik.

Pełna dokumentacja techniczna z tabelami wyników: [README.md](README.md)
(po angielsku). Decyzje architektoniczne: `docs/adr/` (12 ADR-ów).

## Co zostało zbudowane (M1–M7 + program syndykacki)

- **Dane**: 21 589 meczów z 5 najlepszych lig Europy (sezony 2014/15–2025/26),
  ~1 mln kwotowań kursów (otwarcie **i** zamknięcie: bet365, Pinnacle,
  max/średnia rynku; 1X2, O/U 2.5, handicap azjatycki), 99,98% pokrycia xG
  z Understat, 40 500 rekordów kontuzji.
- **Modele**: Dixon-Coles (MLE z wygaszaniem czasowym), Poisson, Elo z
  ordered-logit, XGBoost na cechach xG/formy, ensemble (log-liniowy stacking).
  Ścieżka bettingowa używa Dixon-Colesa; ensemble służy tylko do wyświetlania
  (zmierzony werdykt, nie opinia).
- **Rynki**: każdy rynek bramkowy wyprowadzany z jednej macierzy wyników
  (ADR 0002), z pełną semantyką rozliczeń ćwierć-linii AH.
- **Ewaluacja (M7, ADR 0010)**: symulacja walk-forward bez podglądania
  przyszłości (własność testowana automatycznie), **dwa zegary** — zakład przy
  zamknięciu (protokół legacy) i **zakład przy otwarciu z prawdziwym CLV**
  (kurs otwarcia × fair z zamknięcia − 1), trzy rynki, pięć lig, przedziały
  ufności block-bootstrap po tygodniach, rejestr eksperymentów ze sparowanymi
  porównaniami A/B (`pitchprob experiment run|compare`).
- **Analizy fazy 1**: studium ruchu linii (`pitchprob study movement`),
  automatyczna analiza błędów po segmentach z korektą Benjaminiego-Hochberga,
  cechy rynkowe dostępne w momencie zakładu.
- **Meta-gate CLV (faza 2a, ADR 0011)**: drugi model (XGBoost, walk-forward,
  bez wycieku — udowodnione testem) przewidujący ostre CLV każdego kandydata
  i grający tylko powyżej progu.
- **Taśma kursów (faza 5, ADR 0012)**: `pitchprob record-odds` nagrywa
  codziennie kursy 21 bukmacherów (w tym Pinnacle i giełdę Betfair jako
  odniesienia) do tabeli `odds_ticks`; zaplanowane w harmonogramie Windows.
- **Interfejsy**: dashboard Next.js (wycena meczów, kupony, historia
  backtestów) + API FastAPI + CLI Typer. `make stack-up` → :3000/:8000.

## Uczciwe werdykty (zmierzone, nie założone)

1. **Model jest blisko rynku, ale za nim**: log-loss Dixon-Colesa jest ~2–3%
   za linią otwarcia i ~3% za zamknięciem — we wszystkich pięciu ligach.
2. **Raportowane wcześniej "+1,3% CLV" było wartością porównywania cen
   (line shopping), nie timingu.** Zmierzone uczciwie, prawdziwe CLV strategii
   przy otwarciu jest **istotnie ujemne we wszystkich 5 ligach** (−0,8% do
   −1,3%, p ≤ 0,003) — linia zamknięcia idzie *przeciwko* naszym zakładom.
3. **Rozbieżność model–rynek to sygnał błędu modelu, nie "steam"**: rynek nie
   podąża za modelem w żadnej lidze (w Bundeslidze istotnie idzie w drugą
   stronę), najmocniej tam, gdzie model najbardziej się wychyla.
4. **Meta-gate: null.** Bramka nie znalazła podzbioru z dodatnim ostrym CLV
   (pooled ≈ −0,4%); jej pozorna poprawa była znów line shoppingiem, co
   wyłapał projekt z dwiema etykietami CLV. **Prawdziwe pieniądze pozostają
   zablokowane** do czasu, aż dziennik zakładów pokaże co innego.
5. Wcześniejsze werdykty wciąż obowiązują: ensemble gorszy do grania mimo
   lepszej kalibracji; kalibracja per-klasa nie naprawia ROI; izotoniczna
   eksploduje; liczby kontuzji nic nie wnoszą (rynek już je wycenia).

## Kontekst polski (operator w Polsce)

- Gramy wyłącznie u **legalnych polskich bukmacherów** (rejestr Ministerstwa
  Finansów). Betfair i Pinnacle są niedostępne — służą tylko jako punkty
  odniesienia do pomiaru.
- Podatek 12% od stawek (wliczony w kursy) + 10% od wygranych > 2 280 zł
  oznacza, że polskie kursy są istotnie gorsze od najlepszych europejskich —
  **każdy wniosek o realnych pieniądzach liczymy po kursach faktycznie
  wziętych w Polsce**, wpisywanych do dziennika zakładów przy stawianiu.
- Polityka stawek: płaskie 2–5 zł na zakład, bezterminowo, cel pomiarowy;
  decyzją jest ledger CLV, nigdy ROI z backtestu.

## Jak uruchomić

```bash
make install                     # uv sync (Python 3.12)
make db-up && make migrate       # Postgres 16 w Dockerze (port 5433)
make stack-up                    # pełny stack: dashboard :3000, API :8000
make check                       # ruff + mypy --strict + ~400 testów

uv run pitchprob backtest --league E0 --start 2021-08-01 --at open \
    --markets 1x2,ou,ah --selector blended        # zegar syndykatu
uv run pitchprob experiment compare --league E0 --start 2021-08-01 \
    --at open --vs selector=meta                  # sparowane A/B
uv run pitchprob study movement --league E0 --start 2021-08-01
uv run pitchprob record-odds --league all         # snapshot taśmy (~15 kredytów)
```

## Co dalej (plan zatwierdzony 2026-07-18)

Dziennik zakładów (pick ledger) z ręcznym wpisem polskich kursów i
automatycznym CLV z taśmy → warstwa ryzyka (limity ekspozycji, bezpiecznik
obsunięć) → cechy kontekstowe przez harness (sędzia, frekwencja, presja
tabeli) → sezon 2026/27 jako **test na żywo**: stawki symboliczne, decyzje
wyłącznie na podstawie zmierzonego CLV po polskich kursach.
