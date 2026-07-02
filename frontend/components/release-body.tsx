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

// v1.3.0: which ## headings to drop when ``hideInstallSections`` is on.
// Case-insensitive, matched against the trimmed heading text.
const INSTALL_HEADINGS = new Set(["images", "cli", "deploy"]);

export function ReleaseBody({
  body,
  hideInstallSections = false,
}: {
  body: string | null;
  // v1.3.0: when true, sections whose ## heading is exactly "Images",
  // "CLI", or "Deploy" (the auto-generated docker/pip/install trio)
  // are skipped. Everything BETWEEN the skipped heading and the next
  // ## heading is dropped too. Used by the What's New page to render
  // "above-fold" clean content, and the same body un-filtered inside
  // a collapsed "Install details" toggle.
  hideInstallSections?: boolean;
}) {
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
  // v1.3.0: when we hit an install-section ## heading, drop every
  // subsequent line until the next heading of any level.
  let skippingSection = false;

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
      if (!skippingSection) {
        flushList();
        flushPara();
      }
      continue;
    }
    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    const bulletMatch = line.match(/^[-*]\s+(.+)$/);
    if (headingMatch) {
      flushList();
      flushPara();
      const level = headingMatch[1].length;
      const text = headingMatch[2];
      // v1.3.0: install-section filter. A ## heading whose text is
      // one of Images / CLI / Deploy starts a skipped run; any other
      // heading (## or otherwise) ends it.
      if (hideInstallSections) {
        const normalized = text.trim().toLowerCase();
        if (INSTALL_HEADINGS.has(normalized)) {
          skippingSection = true;
          continue;
        }
        skippingSection = false;
      }
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
    } else if (skippingSection) {
      // Body of an install section — drop it.
      continue;
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
