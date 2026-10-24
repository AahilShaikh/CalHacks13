import { useEffect, useRef } from 'react';
import { useStore } from '../store';
import { AgentMessage } from './AgentMessage';
import { ToolCard } from './ToolCard';

const NOTE_TONE = { muted: 'text-faint', error: 'text-error', ok: 'text-faint' } as const;

/** The Activity tab: agent narration and tool calls, newest at the bottom. */
export function Feed() {
  const feed = useStore((s) => s.feed);
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // Follow new items only while the user is already at the bottom.
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [feed]);

  if (!feed.length) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] leading-[1.45] text-faint">
        Agent messages and every tool call it makes will appear here.
      </div>
    );
  }

  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="scroll-thin flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1"
    >
      {feed.map((item) => {
        switch (item.kind) {
          case 'agent':
            return <AgentMessage key={item.id} text={item.text} />;
          case 'user':
            return <AgentMessage key={item.id} text={item.text} author="You" />;
          case 'tool':
            return <ToolCard key={item.id} item={item} />;
          case 'note':
            return (
              <p key={item.id} className={`py-1 text-center text-[11px] ${NOTE_TONE[item.tone]}`}>
                {item.text}
              </p>
            );
        }
      })}
    </div>
  );
}
