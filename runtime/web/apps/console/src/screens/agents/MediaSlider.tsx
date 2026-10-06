import { useState, type KeyboardEvent } from "react";
import { cx } from "@eggie/ui";
import type { Media } from "../../agents/catalog";
import { BACK, CHEVRON_RIGHT } from "../icons";
import s from "./Agents.module.css";

// One step's screenshots and videos. Keyed by the step, so it starts at the
// first slide whenever the step changes.
export function MediaSlider({ media, alt }: { media: Media[]; alt: string }) {
  const [index, setIndex] = useState(0);
  if (media.length === 0) return <div className={s.placeholder}>{alt}</div>;

  const many = media.length > 1;
  const item = media[index];
  const label = many ? `${alt} (${index + 1} of ${media.length})` : alt;
  const go = (next: number) => setIndex((next + media.length) % media.length);
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowLeft") go(index - 1);
    else if (event.key === "ArrowRight") go(index + 1);
    else return;
    event.preventDefault();
  };

  return (
    <div className={s.slider} role={many ? "group" : undefined} aria-roledescription={many ? "slider" : undefined} onKeyDown={many ? onKeyDown : undefined}>
      {item.kind === "video" ? (
        // Keyed so moving between two videos loads the new file.
        <video key={item.src} className={s.shot} src={item.src} aria-label={label} autoPlay loop muted playsInline controls />
      ) : (
        <img key={item.src} className={s.shot} src={item.src} alt={label} />
      )}
      {many && (
        <>
          <button type="button" className={cx(s.slideArrow, s.slidePrev)} aria-label="Previous screenshot" onClick={() => go(index - 1)}>{BACK}</button>
          <button type="button" className={cx(s.slideArrow, s.slideNext)} aria-label="Next screenshot" onClick={() => go(index + 1)}>{CHEVRON_RIGHT}</button>
          <div className={s.slideDots}>
            {media.map((dot, n) => (
              <button
                key={dot.src}
                type="button"
                className={cx(s.slideDot, n === index && s.slideDotOn)}
                aria-label={`Show ${n + 1} of ${media.length}`}
                aria-current={n === index ? "true" : undefined}
                onClick={() => setIndex(n)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
