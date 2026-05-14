// Generic table wrapper built on TanStack Table + shadcn `<Table>`. Mirrors
// the shadcn data-table pattern (https://ui.shadcn.com/docs/components/data-table)
// kept lean so it can be reused for alerts/observations in later phases.

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export interface DataTableProps<TData> {
  columns: ColumnDef<TData>[];
  data: TData[];
  initialSorting?: SortingState;
  emptyMessage?: string;
}

export function DataTable<TData>({
  columns,
  data,
  initialSorting,
  emptyMessage = "No rows.",
}: DataTableProps<TData>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting ?? []);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="rounded-md border border-border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((h) => {
                const canSort = h.column.getCanSort();
                const sortDir = h.column.getIsSorted();
                return (
                  <TableHead key={h.id}>
                    {h.isPlaceholder ? null : (
                      <button
                        type="button"
                        className={
                          canSort
                            ? "inline-flex items-center gap-1 hover:text-foreground"
                            : "cursor-default"
                        }
                        onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {canSort && sortDir === "asc" && <span aria-hidden>↑</span>}
                        {canSort && sortDir === "desc" && <span aria-hidden>↓</span>}
                      </button>
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={columns.length} className="text-center text-muted-foreground py-6">
                {emptyMessage}
              </TableCell>
            </TableRow>
          ) : (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
