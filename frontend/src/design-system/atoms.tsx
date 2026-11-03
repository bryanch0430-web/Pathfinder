import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./icons";
import { useI18n } from "../lib/i18n";

type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "info" | "busy" | "ink";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "ink" | "soft";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  block?: boolean;
  iconLeft?: IconName;
  iconRight?: IconName;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  block = false,
  iconLeft,
  iconRight,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={["pf-button", className].filter(Boolean).join(" ")}
      data-variant={variant}
      data-size={size}
      data-block={block || undefined}
      data-loading={loading || undefined}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <span className="pf-spinner" aria-hidden="true" /> : iconLeft ? <Icon name={iconLeft} /> : null}
      {children}
      {iconRight ? <Icon name={iconRight} /> : null}
    </button>
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName;
  label: string;
  tone?: "default" | "brand";
}

export function IconButton({ icon, label, tone = "default", className, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      className={["pf-icon-btn", className].filter(Boolean).join(" ")}
      data-tone={tone}
      aria-label={label}
      title={label}
      {...rest}
    >
      <Icon name={icon} />
    </button>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  iconLeft?: IconName;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, iconLeft, className, ...rest },
  ref,
) {
  if (iconLeft) {
    return (
      <span className="pf-input-wrap">
        <span className="pf-input-wrap__icon">
          <Icon name={iconLeft} size={15} />
        </span>
        <input
          ref={ref}
          className={["pf-input", className].filter(Boolean).join(" ")}
          data-invalid={invalid || undefined}
          {...rest}
        />
      </span>
    );
  }
  return (
    <input
      ref={ref}
      className={["pf-input", className].filter(Boolean).join(" ")}
      data-invalid={invalid || undefined}
      {...rest}
    />
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean }>(
  function Textarea({ invalid, className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={["pf-textarea", className].filter(Boolean).join(" ")}
        data-invalid={invalid || undefined}
        {...rest}
      />
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <select ref={ref} className={["pf-select", className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </select>
  );
});

export function Checkbox({
  label,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="pf-checkbox">
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}

export function Switch({
  label,
  checked,
  onChange,
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, "onChange"> & {
  label: ReactNode;
  onChange?: (checked: boolean) => void;
}) {
  return (
    <label className="pf-switch">
      <input type="checkbox" checked={checked} onChange={(event) => onChange?.(event.target.checked)} {...rest} />
      <span className="pf-switch__track" aria-hidden="true">
        <span className="pf-switch__thumb" />
      </span>
      <span>{label}</span>
    </label>
  );
}

export function Badge({
  tone = "neutral",
  dot = false,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span className="pf-badge" data-tone={tone}>
      {dot ? <span className="pf-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function Spinner({ size = "md", label }: { size?: "md" | "lg"; label?: string }) {
  const { t } = useI18n();
  return <span className="pf-spinner" data-size={size} role="status" aria-label={label ?? t("common.loading")} />;
}

export function Avatar({
  name,
  src,
  size = "md",
}: {
  name: string;
  src?: string;
  size?: "sm" | "md" | "lg";
}) {
  const initials = name
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span className="pf-avatar" data-size={size}>
      {src ? <img src={src} alt={name} /> : <span aria-hidden="true">{initials}</span>}
      {src ? null : <span className="pf-visually-hidden">{name}</span>}
    </span>
  );
}

export function Heading({
  level = 2,
  children,
  className,
  id,
}: {
  level?: 1 | 2 | 3 | 4 | 5 | 6;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  const Tag = `h${level}` as keyof JSX.IntrinsicElements;
  return (
    <Tag id={id} className={["pf-heading", className].filter(Boolean).join(" ")} data-level={level}>
      {children}
    </Tag>
  );
}

export function Text({
  as: Tag = "p",
  size = "md",
  tone = "default",
  weight,
  children,
  className,
  ...rest
}: {
  as?: "p" | "span" | "div" | "small";
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  tone?: "default" | "secondary" | "tertiary" | "brand" | "success" | "danger";
  weight?: "regular" | "medium" | "semibold" | "bold";
  children: ReactNode;
  className?: string;
} & HTMLAttributes<HTMLElement>) {
  return (
    <Tag
      className={["pf-text", className].filter(Boolean).join(" ")}
      data-size={size}
      data-tone={tone}
      data-weight={weight}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export function Divider({ vertical = false }: { vertical?: boolean }) {
  return <hr className="pf-divider" data-orientation={vertical ? "vertical" : "horizontal"} />;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])';

/**
 * What a modal does besides showing: Escape closes it, the page behind stops scrolling, focus moves into
 * it and stays there while Tab is pressed, and goes back to what opened it when it closes. Put the returned
 * ref and `tabIndex={-1}` on the element that has `role="dialog"`; focus lands on that element first.
 */
export function useModalBehaviour<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const modalRef = useRef<T | null>(null);
  // The latest onClose. A parent that passes a new function on every render must not restart the effect, which would move focus again.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const modal = modalRef.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    modal?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !modal) return;
      const items = Array.from(modal.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((item) => item.getClientRects().length > 0);
      const active = document.activeElement;
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) {
        event.preventDefault();
        modal.focus();
      } else if (event.shiftKey && (active === first || active === modal || !modal.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !modal.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    // Focus that leaves the modal in a way Tab does not show here (out of an embedded frame, say) is brought back.
    const onFocusIn = (event: FocusEvent) => {
      if (modal && event.target instanceof Node && !modal.contains(event.target)) modal.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
      document.body.style.overflow = previous;
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open]);
  return modalRef;
}

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const { t } = useI18n();
  const modalRef = useModalBehaviour<HTMLDivElement>(open, onClose);
  const titleId = useId();
  if (!open) return null;
  return createPortal(
    <div className="pf-dialog-overlay" role="presentation" onMouseDown={onClose}>
      <div
        ref={modalRef}
        className="pf-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="pf-dialog__head">
          <Heading level={4} id={titleId}>{title}</Heading>
          <IconButton icon="close" label={t("common.closeDialog")} onClick={onClose} />
        </div>
        <div className="pf-dialog__body">
          {children}
        </div>
        {footer ? <div className="pf-dialog__foot">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const modalRef = useModalBehaviour<HTMLElement>(open, onClose);
  if (!open) return null;
  return createPortal(
    <div className="pf-drawer-overlay" role="presentation" onMouseDown={onClose}>
      <aside
        ref={modalRef}
        className="pf-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="pf-row pf-row--between">
          <Heading level={4}>{title}</Heading>
          <IconButton icon="close" label={t("common.closePanel")} onClick={onClose} />
        </div>
        {children}
      </aside>
    </div>,
    document.body,
  );
}
