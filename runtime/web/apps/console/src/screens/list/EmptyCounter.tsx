import type { ReactNode } from "react";
import { Button, Card, Icon, type IconName } from "@eggie/ui";
import s from "./ProjectList.module.css";

function Option({ icon, title, children, action }: { icon: IconName; title: string; children: ReactNode; action: ReactNode }) {
  return (
    <Card className={s.option}>
      <Icon name={icon} className={s.optionIcon} />
      <div className={s.optionText}>
        <h2 className={s.optionTitle}>{title}</h2>
        <p className={s.optionBody}>{children}</p>
      </div>
      {action}
    </Card>
  );
}

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
    <div className={s.empty}>
      {connectedAgent && (
        <p className={s.connected} role="status">
          <Icon name="check-circle" />
          {connectedAgent} is connected. Now give it something to work on.
        </p>
      )}
      <div className={s.emptyArt} aria-hidden="true"><Icon name="egg" size="md" /></div>
      <h1 className={s.emptyTitle}>An empty counter</h1>
      <p className={s.emptyText}>Projects live here. Make one and your coding agent finally has somewhere to put things.</p>
      <div className={s.options}>
        <Option
          icon="plus"
          title="Start from scratch"
          action={<Button variant="primary" icon="plus" onClick={onNew}>New project</Button>}
        >
          An empty project with a name on it. Your coding agent takes it from there.
        </Option>
        <Option
          icon="folder"
          title="From a folder"
          action={<Button icon="folder" onClick={onFolder}>Choose a folder</Button>}
        >
          Already have one on this computer? Eggie copies it in. The original stays where it is.
        </Option>
        <Option
          icon="branch"
          title="From GitHub"
          action={<Button icon="branch" onClick={onGitHub}>Pick a repo</Button>}
        >
          Pull in a repo you already have. Connect GitHub once and pick it.
        </Option>
      </div>
    </div>
  );
}
