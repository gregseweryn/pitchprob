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
  ~1 mln kwotowań kursów (wczesny snapshot **i** zamknięcie: bet365, Pinnacle,
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
  zamknięciu (protokół legacy) i **zakład przy wczesnym snapshocie z prawdziwym
  CLV** (wczesny kurs × fair z zamknięcia − 1), trzy rynki, pięć lig, przedziały
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
- **Skaner PL i dziennik zakładów (faza 5, ADR 0013)**: `pitchprob scan`
  werdyktuje kursy, które operator widzi u polskich buków, względem
  Pinnacle fair z taśmy (kotwica **główna** — po werdyktach faz 0–2a to
  rynek, nie model, jest punktem odniesienia; model fair jest tylko
  informacyjny), zawsze na **kursach efektywnych** (×0,88 z podatkiem,
  ×1,0 pod promocją bez podatku). `pitchprob pick log|settle|list`
  prowadzi dziennik realnych zakładów z automatycznym rozliczeniem i
  dekompozycją CLV na `clv_sharp` (timing) i `clv_exec` (cena wykonana).
- **Rożne, mapa opóźnień i feed PL (ADR 0014)**: `pitchprob record-corners`
  dopisuje do taśmy kursy rożnych dla meczów w oknie 26 h przed gwizdkiem
  (Pinnacle wycenia je dopiero ~dobę wcześniej — zmierzone, nie założone;
  pilot E0 kosztuje ~43 z 500 kredytów miesięcznie i ma dwa sufity
  wydatków). `pitchprob study latency` mierzy, który buk kopiuje ruch
  ostrej linii najpóźniej. `pitchprob oddsio` + `pitchprob quote-check`
  to prototyp feedu odds-api.io (Betclic PL + STS PL) — **informacyjny**,
  dopóki ręczna walidacja go nie dopuści.
- **Warstwa ryzyka (faza 3, ADR 0015)**: limity ekspozycji i bezpiecznik
  obsunięcia wpięte w `pick log` — odrzucony zakład nie zapisuje się wcale,
  a `--override-risk` stawia go mimo to i **trwale znakuje picka** powodem.
  `pitchprob risk status|report` pokazuje stan limitów i cotygodniowy raport
  „co mówi taśma".
- **Interfejsy**: dashboard Next.js (wycena meczów, **skaner**, **ledger**,
  kupony, historia backtestów) + API FastAPI + CLI Typer. `make stack-up` →
  :3000/:8000. Widok skanera pokazuje kursy efektywne per buk, wiek kotwicy
  i werdykty NO ANCHOR/STALE/UNVERIFIED jako nazwane odmowy — nie puste
  komórki; ledger pokazuje CLV exec/sharp z dekompozycją i stan warstwy
  ryzyka. Disclaimery przychodzą w payloadzie API i są renderowane jako
  treść obok liczb.

## Uczciwe werdykty (zmierzone, nie założone)

> **Zastrzeżenie nazewnicze (audyt 2026-07-20):** to, co źródło danych i
> flaga CLI `--at open` nazywają „otwarciem", **nie jest otwarciem rynku** —
> football-data.co.uk zbiera te kursy w piątkowe (mecze weekendowe) i
> wtorkowe (środek tygodnia) popołudnia, czyli T-3 do T-1 przed meczem.
> Wszędzie poniżej znaczy to **„wczesny snapshot"**. Werdykty ujemnego CLV są
> przy tej korekcie konserwatywne: zmierzone okno wczesny snapshot→zamknięcie
> jest krótsze niż prawdziwe otwarcie→zamknięcie, więc ruch rynku przeciw tym
> zakładom jest raczej niedoszacowany niż zawyżony.

1. **Model jest blisko rynku, ale za nim**: log-loss Dixon-Colesa jest ~2–3%
   za wczesnym snapshotem i ~3% za zamknięciem — we wszystkich pięciu ligach.
2. **Raportowane wcześniej "+1,3% CLV" było wartością porównywania cen
   (line shopping), nie timingu.** Zmierzone uczciwie, prawdziwe CLV strategii
   przy wczesnym snapshocie jest **istotnie ujemne we wszystkich 5 ligach** (−0,8% do
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
- **Wartość mieszka w promocjach, nie w cenach.** Silnik promo-EV wycenia
  boost, „Grę bez podatku" czy freebet jako **instrument**: ile EV dokłada
  sama promocja ponad goły, opodatkowany kurs. Betclic „Gra bez podatku 2.0"
  (pierwsze 1 000 zł obrotu bez podatku) to +13,6% wypłaty — więcej niż
  jakakolwiek przewaga zmierzona w tym projekcie, dlatego jest domyślnym
  miejscem gry na sezon pomiarowy. Dziennik pilnuje limitu 1 000 zł i
  **odmawia** zapisania zakładu bez podatku, który się w nim nie mieści.
- Spodziewany werdykt skanera to najczęściej **„nie graj"** — 12% podatku
  siedzi w cenach i jest większe niż mierzalna przewaga. Skaner ma to mówić
  wprost, nie produkować akcji.

## Jak uruchomić

```bash
make install                     # uv sync (Python 3.12)
make db-up && make migrate       # Postgres 16 w Dockerze (port 5433)
make stack-up                    # pełny stack: dashboard :3000, API :8000
make check                       # ruff + mypy --strict + ~500 testów

uv run pitchprob backtest --league E0 --start 2021-08-01 --at open \
    --markets 1x2,ou,ah --selector blended        # zegar syndykatu
uv run pitchprob experiment compare --league E0 --start 2021-08-01 \
    --at open --vs selector=meta                  # sparowane A/B
uv run pitchprob study movement --league E0 --start 2021-08-01
uv run pitchprob record-odds --league all         # snapshot taśmy (~15 kredytów)
uv run pitchprob record-corners --league E0       # rożne, T-26h (~1 kredyt/mecz)
uv run pitchprob study latency                    # kto kopiuje ostrą linię najpóźniej
uv run pitchprob oddsio books --filter PL         # katalog buków PL (bez klucza)
uv run pitchprob quote-check report               # czy feed zasłużył na zaufanie
uv run pitchprob risk status                     # limity i stan bezpiecznika
uv run pitchprob risk report                     # cotygodniowe „co mówi taśma"
uv run pitchprob status                          # bramka świeżości przed kolejką

# skaner: podajesz kursy, które widzisz u polskich buków
uv run pitchprob scan "arsenal" --market ou --selection over --line 3.0 \
    --quote betclic:2.10 --quote sts:2.05 --tax-free betclic

# dziennik: zapis wykonanego zakładu, rozliczenie, CLV
uv run pitchprob pick log --match "arsenal" --market ou --selection over \
    --line 3.0 --book betclic --stake 5 --price 2.10 --tax-free
uv run pitchprob pick settle                      # wyniki + CLV z taśmy
uv run pitchprob pick list
```

Ten sam kurs 2,10 dostaje przeciwne werdykty: w Betclicu pod „Grą bez
podatku" to **+5,0%** przewagi (graj), w opodatkowanym STS kurs efektywny
spada do 1,80, czyli **−9,8%** (nie graj). Skaner odmawia też werdyktu,
gdy taśma nie kwotuje tej samej linii (**NO ANCHOR**) i degraduje „graj" do
**STALE**, gdy kotwica ma ponad 30 godzin — świeżość kwotowania jest
drukowana przy każdym werdykcie, bo taśma nagrywa raz dziennie.

### Po co są limity — bo nie po to, po co się zwykle myśli

Przy stawkach 2–5 PLN i bankrollu 500 PLN **ruina nie jest ryzykiem** —
to 100–250 zakładów zapasu. Limity chronią co innego. Po pierwsze próbkę:
CLV raportujemy block bootstrapem po tygodniach ISO, który widzi zależność
*między* tygodniami, ale jest ślepy na zależność *wewnątrz* meczu. Dwa
zakłady na ten sam mecz wchodzą do próbki jako dwie obserwacje, niosąc
informację mniej więcej jednej — i przedział ufności wychodzi za wąski.
Dlatego limit na mecz równa się limitowi pojedynczej stawki: **jeden zakład
na mecz, egzekwowany, nie deklarowany.** Po drugie wykrycie błędu: sezon
najpewniej zepsuje bug, nie wariancja, więc bezpiecznik obsunięcia (75 PLN,
15% bankrolla) jest czujnikiem dymu, a nie ochroną kapitału. Liczy wyłącznie
**zrealizowany** wynik rozliczonych zakładów — otwarte pozycje mogą jeszcze
wygrać, a przy 15 otwartych piku byłoby to 75 PLN widmowego obsunięcia,
czyli dokładnie cały próg.

Raport tygodniowy **nie publikuje przedziału ufności poniżej czterech
tygodni** zakładów: bootstrap losujący jeden blok zwraca ten sam blok za
każdym razem, więc „95% CI" miałby zerową szerokość — najpewniej wyglądający
wynik w całym systemie, wyprodukowany przez najmniej danych.

### Czego nie dało się zmierzyć — i dlaczego to jest w dokumentacji

Audyt twierdził, że mapa opóźnień linii PL jest „mierzalna już dziś
z istniejących danych". **Nie była**, co pokazał pomiar: taśma miała jeden
snapshot, The Odds API nie niesie ani jednego polskiego bukmachera
(widać `betclic_fr`, nie Betclic PL), a kadencja raz na dobę kwantuje
opóźnienie do 24 godzin — podczas gdy mierzone zjawisko żyje w minutach.
Moduł powstał mimo to, źródło-agnostyczny, i na dzisiejszych danych
uczciwie pisze „brak pomiaru" wraz z powodem, zamiast podać liczbę.
Sprostowanie trafiło do samego audytu (Etap 3.2), a test w
`tests/test_docs.py` pilnuje, żeby tam zostało.

## Co dalej (plan zatwierdzony 2026-07-18)

Dziennik zakładów i skaner są **zbudowane** (ADR 0013) → warstwa ryzyka
(limity ekspozycji, bezpiecznik obsunięć) → cechy kontekstowe przez harness
(sędzia, frekwencja, presja tabeli) → sezon 2026/27 jako **test na żywo**:
stawki symboliczne, decyzje wyłącznie na podstawie zmierzonego CLV po
polskich kursach.
