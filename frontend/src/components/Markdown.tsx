import type { ReactNode } from 'react';

/**
 * The small subset of Markdown the agent writes: paragraphs, headings, bullet and numbered
 * lists, fenced code, **bold**, *italic* and `code`. Builds elements; never injects HTML.
 */
export function Markdown({ text }: { text: string }) {
  return <div className="flex flex-col gap-2">{blocks(text)}</div>;
}

function blocks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.trimStart().startsWith('```')) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) code.push(lines[i++]);
      i++;
      out.push(
        <pre key={key++} className="scroll-thin max-h-48 overflow-auto rounded-md bg-subtle p-2 font-mono text-[11px] leading-relaxed">
          {code.join('\n')}
        </pre>,
      );
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      out.push(<p key={key++} className="font-semibold">{inline(heading[1])}</p>);
      i++;
      continue;
    }
    const bullet = /^\s*[-*•]\s+/;
    const numbered = /^\s*\d+[.)]\s+/;
    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line);
      const pattern = ordered ? numbered : bullet;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        let item = lines[i++].replace(pattern, '');
        // Indented continuation lines belong to the item.
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !bullet.test(lines[i]) && !numbered.test(lines[i])) {
          item += ` ${lines[i++].trim()}`;
        }
        items.push(item);
      }
      const List = ordered ? 'ol' : 'ul';
      out.push(
        <List key={key++} className={`flex flex-col gap-1 pl-5 ${ordered ? 'list-decimal' : 'list-disc'} marker:text-faint`}>
          {items.map((item, n) => <li key={n}>{inline(item)}</li>)}
        </List>,
      );
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*[-*•]\s|\s*\d+[.)]\s)/.test(lines[i])) {
      para.push(lines[i++].trim());
    }
    out.push(<p key={key++}>{inline(para.join(' '))}</p>);
  }
  return out;
}

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let key = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const token = match[0];
    if (token.startsWith('`')) {
      out.push(<code key={key++} className="rounded-sm bg-subtle px-1 py-px font-mono text-[0.92em]">{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**') || token.startsWith('__')) {
      out.push(<strong key={key++} className="font-semibold">{token.slice(2, -2)}</strong>);
    } else {
      out.push(<em key={key++}>{token.slice(1, -1)}</em>);
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
