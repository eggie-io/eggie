import { Link } from "react-router";
import { Card } from "@eggie/ui";
import { overviewRows, primaryFirst } from "../../domains/domains";
import type { Project } from "../../projects/types";
import { useSite } from "../../site/SiteContext";
import { DomainStatus } from "./DomainStatus";
import s from "./ProjectPage.module.css";

export function DomainBlock({ project }: { project: Project }) {
  const site = useSite();
  if (typeof site !== "string") return null;
  const to = `/p/${encodeURIComponent(project.id)}/domain`;
  const domains = primaryFirst(project.domains);
  const rows = overviewRows(project.domains);

  return (
    <section className={s.section}>
      <div className={s.domainHead}>
        <h2 className={s.label}>Your own domain</h2>
        {rows.length > 0 && <Link to={to} className={s.manage}>Manage</Link>}
      </div>
      {rows.length === 0 ? (
        <Card className={s.domainEmpty}>
          <p className={s.domainText}>Open this project on your own domain, like example.com.</p>
          <Link to={to} className={s.linkButton}>Attach your domain</Link>
        </Card>
      ) : (
        <Card as="ul" className={s.domains}>
          {rows.map((row, i) => (
            <li key={domains[i].hostname} className={s.domainRow}>
              <span className={s.domainHost}>{row.host}</span>
              <DomainStatus status={domains[i].status} />
            </li>
          ))}
        </Card>
      )}
    </section>
  );
}
