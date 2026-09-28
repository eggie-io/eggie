import { Link } from "react-router";
import { cx } from "@omelet/ui";
import { filesRoute } from "../../uploads/paths";
import { FOLDER, GLOBE, MAGNIFIER, TRASH } from "../icons";
import s from "./ProjectPage.module.css";

export function Tiles({ id, publicLine, publicDisabled, onPublic, onAnalyze, onDelete }: {
  id: string;
  publicLine: string;
  publicDisabled: boolean;
  onPublic: () => void;
  onAnalyze: () => void;
  onDelete: () => void;
}) {
  return (
    <div className={s.tiles}>
      <button type="button" className={s.tile} onClick={onAnalyze}>{MAGNIFIER}Analyze</button>
      <Link to={filesRoute(id, "")} className={s.tile}>{FOLDER}Files</Link>
      <button type="button" className={cx(s.tile, publicDisabled && s.off)} disabled={publicDisabled} onClick={onPublic}>
        {GLOBE}Public address<small>{publicLine}</small>
      </button>
      <button type="button" className={cx(s.tile, s.danger)} onClick={onDelete}>{TRASH}Delete</button>
    </div>
  );
}
