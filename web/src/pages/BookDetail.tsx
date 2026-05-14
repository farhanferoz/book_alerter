import { useParams } from "react-router-dom";

export function BookDetail() {
  const { id } = useParams<{ id: string }>();
  return (
    <section>
      <h1 className="text-xl font-semibold mb-2">Book detail</h1>
      <p className="text-sm text-muted-foreground">
        Detail view for book <code className="font-mono">{id}</code>. Per-source price history,
        stats, and refetch controls will live here (Phase 10).
      </p>
    </section>
  );
}
