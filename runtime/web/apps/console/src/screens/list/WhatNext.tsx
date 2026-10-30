import type { ReactNode } from "react";
import { BrandMark, Icon, cx } from "@eggie/ui";
import { NO_AGENT, atSentenceStart } from "../../agents/current";
import s from "./WhatNext.module.css";

function Row({ mark, name, mine = false, children }: { mark: ReactNode; name: string; mine?: boolean; children: ReactNode }) {
  return (
    <li className={cx(s.row, mine && s.mine)}>
      {mark}
      <span className={s.name}>{name}</span>
      <span className={s.text}>{children}</span>
    </li>
  );
}

const egg = (
  <span className={s.egg} aria-hidden="true">
    <BrandMark size={14} />
  </span>
);

export function WhatNext({ agent, first, middle }: { agent: string; first: string; middle: string }) {
  const mark = (
    <span className={s.agentMark} aria-hidden="true">
      {agent === NO_AGENT ? <Icon name="terminal" /> : agent.charAt(0).toUpperCase()}
    </span>
  );
  const name = atSentenceStart(agent);
  return (
    <div className={s.block}>
      <p className={s.caption}>What happens next</p>
      <ol className={s.rows}>
        <Row mark={egg} name="Eggie">{first}</Row>
        <Row mark={mark} name={name} mine>{middle}</Row>
        <Row mark={mark} name={name}>Starts it with <code>eggie up</code> and gives you an address</Row>
      </ol>
    </div>
  );
}
