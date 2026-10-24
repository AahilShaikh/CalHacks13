/** Figma: AgentMessage (4:106). Agent narration between tool calls; also shows the user's prompts. */
export function AgentMessage({ text, author = 'Aida' }: { text: string; author?: 'Aida' | 'You' }) {
  return (
    <div className="flex w-full flex-col gap-1 py-1">
      <span className={`text-[11px] font-semibold ${author === 'Aida' ? 'text-cad' : 'text-muted'}`}>{author}</span>
      <p className="whitespace-pre-wrap break-words text-[13px] leading-[1.45]">{text}</p>
    </div>
  );
}
