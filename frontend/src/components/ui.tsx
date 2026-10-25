import { LoaderCircle, type LucideIcon } from 'lucide-react';
import { forwardRef, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';

/** Shared building blocks for the light theme. Keep variants few; the UI should stay quiet. */

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ');

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  icon?: LucideIcon;
  busy?: boolean;
};

const VARIANT = {
  primary: 'bg-accent-ink text-white hover:bg-accent-ink-hover disabled:hover:bg-accent-ink',
  secondary: 'border border-line-strong bg-surface text-ink hover:bg-subtle disabled:hover:bg-surface',
  ghost: 'text-muted hover:bg-subtle hover:text-ink disabled:hover:bg-transparent',
  danger: 'border border-error/40 bg-surface text-error hover:bg-error/8',
} as const;
const SIZE = { sm: 'h-7 gap-1.5 px-2.5 text-[12px]', md: 'h-8 gap-2 px-3 text-[13px]' } as const;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon: Icon, busy = false, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      {...rest}
      className={cx(
        'inline-flex shrink-0 items-center justify-center rounded-md font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-45',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
    >
      {busy ? <Spinner size={14} /> : Icon && <Icon size={size === 'sm' ? 14 : 16} strokeWidth={1.75} aria-hidden />}
      {children}
    </button>
  );
});

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon;
  /** Required: icon buttons have no visible text. */
  label: string;
  size?: 'sm' | 'md';
  active?: boolean;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, label, size = 'md', active = false, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={cx(
        'inline-flex shrink-0 items-center justify-center rounded-md transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-40',
        size === 'sm' ? 'size-6' : 'size-8',
        active ? 'bg-accent-soft text-accent-ink' : 'text-muted hover:bg-subtle hover:text-ink',
        className,
      )}
    >
      <Icon size={size === 'sm' ? 14 : 16} strokeWidth={1.75} aria-hidden />
    </button>
  );
});

const TONE = {
  neutral: 'bg-subtle text-muted',
  accent: 'bg-accent-soft text-accent-ink',
  ok: 'bg-ok/10 text-ok',
  warn: 'bg-warn/10 text-warn',
  error: 'bg-error/10 text-error',
  info: 'bg-inspect/10 text-inspect',
} as const;
export type Tone = keyof typeof TONE;

export function Badge({ tone = 'neutral', dot = false, className, children }: {
  tone?: Tone;
  dot?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={cx('inline-flex shrink-0 items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-[11px] font-medium',
      TONE[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

/** Small uppercase label above a group of rows. */
export function SectionLabel({ children, action, className }: { children: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cx('flex h-8 items-center gap-2 px-3', className)}>
      <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">{children}</h3>
      <span className="flex-1" />
      {action}
    </div>
  );
}

export function Spinner({ size = 14, className }: { size?: number; className?: string }) {
  return <LoaderCircle size={size} strokeWidth={2} className={cx('animate-spin-slow', className)} aria-hidden />;
}

/** A floating card over the viewer (overlays, reply card, prompt pill share this look). */
export function FloatCard({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cx('rounded-lg border border-line bg-surface/95 shadow-float backdrop-blur', className)}>
      {children}
    </div>
  );
}

/** An inline name field: Enter or blur saves, Escape cancels (reports ''). */
export function RenameField({ initial, placeholder, onDone, className }: {
  initial: string;
  placeholder?: string;
  onDone: (name: string) => void;
  className?: string;
}) {
  const [value, setValue] = useState(initial);
  const done = useRef(false);
  const finish = (name: string) => {
    if (done.current) return;
    done.current = true;
    onDone(name.trim());
  };
  return (
    <input
      autoFocus
      value={value}
      maxLength={120}
      placeholder={placeholder}
      aria-label="Workspace name"
      onChange={(e) => setValue(e.target.value)}
      onFocus={(e) => e.currentTarget.select()}
      onBlur={() => finish(value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') finish(value);
        if (e.key === 'Escape') finish('');
      }}
      className={cx('h-7 w-full rounded-md border border-accent/60 bg-surface px-2 text-[13.5px] outline-none', className)}
    />
  );
}

export { cx };

