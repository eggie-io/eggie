import type { ReactNode } from "react";
import { cx } from "../cx";
import s from "./Card.module.css";

export function Card({
  as: Tag = "div",
  className,
  children,
}: {
  as?: "div" | "section" | "ul";
  className?: string;
  children: ReactNode;
}) {
  return <Tag className={cx(s.card, className)}>{children}</Tag>;
}
