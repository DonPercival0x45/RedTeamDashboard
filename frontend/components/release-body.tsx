// Minimal renderer for GitHub Release body Markdown.
//
// The auto-generated changelog GitHub Releases produces is a tight subset
// of Markdown: ## headings, *-bulleted lists, **bold**, [text](url) links,
// and the trailing "**Full Changelog**: <url>" line. Hand-curated bodies
// look the same. Rolling 80 lines of regex avoids pulling react-markdown
// (+ its remark/unified dep tree) into the bundle for one component.
//
// What we DON'T render: tables, code blocks, images, inline HTML, footnotes,
// nested lists. If a future release body needs them, swap this for a real
// renderer.

import type { JSX } from "react";

function renderInline(text: string): (JSX.Element | string)[] {
  // [text](url) → <a>, **text** → <strong>. Sequential single-pass; the
  // two patterns don't overlap in the GH-release dialect we target.
  const parts: (JSX.Element | string)[] = [];
  const pattern = /\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/g;
  let lastIdx = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIdx) parts.push(text.slice(lastIdx, match.index));
    if (match[1] !== undefined) {
      parts.push(
        <a
          key={key++}
          href={match[2]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-foreground underline decoration-dotted hover:decoration-solid"
        >
          {match[1]}
        </a>,
      );
    } else if (match[3] !== undefined) {
      parts.push(
        <strong key={key++} className="font-semibold text-foreground">
          {match[3]}
        </strong>,
      );
    }
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx));
  return parts.length > 0 ? parts : [text];
}

export function ReleaseBody({ body }: { body: string | null }) {
  if (!body || !body.trim()) {
    return (
      <p className="text-sm italic text-muted-foreground">
        (no release notes provided)
      </p>
    );
  }
  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const blocks: JSX.Element[] = [];
  let listBuffer: string[] = [];
  let paraBuffer: string[] = [];
  let blockKey = 0;

  const flushList = () => {
    if (listBuffer.length === 0) return;
    blocks.push(
      <ul
        key={blockKey++}
        className="ml-4 list-disc space-y-1 text-sm text-muted-foreground"
      >
        {listBuffer.map((item, i) => (
          <li key={i}>{renderInline(item)}</li>
        ))}
      </ul>,
    );
    listBuffer = [];
  };

  const flushPara = () => {
    if (paraBuffer.length === 0) return;
    blocks.push(
      <p key={blockKey++} className="text-sm text-muted-foreground">
        {renderInline(paraBuffer.join(" "))}
      </p>,
    );
    paraBuffer = [];
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushList();
      flushPara();
      continue;
    }
    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    const bulletMatch = line.match(/^[-*]\s+(.+)$/);
    if (headingMatch) {
      flushList();
      flushPara();
      const level = headingMatch[1].length;
      const text = headingMatch[2];
      const Tag = (level === 1 ? "h2" : level === 2 ? "h3" : "h4") as
        | "h2"
        | "h3"
        | "h4";
      const sizeClass =
        level === 1
          ? "text-base font-semibold"
          : level === 2
            ? "text-sm font-semibold"
            : "text-xs font-semibold uppercase tracking-wide";
      blocks.push(
        <Tag key={blockKey++} className={`${sizeClass} text-foreground`}>
          {renderInline(text)}
        </Tag>,
      );
    } else if (bulletMatch) {
      flushPara();
      listBuffer.push(bulletMatch[1]);
    } else {
      flushList();
      paraBuffer.push(line);
    }
  }
  flushList();
  flushPara();

  return <div className="space-y-2">{blocks}</div>;
}
