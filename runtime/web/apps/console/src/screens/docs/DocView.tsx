import { createElement, type ReactNode } from "react";
import { Link } from "react-router";
import { Qr } from "../../components/Qr";
import { qrModules } from "../../components/qr";
import { openExternal } from "../../desktop/desktop";
import type { Block, Inline } from "../../docs/markdown";
import { docsRoute } from "../../docs/pages";
import { fileUrl } from "../../uploads/paths";
import s from "./DocsPage.module.css";

// Renders parseDoc's tree as elements; nothing from the page reaches raw HTML.
function Inlines({ id, nodes }: { id: string; nodes: Inline[] }): ReactNode {
  return nodes.map((node, i) => {
    if (typeof node === "string") return node;
    switch (node.kind) {
      case "strong":
      case "em":
      case "del":
        return createElement(node.kind, { key: i }, <Inlines id={id} nodes={node.children} />);
      case "code":
        return <code key={i} className={s.code}>{node.text}</code>;
      case "br":
        return <br key={i} />;
      case "image":
        return node.path === null
          ? <span key={i}>{node.alt}</span>
          : <img key={i} className={s.image} src={fileUrl(id, node.path)} alt={node.alt} />;
      case "link": {
        const children = <Inlines id={id} nodes={node.children} />;
        const target = node.target;
        if (target.kind === "page") return <Link key={i} to={docsRoute(id, target.page)}>{children}</Link>;
        if (target.kind === "external") {
          return (
            <a key={i} href={target.href} onClick={(e) => { e.preventDefault(); openExternal(target.href); }}>
              {children}
            </a>
          );
        }
        return <span key={i}>{children}</span>;
      }
    }
  });
}

function Blocks({ id, blocks }: { id: string; blocks: Block[] }): ReactNode {
  return blocks.map((block, i) => {
    switch (block.kind) {
      case "heading":
        return createElement(`h${block.level}`, { key: i }, <Inlines id={id} nodes={block.children} />);
      case "paragraph":
        return <p key={i}><Inlines id={id} nodes={block.children} /></p>;
      case "code":
        return <pre key={i} className={s.pre}><code>{block.text}</code></pre>;
      case "qr":
        return qrModules(block.value)
          ? <figure key={i} className={s.qr}><Qr value={block.value} label="QR code" /></figure>
          : <pre key={i} className={s.pre}><code>{block.value}</code></pre>;
      case "list": {
        const items = block.items.map((item, j) => <li key={j}><Blocks id={id} blocks={item} /></li>);
        return block.ordered ? <ol key={i} start={block.start}>{items}</ol> : <ul key={i}>{items}</ul>;
      }
      case "table":
        return (
          <div key={i} className={s.tableWrap}>
            <table>
              <thead>
                <tr>{block.header.map((cell, j) => <th key={j} style={{ textAlign: block.align[j] ?? undefined }}><Inlines id={id} nodes={cell} /></th>)}</tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>{row.map((cell, j) => <td key={j} style={{ textAlign: block.align[j] ?? undefined }}><Inlines id={id} nodes={cell} /></td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      case "quote":
        return <blockquote key={i}><Blocks id={id} blocks={block.children} /></blockquote>;
      case "hr":
        return <hr key={i} />;
    }
  });
}

export function DocView({ id, blocks }: { id: string; blocks: Block[] }) {
  return <article className={s.doc}><Blocks id={id} blocks={blocks} /></article>;
}
