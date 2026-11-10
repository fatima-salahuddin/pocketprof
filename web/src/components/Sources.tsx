import { useEffect, useRef } from "react";
import type { NoteSource, Source } from "../lib/types";

/** The evidence panel beside an answer, notes or a practice item (UI-2: citations open the source). */
export function SourcesPanel({ sources, focus, onClose }: { sources: Source[]; focus: number | null; onClose: () => void }) {
  const refs = useRef<Record<number, HTMLLIElement | null>>({});
  useEffect(() => {
    if (focus != null) refs.current[focus]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focus]);
  return (
    <aside className="sources" aria-label="Sources">
      <header>
        <h2>Sources</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close sources">×</button>
      </header>
      <ol>
        {sources.map((s) => (
          <li key={s.n} ref={(el) => (refs.current[s.n] = el)} className={`${s.cited ? "cited" : ""} ${s.n === focus ? "focus" : ""}`}>
            <div className="src-head">
              <span className="cite static">S{s.n}</span>
              <span className="src-file">{s.file}</span>
            </div>
            <p className="src-where">
              {[s.location, s.week ? `Week ${s.week}` : null, s.post_title].filter(Boolean).join(" · ")}
            </p>
            {s.snippet && <p className="src-snippet">{s.snippet}</p>}
            {s.ocr && <p className="src-flag">Read with OCR; check the original.</p>}
            {s.url && <a href={s.url} target="_blank" rel="noreferrer">Open on Classroom</a>}
          </li>
        ))}
      </ol>
    </aside>
  );
}

/** Notes and practice items store a slimmer source record; the panel takes the chat shape. */
export function asSources(records: NoteSource[]): Source[] {
  return records.map((r) => ({
    n: r.n, cited: true, kind: r.kind, post_title: r.post_title, topic: r.topic, file: r.file,
    location: r.location, week: r.week, date: r.date, url: r.url, snippet: r.snippet ?? "", score: 0, ocr: false,
  }));
}
