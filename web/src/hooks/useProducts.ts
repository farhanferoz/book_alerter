// React Query hook over `GET /api/products`.
//
// Mirror of `useBooks`. Backend supports `include_archived` only — sort and
// signal filters are applied client-side in ProductsDashboard.

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/api/client";
import type { components } from "@/api/schema";

export type Product = components["schemas"]["ProductOut"];

export type ProductsQueryParams = {
  include_archived?: boolean;
};

export function useProducts(params: ProductsQueryParams = {}) {
  return useQuery<Product[]>({
    queryKey: ["products", params],
    queryFn: async () => {
      const search = new URLSearchParams();
      if (params.include_archived) {
        search.set("include_archived", "true");
      }
      const qs = search.toString();
      const path = qs ? `/api/products?${qs}` : "/api/products";
      const body = await apiGet(path as "/api/products");
      return body as Product[];
    },
  });
}
