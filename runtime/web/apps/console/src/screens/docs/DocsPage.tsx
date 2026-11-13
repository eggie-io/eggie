import { useMemo } from "react";
import { Link, useParams } from "react-router";
import { Notice, cx } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { parseDoc } from "../../docs/markdown";
import { docsRoute, pageTitle } from "../../docs/pages";
import { useDocPages, useDocSource, useDocSources } from "../../docs/queries";
import { actionError } from "../../projects/copy";
import { TitleRow } from "../project/TitleRow";
import { DocView } from "./DocView";
import s from "./DocsPage.module.css";

const SUB = "Notes your coding agent wrote for this project.";

export function DocsPage() {
  const { id = "", page } = useParams();
  const pages = useDocPages(id);
  const names = pages.data ?? [];
  const current = page ?? names[0];
  const listed = current !== undefined && names.includes(current);
  const source = useDocSource(id, listed ? current : undefined);
  const titles = useDocSources(id, names.length > 1 ? names : []);
  const blocks = useMemo(() => (source.data === undefined ? null : parseDoc(source.data)), [source.data]);

  const body = () => {
    if (pages.isError) return <Notice tone="danger">{actionError(pages.error)}</Notice>;
    if (!pages.data) return <p className={s.muted}>Turning the pages…</p>;
    if (names.length === 0) return <p className={s.muted}>No pages yet.</p>;
    const gone = !listed || (source.error instanceof ApiError && source.error.code === "file_not_found");
    if (gone) {
      return (
        <p className={s.muted}>
          This page is gone. <Link to={docsRoute(id)}>Open the first page</Link>
        </p>
      );
    }
    if (source.isError) return <Notice tone="danger">{actionError(source.error)}</Notice>;
    if (!blocks) return <p className={s.muted}>Turning the pages…</p>;
    return <DocView id={id} blocks={blocks} />;
  };

  return (
    <section className={s.page}>
      <TitleRow title="Docs" sub={SUB} />
      {names.length > 1 && (
        <nav className={s.tabs} aria-label="Pages">
          {names.map((name, i) => (
            <Link
              key={name}
              to={docsRoute(id, name)}
              className={cx(s.tab, name === current && s.tabCurrent)}
              aria-current={name === current ? "page" : undefined}
            >
              {pageTitle(name, titles[i]?.data)}
            </Link>
          ))}
        </nav>
      )}
      {body()}
    </section>
  );
}
