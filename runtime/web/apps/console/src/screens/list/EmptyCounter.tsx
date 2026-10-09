import { Button, Egg } from "@eggie/ui";
import { CHECK, FOLDER, GITHUB, PLUS } from "../icons";
import s from "./ProjectList.module.css";

export function EmptyCounter({
  onNew,
  onFolder,
  onGitHub,
  connectedAgent,
}: {
  onNew: () => void;
  onFolder: () => void;
  onGitHub: () => void;
  connectedAgent: string | null;
}) {
  return (
    <section className={s.empty}>
      {connectedAgent && (
        <p className={s.connected} role="status">
          {CHECK}{connectedAgent} is connected. Now give it something to work on.
        </p>
      )}
      <div className={s.emptyHead}>
        <Egg size={40} dim bob />
        <div className={s.emptyText}>
          <h1 className={s.emptyTitle}>An empty counter</h1>
          <p className={s.lead}>Projects live here. Make one and your coding agent finally has somewhere to put things.</p>
        </div>
      </div>
      <div className={s.cards}>
        <div className={`${s.card} ${s.cardPrimary}`}>
          <span className={s.cardIcon}>{PLUS}</span>
          <div className={s.cardText}>
            <h2 className={s.cardTitle}>Start from scratch</h2>
            <p className={s.cardBody}>An empty project with a name on it. Your coding agent takes it from there.</p>
          </div>
          <Button variant="primary" onClick={onNew}>New project</Button>
        </div>
        <div className={s.card}>
          <span className={`${s.cardIcon} ${s.cardIconMuted}`}>{FOLDER}</span>
          <div className={s.cardText}>
            <h2 className={s.cardTitle}>From a folder</h2>
            <p className={s.cardBody}>Already have one on this computer? Eggie copies it in. The original stays where it is.</p>
          </div>
          <Button onClick={onFolder}>Choose a folder</Button>
        </div>
        <div className={s.card}>
          <span className={`${s.cardIcon} ${s.cardIconMuted}`}>{GITHUB}</span>
          <div className={s.cardText}>
            <h2 className={s.cardTitle}>From GitHub</h2>
            <p className={s.cardBody}>Pull in a repo you already have. Connect GitHub once and pick it.</p>
          </div>
          <Button onClick={onGitHub}>Pick a repo</Button>
        </div>
      </div>
    </section>
  );
}
