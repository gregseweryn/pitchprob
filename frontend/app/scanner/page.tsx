import { ScannerClient } from "@/app/scanner/scanner-client";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Scanner — pitchprob",
  description:
    "Verdict Polish bookmaker quotes against the tape's Pinnacle fair, on effective prices after the 12% turnover tax.",
};

export default function ScannerPage() {
  return <ScannerClient />;
}
