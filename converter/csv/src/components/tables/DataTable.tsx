// import {
//   useReactTable,
//   getCoreRowModel,
//   getSortedRowModel,
//   flexRender,
// } from "@tanstack/react-table";
// import { useMemo, useState } from "react";

// interface DataTableProps {
//   rows: Record<string, any>[];
// }

// export default function DataTable({ rows }: DataTableProps) {
//   const [sorting, setSorting] = useState([]);

//   const columns = useMemo(
//     () =>
//       rows.length
//         ? Object.keys(rows[0]).map((key) => ({
//             accessorKey: key,
//             header: key,
//           }))
//         : [],
//     [rows]
//   );

//   const table = useReactTable({
//     data: rows,
//     columns,
//     state: { sorting },
//     onSortingChange: setSorting,
//     getCoreRowModel: getCoreRowModel(),
//     getSortedRowModel: getSortedRowModel(),
//   });

//   if (!rows.length) return <p>No data</p>;

//   return (
//     <div className="table-wrapper">
//       <table className="data-table">
//         <thead>
//           {table.getHeaderGroups().map((hg) => (
//             <tr key={hg.id}>
//               {hg.headers.map((header) => (
//                 <th
//                   key={header.id}
//                   onClick={header.column.getToggleSortingHandler()}
//                   style={{ cursor: "pointer" }}
//                 >
//                   {flexRender(
//                     header.column.columnDef.header,
//                     header.getContext()
//                   )}
//                   {{
//                     asc: " 🔼",
//                     desc: " 🔽",
//                   }[header.column.getIsSorted()] ?? ""}
//                 </th>
//               ))}
//             </tr>
//           ))}
//         </thead>

//         <tbody>
//           {table.getRowModel().rows.map((row) => (
//             <tr key={row.id}>
//               {row.getVisibleCells().map((cell) => (
//                 <td key={cell.id}>
//                   {flexRender(
//                     cell.column.columnDef.cell,
//                     cell.getContext()
//                   )}
//                 </td>
//               ))}
//             </tr>
//           ))}
//         </tbody>
//       </table>
//     </div>
//   );
// }