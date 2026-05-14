import { useEffect, useState } from "react";

type HealthState =
  | { status: "loading" }
  | { status: "ok"; data: unknown }
  | { status: "error"; message: string };

function App() {
  const [health, setHealth] = useState<HealthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetch("/api/health")
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setHealth({ status: "ok", data });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setHealth({
            status: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="min-h-svh bg-background text-foreground p-8">
      <h1 className="text-2xl font-semibold mb-4">Book Alerter</h1>
      <section>
        <h2 className="text-lg font-medium mb-2">/api/health</h2>
        {health.status === "loading" && (
          <p className="text-muted-foreground">Loading...</p>
        )}
        {health.status === "error" && (
          <pre className="text-destructive">{health.message}</pre>
        )}
        {health.status === "ok" && (
          <pre className="rounded-md border bg-muted p-3 text-sm">
            {JSON.stringify(health.data, null, 2)}
          </pre>
        )}
      </section>
    </main>
  );
}

export default App;
