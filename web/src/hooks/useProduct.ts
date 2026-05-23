// React Query hooks for the single-product detail view. Mirror of useBook.

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiGet } from "@/api/client";
import type { components } from "@/api/schema";

export type Product = components["schemas"]["ProductOut"];
export type ProductObservationsPage =
  components["schemas"]["ProductObservationsPage"];
export type ProductObservation =
  components["schemas"]["ProductObservationOut"];

export function useProduct(id: number | null) {
  return useQuery<Product, ApiError>({
    queryKey: ["product", id],
    queryFn: async () => {
      const path = `/api/products/${id}` as "/api/products/{product_id}";
      const body = await apiGet(path);
      return body as Product;
    },
    enabled: id != null,
    retry: (count, err) => {
      if (err instanceof ApiError && err.status === 404) return false;
      return count < 2;
    },
  });
}

export function useProductObservations(id: number | null, limit = 500) {
  return useQuery<ProductObservationsPage, ApiError>({
    queryKey: ["product", id, "observations", limit],
    queryFn: async () => {
      const path = `/api/products/${id}/observations?limit=${limit}`;
      const body = await apiGet(
        path as "/api/products/{product_id}/observations",
      );
      return body as ProductObservationsPage;
    },
    enabled: id != null,
  });
}
