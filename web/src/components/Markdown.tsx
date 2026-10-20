import { Fragment, type ReactNode } from "react";

/** A small Markdown subset rendered to React elements, never to innerHTML, so text that came from course
 * files or a model can't inject markup. Supports paragraphs, bullet and numbered lists, headings (as
 * bold lines), **bold**, *italic*, `code`, fenced code blocks and [S1] citation chips. */

interface Props {
  text: string;
  onCite?: (n: number) => void;
  citable?: Set<number>;
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[S\d+\]|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

function inline(s: string, props: Props, keyBase: string): ReactNode[] {
  return s.split(INLINE).filter(Boolean).map((part, i) => {
    const key = `${keyBase}-${i}`;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={key}>{part.slice(1, -1)}</code>;
    const cite = part.match(/^\[S(\d+)\]$/);
    if (cite) {
      const n = Number(cite[1]);
      if (props.citable && !props.citable.has(n)) return null;
      return (
        <button key={key} type="button" className="cite" onClick={() => props.onCite?.(n)}
                aria-label={`Show source ${n}`}>
          S{n}
        </button>
      );
    }
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={key}>{part}</Fragment>;
  });
}

export function Markdown(props: Props) {
  const lines = props.text.replace(/\r/g, "").split("\n");
  const out: ReactNode[] = [];
  let para: string[] = [];
  let list: { type: "ul" | "ol"; items: string[] } | null = null;
  let code: string[] | null = null;

  const flushPara = () => {
    if (para.length) {
      const k = `p${out.length}`;
      const parts: ReactNode[] = [];
      para.forEach((l, i) => {
        if (i) parts.push(<br key={`${k}b${i}`} />);
        parts.push(...inline(l, props, `${k}-${i}`));
      });
      out.push(<p key={k}>{parts}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const k = `l${out.length}`;
      const items = list.items.map((it, i) => <li key={`${k}-${i}`}>{inline(it, props, `${k}-${i}`)}</li>);
      out.push(list.type === "ul" ? <ul key={k}>{items}</ul> : <ol key={k}>{items}</ol>);
      list = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (line.trimStart().startsWith("```")) {
      if (code) {
        out.push(<pre key={`c${out.length}`}><code>{code.join("\n")}</code></pre>);
        code = null;
      } else {
        flushPara();
        flushList();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(rawLine);
      continue;
    }
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.*)/);
    if (heading) {
      flushPara();
      flushList();
      out.push(<p key={`h${out.length}`} className="md-heading">{inline(heading[1], props, `h${out.length}`)}</p>);
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (bullet || numbered) {
      flushPara();
      const type = bullet ? "ul" : "ol";
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [] };
      }
      list.items.push((bullet ?? numbered)![1]);
      continue;
    }
    flushList();
    para.push(line);
  }
  if (code) out.push(<pre key={`c${out.length}`}><code>{(code as string[]).join("\n")}</code></pre>);
  flushPara();
  flushList();
  return <div className="md">{out}</div>;
}
