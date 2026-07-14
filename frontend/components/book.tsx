/** Market book view: everything the API says about one fixture, rendered
 * dense and honest. Data-viz vocabulary per DESIGN.md: home=gold, draw=
 * neutral, away=slate; negative EV=brick. */

import type { CountsSection, MarketBook, Outcome3 } from "@/lib/api";
import { pct, price } from "@/lib/format";
import { DataTable, EvBadge, Panel } from "@/components/ui";

/** JS objects reorder integer-like keys ("0", "1") ahead of everything else,
 * scrambling market lines — always sort numerically for display. */
function byLine<T>(record: Record<string, T>): Array<[string, T]> {
  return Object.entries(record).sort((a, b) => parseFloat(a[0]) - parseFloat(b[0]));
}

function ProbBar({ p }: { p: Outcome3 }) {
  return (
    <div
      aria-hidden
      className="flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full"
    >
      <span style={{ width: `${100 * p.home}%` }} className="bg-gold" />
      <span style={{ width: `${100 * p.draw}%` }} className="bg-draw" />
      <span style={{ width: `${100 * p.away}%` }} className="bg-slate" />
    </div>
  );
}

const MODEL_LABELS: Record<string, string> = {
  ensemble: "Ensemble",
  dixon_coles: "Dixon-Coles",
  elo: "Elo",
  gbm: "GBM",
};

function OneXTwoPanel({ book }: { book: MarketBook }) {
  const order = ["ensemble", "dixon_coles", "elo", "gbm"].filter(
    (m) => m in book.markets["1x2"],
  );
  const weights = Object.entries(book.ensemble_weights)
    .map(([name, w]) => `${MODEL_LABELS[name] ?? name} ${w.toFixed(2)}`)
    .join(" · ");
  return (
    <Panel
      title="Match result (1X2)"
      footnote={`Ensemble stack weights: ${weights}. Component disagreement is information, not noise.`}
    >
      <div className="flex flex-col gap-3">
        {order.map((model) => {
          const p = book.markets["1x2"][model];
          const emphasized = model === "ensemble";
          return (
            <div key={model} className={emphasized ? "" : "opacity-80"}>
              <div className="mb-1 flex items-baseline justify-between text-sm">
                <span className={emphasized ? "font-semibold" : "text-ink-muted"}>
                  {MODEL_LABELS[model] ?? model}
                </span>
                <span className="num flex gap-4">
                  <span className="text-gold-ink">{pct(p.home)}</span>
                  <span className="text-ink-muted">{pct(p.draw)}</span>
                  <span className="text-slate">{pct(p.away)}</span>
                </span>
              </div>
              <ProbBar p={p} />
            </div>
          );
        })}
        <div className="flex justify-between text-xs text-ink-muted">
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-gold" />
            {book.home_team}
          </span>
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-draw" />
            draw
          </span>
          <span>
            <span className="mr-1 inline-block h-2 w-2 rounded-full bg-slate" />
            {book.away_team}
          </span>
        </div>
      </div>
    </Panel>
  );
}

function CountsPanel({
  label,
  section,
}: {
  label: string;
  section: CountsSection;
}) {
  return (
    <div>
      <h4 className="mb-1 text-sm font-medium">{label}</h4>
      <p className="num mb-2 text-xs text-ink-muted">
        expected {section.expected.home.toFixed(1)} – {section.expected.away.toFixed(1)}{" "}
        (total {section.expected.total.toFixed(1)})
      </p>
      <DataTable
        head={["line", "over", "under"]}
        rows={byLine(section.totals).map(([line, ou]) => [
          line,
          pct(ou.over),
          pct(ou.under),
        ])}
      />
    </div>
  );
}

export function BookView({ book }: { book: MarketBook }) {
  const dc = book.markets.double_chance;
  const dnb = book.markets.draw_no_bet;
  const value = book.value_analysis;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <h2 className="text-2xl font-semibold tracking-tight">
          {book.home_team} <span className="text-ink-muted">vs</span> {book.away_team}
        </h2>
        <p className="num text-sm text-ink-muted">
          expected goals {book.expected_goals.home.toFixed(2)} –{" "}
          {book.expected_goals.away.toFixed(2)}
        </p>
        <p className="text-xs text-ink-muted">
          {book.league} · trained on {book.trained_on_matches.toLocaleString()} matches to{" "}
          {book.train_max_date}
        </p>
      </div>

      {value ? (
        <Panel title="Value vs offered odds (quarter-Kelly)">
          <DataTable
            head={["selection", "offered", "fair", "EV", "kelly"]}
            rows={(["home", "draw", "away"] as const).map((selection) => {
              const entry = value[selection];
              return [
                selection,
                price(entry.offered_price),
                price(entry.fair_price),
                <EvBadge key="ev" value={entry.expected_value} />,
                entry.kelly_fraction > 0 ? pct(entry.kelly_fraction) : "—",
              ];
            })}
          />
        </Panel>
      ) : null}

      <div className="grid gap-5 md:grid-cols-2">
        <OneXTwoPanel book={book} />

        <Panel title="Derived match markets">
          <DataTable
            head={["market", "", ""]}
            rows={[
              ["Double chance 1X", pct(dc.home_or_draw), ""],
              ["Double chance 12", pct(dc.home_or_away), ""],
              ["Double chance X2", pct(dc.draw_or_away), ""],
              ["Draw no bet — home", pct(dnb.home), ""],
              ["Draw no bet — away", pct(dnb.away), ""],
              ["BTTS — yes", pct(book.markets.btts.yes), ""],
              ["BTTS — no", pct(book.markets.btts.no), ""],
            ]}
          />
        </Panel>

        <Panel title="Totals (goals)">
          <DataTable
            head={["line", "over", "under"]}
            rows={byLine(book.markets.totals).map(([line, ou]) => [
              line,
              pct(ou.over),
              pct(ou.under),
            ])}
          />
        </Panel>

        <Panel
          title="Asian handicap"
          footnote="Win probabilities include half-wins; pushes refund."
        >
          <DataTable
            head={["home line", "home wins", "push", "away wins"]}
            rows={byLine(book.markets.asian_handicap).map(([line, ah]) => [
              line,
              pct(ah.home),
              ah.push > 0 ? pct(ah.push) : "—",
              pct(ah.away),
            ])}
          />
        </Panel>

        <Panel title="Most likely scores">
          <ul className="flex flex-wrap gap-2">
            {book.markets.correct_score_top.map((entry) => (
              <li
                key={entry.score}
                className="num rounded-md border border-line px-2.5 py-1 text-sm"
              >
                {entry.score}
                <span className="ml-2 text-ink-muted">{pct(entry.probability)}</span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Corners & cards" footnote={book.counts_markets.caveat}>
          <div className="grid gap-4 sm:grid-cols-2">
            {book.counts_markets.corners ? (
              <CountsPanel label="Corners" section={book.counts_markets.corners} />
            ) : null}
            {book.counts_markets.cards ? (
              <CountsPanel label="Cards" section={book.counts_markets.cards} />
            ) : null}
          </div>
        </Panel>
      </div>

      <p className="text-xs leading-relaxed text-ink-muted">{book.disclaimer}</p>
    </div>
  );
}
