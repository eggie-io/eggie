import { createElement, type ReactNode } from "react";
import type { Rich } from "../../agents/rich";

// Renders the tree parseRich built: only its tags, never raw HTML.
export function RichText({ nodes }: { nodes: Rich[] }): ReactNode {
  return nodes.map((node, index) =>
    typeof node === "string" ? node : createElement(node.tag, { key: index }, node.tag === "br" ? undefined : <RichText nodes={node.children} />),
  );
}
