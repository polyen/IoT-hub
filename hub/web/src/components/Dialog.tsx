import * as RadixDialog from "@radix-ui/react-dialog";
import { clsx } from "clsx";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  children: React.ReactNode;
  className?: string;
}

export function Dialog({ open, onOpenChange, title, children, className }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 bg-black/60 z-40 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <RadixDialog.Content
          className={clsx(
            "fixed z-50 bg-slate-800 light:bg-white rounded-xl shadow-2xl border border-slate-700 light:border-slate-200 p-6 focus:outline-none",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            "left-[50%] top-[50%] translate-x-[-50%] translate-y-[-50%] w-full max-w-md max-h-[85vh] overflow-y-auto",
            className,
          )}
        >
          {title && (
            <RadixDialog.Title className="text-lg font-semibold mb-4 text-slate-100 light:text-slate-900">
              {title}
            </RadixDialog.Title>
          )}
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/* Bottom sheet variant for mobile */
export function Sheet({ open, onOpenChange, title, children, className }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 bg-black/60 z-40 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <RadixDialog.Content
          className={clsx(
            "fixed z-50 bg-slate-800 light:bg-white rounded-t-2xl shadow-2xl border-t border-slate-700 light:border-slate-200 px-4 pt-4 pb-8",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom",
            "bottom-0 left-0 right-0 max-h-[85vh] overflow-y-auto",
            className,
          )}
        >
          <div className="mx-auto w-10 h-1 rounded-full bg-slate-600 mb-4" />
          {title && (
            <RadixDialog.Title className="text-base font-semibold mb-3 text-slate-100 light:text-slate-900">
              {title}
            </RadixDialog.Title>
          )}
          {children}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
