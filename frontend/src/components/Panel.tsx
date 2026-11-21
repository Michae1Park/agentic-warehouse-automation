import type { ReactNode } from "react";

export default function Panel({ title, area, children }: { title: string; area: string; children: ReactNode }) {
  return (
    <section className="panel" style={{ gridArea: area }}>
      <h2>{title}</h2>
      <div className="panel-body">{children}</div>
    </section>
  );
}
