import { getLeagues } from "@/lib/api";
import { PredictClient } from "@/app/predict-client";

export const dynamic = "force-dynamic";

export default async function PredictPage() {
  let leagues: Awaited<ReturnType<typeof getLeagues>> = [];
  let apiError: string | null = null;
  try {
    leagues = await getLeagues();
  } catch (error) {
    apiError = error instanceof Error ? error.message : String(error);
  }

  if (apiError) {
    return (
      <div className="max-w-xl">
        <h1 className="mb-3 text-2xl font-semibold tracking-tight">Wycena meczu</h1>
        <p className="rounded-md border border-brick/30 bg-brick-soft px-4 py-3 text-sm text-brick">
          The pitchprob API is not reachable ({apiError}). Start it with{" "}
          <code className="num">make serve</code> and reload.
        </p>
      </div>
    );
  }

  return <PredictClient leagues={leagues} />;
}
