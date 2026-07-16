// Lightweight Markdown renderer for the engagement strategy body.
//
// Handles the subset the strategist emits: #-### headings, bullet (`-`/`*`)
// and numbered (`1.`) lists, **bold**, `inline code`, [links](url), and
// paragraphs. No new dependency — mirrors release-body.tsx's approach of
// avoiding react-markdown (+ its remark/unified dep tree) for one consumer.
// If the strategy body ever needs tables / fenced code blocks / images, swap
// this for a real renderer.

import type { JSX } from "react";

function renderInline(text: string): (JSX.Element | string)[] {
  const parts: (JSX.Element | string)[] = [];
  // **bold** | `code` | [text](url)  — non-overlapping single pass.
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let lastIdx = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIdx) parts.push(text.slice(lastIdx, match.index));
    if (match[1] !== undefined) {
      parts.push(
        <strong key={key++} className="font-semibold text-foreground">
          {match[1]}
        </strong>,
      );
    } else if (match[2] !== undefined) {
      parts.push(
        <code
          key={key++}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-foreground"
        >
          {match[2]}
        </code>,
      );
    } else if (match[3] !== undefined) {
      parts.push(
        <a
          key={key++}
          href={match[4]}
          target="_blank"
          rel="noopener noreferrer"
          className="text-foreground underline decoration-dotted hover:decoration-solid"
        >
          {match[3]}
        </a>,
      );
    }
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx));
  return parts.length > 0 ? parts : [text];
}

export function StrategyMarkdown({ body }: { body: string }) {
  if (!body || !body.trim()) {
    return (
      <p className="text-sm italic text-muted-foreground">
        (no strategy body yet)
      </p>
    );
  }

  const lines = body.replace(/\r\n/g, "\n").split("\n");
  const blocks: JSX.Element[] = [];
  let bullets: string[] = [];
  let numbers: string[] = [];
  let para: string[] = [];
  let key = 0;

  const flushBullets = () => {
    if (bullets.length === 0) return;
    blocks.push(
      <ul
        key={key++}
        className="ml-5 list-disc space-y-1 text-sm text-muted-foreground"
      >
        {bullets.map((t, i) => (
          <li key={i}>{renderInline(t)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };
  const flushNumbers = () => {
    if (numbers.length === 0) return;
    blocks.push(
      <ol
        key={key++}
        className="ml-5 list-decimal space-y-1 text-sm text-muted-foreground"
      >
        {numbers.map((t, i) => (
          <li key={i}>{renderInline(t)}</li>
        ))}
      </ol>,
    );
    numbers = [];
  };
  const flushPara = () => {
    if (para.length === 0) return;
    blocks.push(
      <p key={key++} className="text-sm leading-relaxed text-muted-foreground">
        {renderInline(para.join(" "))}
      </p>,
    );
    para = [];
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (line === "") {
      flushBullets();
      flushNumbers();
      flushPara();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    const bullet = line.match(/^[-*]\s+(.+)$/);
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    if (heading) {
      flushBullets();
      flushNumbers();
      flushPara();
      const level = heading[1].length;
      // Shift heading levels down one for in-flyout visual hierarchy.
      const Tag = (`h${Math.min(level + 1, 5)}`) as "h2" | "h3" | "h4" | "h5";
      const cls =
        level === 1
          ? "text-lg font-semibold"
          : level === 2
            ? "text-base font-semibold"
            : level === 3
              ? "text-sm font-semibold"
              : "text-xs font-semibold uppercase tracking-wide";
      blocks.push(
        <Tag key={key++} className={`${cls} text-foreground`}>
          {renderInline(heading[2])}
        </Tag>,
      );
    } else if (bullet) {
      flushPara();
      flushNumbers();
      bullets.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      flushBullets();
      numbers.push(numbered[1]);
    } else {
      flushBullets();
      flushNumbers();
      para.push(line);
    }
  }
  flushBullets();
  flushNumbers();
  flushPara();

  return <div className="space-y-2">{blocks}</div>;
}
