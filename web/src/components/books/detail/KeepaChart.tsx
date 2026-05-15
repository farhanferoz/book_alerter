// Keepa price-history chart embed.
//
// The Keepa PNG endpoint is free + no-auth (sanctioned for embedding) and
// shows Amazon UK price history for the book. The backend proxies + caches
// it under /api/books/{id}/keepa-chart.png so we don't expose the user's IP
// to Keepa and we get a 24h server-side disk cache.
//
// 404 from the proxy = Keepa has no chart for this ISBN (very niche / brand
// new / 979-prefixed). Render nothing in that case.

import { useState } from "react";

export function KeepaChart({ bookId }: { bookId: number }) {
  const [errored, setErrored] = useState(false);
  if (errored) return null;
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <h3 className="mb-2 text-sm font-medium">
        Amazon UK price history{" "}
        <span className="text-xs font-normal text-muted-foreground">via Keepa</span>
      </h3>
      <img
        src={`/api/books/${bookId}/keepa-chart.png`}
        alt="Amazon UK price history from Keepa"
        loading="lazy"
        onError={() => setErrored(true)}
        className="w-full rounded"
      />
    </div>
  );
}
