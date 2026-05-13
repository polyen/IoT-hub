import { clsx } from "clsx";
import { ButtonHTMLAttributes, forwardRef } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "secondary", size = "md", className, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={clsx(
          "inline-flex items-center justify-center rounded-lg font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50 disabled:pointer-events-none",
          {
            "bg-blue-600 text-white hover:bg-blue-700": variant === "primary",
            "bg-slate-700 text-slate-100 hover:bg-slate-600 dark:bg-slate-700 dark:hover:bg-slate-600 light:bg-slate-200 light:text-slate-900 light:hover:bg-slate-300":
              variant === "secondary",
            "bg-red-700 text-white hover:bg-red-600": variant === "danger",
            "text-slate-300 hover:text-white hover:bg-slate-700 light:text-slate-600 light:hover:text-slate-900 light:hover:bg-slate-200":
              variant === "ghost",
          },
          {
            "text-xs px-2.5 py-1.5 gap-1.5": size === "sm",
            "text-sm px-3.5 py-2 gap-2": size === "md",
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
