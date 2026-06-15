import { clsx } from "clsx";
import { ButtonHTMLAttributes, forwardRef } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost" | "glass";
  size?: "sm" | "md" | "lg";
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "secondary", size = "md", className, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={clsx(
          "inline-flex items-center justify-center rounded-xl font-medium transition-all duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--bg)]",
          "active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none",
          {
            "bg-primary-600 text-white hover:bg-primary-500 shadow-sm hover:shadow-glow-primary":
              variant === "primary",
            "bg-[color:var(--raised)] text-[color:var(--text)] hover:bg-[color:var(--card-hover)] border border-[color:var(--border)]":
              variant === "secondary",
            "bg-red-600 text-white hover:bg-red-700":
              variant === "danger",
            "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)]":
              variant === "ghost",
            "glass-card text-[color:var(--text)] hover:bg-[color:var(--raised)] border-[color:var(--glass-border)]":
              variant === "glass",
          },
          {
            "text-xs px-3 py-1.5 gap-1.5": size === "sm",
            "text-sm px-4 py-2 gap-2": size === "md",
            "text-base px-5 py-2.5 gap-2.5": size === "lg",
          },
          className,
        )}
        {...props}
      />
    );
  },
);

Button.displayName = "Button";
