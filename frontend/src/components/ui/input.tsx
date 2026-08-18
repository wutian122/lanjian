import * as React from "react";

import { cn } from "@/shared/utils/utils";

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "file:text-foreground selection:bg-primary selection:text-primary-foreground flex h-11 w-full min-w-0 rounded-sm border border-input bg-background px-4 py-2.5 text-base text-foreground shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:shadow-focus file:inline-flex file:h-9 file:border-0 file:bg-transparent file:text-base file:font-medium disabled:cursor-not-allowed disabled:opacity-50",
        "aria-invalid:border-secondary",
        className
      )}
      {...props}
    />
  );
}

export { Input };
