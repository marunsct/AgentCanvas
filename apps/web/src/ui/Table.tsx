/** A table with headers, so no page forgets them: `columns` are the header cells (`hidden` ones, such as a column of
 * row actions, are read out but not shown), `children` the rows. An empty list is the page's `EmptyState`, not an
 * empty table. */
export interface Column { label: string; className?: string; hidden?: boolean }

export function Table({ columns, children, className, testId, bodyTestId, caption }: {
  columns: Column[]; children: React.ReactNode; className?: string; testId?: string; bodyTestId?: string; caption?: string;
}) {
  return (
    <table className={`table${className ? ` ${className}` : ""}`} data-testid={testId}>
      {caption && <caption className="visually-hidden">{caption}</caption>}
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.label} className={c.className} scope="col">
              {c.hidden ? <span className="visually-hidden">{c.label}</span> : c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody data-testid={bodyTestId}>{children}</tbody>
    </table>
  );
}
