// Keepa price-history chart embed.
//
// The Keepa PNG endpoint is free + no-auth (sanctioned for embedding) and
// shows Amazon UK price history for the item. The backend proxies + caches
// it under /api/books/{id}/keepa-chart.png or /api/products/{id}/keepa-chart.png
// (same shape on both — `itemApiBase` picks the prefix) so we don't expose
// the user's IP to Keepa and we get a 24h server-side disk cache.
//
// 404 from the proxy = Keepa has no chart for this ISBN/ASIN (very niche /
// brand new / 979-prefixed ISBN). Render nothing in that case.

import { useState } from "react";

import { itemApiBase, type Item } from "@/lib/item";

export function KeepaChart({ item }: { item: Pick<Item, "kind" | "id"> }) {
  const [errored, setErrored] = useState(false);
  if (errored) return null;
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <h3 className="mb-2 text-sm font-medium">
        Amazon UK price history{" "}
        <span className="text-xs font-normal text-muted-foreground">via Keepa</span>
      </h3>
      {/* Keepa renders the PNG on white with its own colours; in dark mode a
          bare white image glares, so it sits on a white, rounded mat that
          reads as "an embedded chart" rather than a hole in the page. */}
      <div className="rounded bg-white dark:p-1">
        <img
          src={`${itemApiBase(item.kind)}/${item.id}/keepa-chart.png`}
          alt="Amazon UK price history from Keepa"
          loading="lazy"
          onError={() => setErrored(true)}
          className="w-full rounded"
        />
      </div>
    </div>
  );
}
