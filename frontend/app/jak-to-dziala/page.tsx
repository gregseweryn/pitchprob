/** The explainer the operator asked for: what decides a bet, and why it is
 * not the model.
 *
 * This page exists because "I do not see the model's predictions, I do not
 * know when it says to bet" is the most reasonable possible confusion — the
 * model IS on screen, it just deliberately does not drive the verdict. That
 * separation is the project's central finding, and a tool that hides it
 * would be lying by omission. Copy is Polish: the operator reads this one,
 * not a maintainer. */

import Link from "next/link";

import { Panel } from "@/components/ui";

export const metadata = {
  title: "Jak to działa — pitchprob",
  description:
    "Co decyduje o zakładzie, dlaczego nie decyduje o nim model, i skąd bierze się „nie graj”.",
};

function Step({
  n,
  title,
  children,
}: {
  n: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-4">
      <span
        aria-hidden
        className="num mt-0.5 shrink-0 text-sm font-medium text-gold-ink"
      >
        {n}
      </span>
      <div>
        <h3 className="text-sm font-medium">{title}</h3>
        <div className="mt-1 max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          {children}
        </div>
      </div>
    </div>
  );
}

export default function HowItWorksPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Jak to działa</h1>
        <p className="mt-1 max-w-[68ch] text-sm text-ink-muted">
          Krótko: <strong className="text-ink">model nie decyduje o tym,
          czy grasz</strong>. Decyduje cena Pinnacle&apos;a. Poniżej dlaczego
          — bo to nie jest brak funkcji, tylko główny wynik tego projektu.
        </p>
      </div>

      <Panel title="Dwa różne pytania, dwa różne narzędzia">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-ink-muted">
                <th className="pb-2 font-medium">pytanie</th>
                <th className="pb-2 font-medium">gdzie</th>
                <th className="pb-2 font-medium">czy decyduje o zakładzie</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-line">
                <td className="py-2">Ile ten mecz naprawdę jest wart?</td>
                <td className="py-2">
                  <Link href="/" className="text-gold-ink underline">
                    Wycena meczu
                  </Link>
                </td>
                <td className="py-2 text-ink-muted">nie</td>
              </tr>
              <tr>
                <td className="py-2">Czy ten kurs u buka opłaca się zagrać?</td>
                <td className="py-2">
                  <Link href="/scanner" className="text-gold-ink underline">
                    Skaner
                  </Link>
                </td>
                <td className="py-2 font-medium">tak</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-4 max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          Skaner <strong className="text-ink">nie pyta modelu</strong>. Pyta
          Pinnacle&apos;a. Model możesz mu podać jako informację poboczną — i
          zobaczysz ją w osobnej kolumnie — ale nigdy nie odwróci werdyktu.
        </p>
      </Panel>

      <Panel title="Dlaczego model nie decyduje">
        <p className="max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          Bo to sprawdzono i model przegrał z rynkiem. Trzy pomiary, wszystkie
          na danych spoza próby uczącej:
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm">
            <tbody>
              <tr className="border-b border-line">
                <td className="py-2">
                  Jakość prognoz (log-loss, mniej = lepiej)
                </td>
                <td className="num py-2 text-right">
                  model <span className="text-brick">0,9755</span> vs Pinnacle{" "}
                  <span className="text-gold-ink">0,9464</span>
                </td>
              </tr>
              <tr className="border-b border-line">
                <td className="py-2">
                  Obstawianie wg przewagi modelu, 2 189 zakładów
                </td>
                <td className="num py-2 text-right text-brick">−1,8% ROI</td>
              </tr>
              <tr>
                <td className="py-2">To samo, wersja z ensemble</td>
                <td className="num py-2 text-right text-brick">−11,2% ROI</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="mt-4 max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          Gdyby ta aplikacja mówiła „model każe grać”, mówiłaby nieprawdę.
          Mówi więc co innego: model jest ciekawy jako obraz meczu, ale
          pieniądze stawiamy wyłącznie tam, gdzie <em>cena</em> jest lepsza
          od ceny ostrej.
        </p>
      </Panel>

      <Panel title="Co więc decyduje: cena Pinnacle'a bez marży">
        <div className="flex flex-col gap-4">
          <Step n="1" title="Pinnacle publikuje kurs">
            To bukmacher, który przyjmuje duże zakłady od zawodowców i tnie
            marżę do minimum. Dlatego jego kurs jest najlepszym publicznie
            dostępnym oszacowaniem prawdy — lepszym niż jakikolwiek model,
            który tu zbudowano.
          </Step>
          <Step n="2" title="Zdejmujemy marżę (metoda Shina)">
            Zostaje <strong className="text-ink">fair</strong> — czysta
            szansa. Przy kursach 1.89 / 1.89 fair wynosi dokładnie 50%.
          </Step>
          <Step n="3" title="Twój kurs przeliczamy na efektywny">
            Polski bukmacher pobiera{" "}
            <strong className="text-ink">12% podatku od stawki</strong>, więc
            kurs 2.10 realnie płaci 2.10 × 0,88 ={" "}
            <span className="num">1.85</span>. Pod promocją „gra bez podatku”
            płaci pełne <span className="num">2.10</span>.
          </Step>
          <Step n="4" title="Porównujemy i mówimy wprost">
            <div className="num mt-2 rounded-md border border-line bg-surface p-3 text-xs leading-relaxed">
              Betclic 2.10 bez podatku: 0,50 × 2,10 − 1 ={" "}
              <span className="text-gold-ink">+5,0%</span> → GRAJ
              <br />
              STS 2.10 z podatkiem: 0,50 × 1,85 − 1 ={" "}
              <span className="text-brick">−7,6%</span> → NIE GRAJ
            </div>
          </Step>
        </div>
      </Panel>

      <Panel title="Dlaczego prawie zawsze usłyszysz „nie graj”">
        <p className="max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          Bo <strong className="text-ink">12% podatku jest większe niż
          jakakolwiek przewaga, którą ten projekt kiedykolwiek zmierzył</strong>.
          To nie jest usterka ani nadmierna ostrożność — to arytmetyka.
          Wartość może się pojawić w dwóch miejscach:
        </p>
        <ul className="mt-3 flex max-w-[68ch] list-disc flex-col gap-2 pl-5 text-sm leading-relaxed text-ink-muted">
          <li>
            <strong className="text-ink">W promocjach.</strong> „Gra bez
            podatku” jest warta +13,6% wypłaty — więcej niż jakakolwiek
            przewaga modelowa zmierzona w tym projekcie. Dlatego promocje są
            wyceniane jako osobny instrument, w kolumnie „promocja”.
          </li>
          <li>
            <strong className="text-ink">W kursach, które nie nadążyły.</strong>{" "}
            Gdy Pinnacle rusza cenę, a polski bukmacher jeszcze nie — przez
            chwilę jego kurs jest za wysoki. Właśnie to wykrywa porównanie
            z kotwicą.
          </li>
        </ul>
      </Panel>

      <Panel title="Co znaczy każdy werdykt">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[38rem] text-sm">
            <tbody>
              <tr className="border-b border-line">
                <td className="py-2 whitespace-nowrap">
                  <span className="inline-block rounded border border-gold-ink bg-gold-soft px-1.5 py-0.5 text-xs font-medium text-gold-ink">
                    GRAJ
                  </span>
                </td>
                <td className="py-2 text-ink-muted">
                  Kurs efektywny bije świeżą kotwicę o co najmniej 2%.
                </td>
              </tr>
              <tr className="border-b border-line">
                <td className="py-2 whitespace-nowrap">
                  <span className="inline-block rounded border border-line bg-surface px-1.5 py-0.5 text-xs text-ink-muted">
                    NIE GRAJ
                  </span>
                </td>
                <td className="py-2 text-ink-muted">
                  Policzone i odrzucone. Normalna, spodziewana odpowiedź.
                </td>
              </tr>
              <tr className="border-b border-line">
                <td className="py-2 whitespace-nowrap">
                  <span className="inline-block rounded border border-dashed border-ink-muted px-1.5 py-0.5 text-xs text-ink-muted">
                    NIEAKTUALNE
                  </span>
                </td>
                <td className="py-2 text-ink-muted">
                  Kotwica ma ponad 30 godzin. Nie wiemy, czy cena Pinnacle&apos;a
                  w międzyczasie nie uciekła — to sygnał, żeby odświeżyć
                  taśmę, nie żeby grać.
                </td>
              </tr>
              <tr className="border-b border-line">
                <td className="py-2 whitespace-nowrap">
                  <span className="inline-block rounded border border-dashed border-ink-muted px-1.5 py-0.5 text-xs text-ink-muted">
                    BRAK KOTWICY
                  </span>
                </td>
                <td className="py-2 text-ink-muted">
                  Taśma nie ma tej linii. Porównanie z sąsiednią linią byłoby{" "}
                  <em>złą</em> odpowiedzią zamiast brakującej, więc aplikacja
                  odmawia.
                </td>
              </tr>
              <tr>
                <td className="py-2 whitespace-nowrap">
                  <span className="inline-block rounded border border-gold-ink px-1.5 py-0.5 text-xs text-gold-ink">
                    NIEZWERYFIKOWANE
                  </span>
                </td>
                <td className="py-2 text-ink-muted">
                  Cena przyszła z automatycznego feedu, którego jeszcze nie
                  sprawdziliśmy ręcznie. Zajrzyj na stronę bukmachera zanim
                  zagrasz.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Po co to wszystko, skoro prawie zawsze „nie graj”">
        <p className="max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          Bo celem sezonu 2026/27 nie jest zysk, tylko{" "}
          <strong className="text-ink">odpowiedź na pytanie, czy masz
          przewagę</strong>. Przy stawkach 2–5 zł nawet pełny sukces to
          kilkadziesiąt złotych — wartością jest dowód, nie pieniądze.
        </p>
        <p className="mt-3 max-w-[68ch] text-sm leading-relaxed text-ink-muted">
          Mierzy to <Link href="/ledger" className="text-gold-ink underline">
          dziennik</Link>: dla każdego zakładu liczy{" "}
          <strong className="text-ink">CLV</strong> — czy cena, którą wziąłeś,
          była lepsza od ceny zamknięcia. Po kilkunastu tygodniach dziennik
          powie, czy wybierasz dobrze. To jedyna liczba w tym projekcie,
          która ma prawo zmienić decyzję o graniu prawdziwymi pieniędzmi.
        </p>
      </Panel>
    </div>
  );
}
