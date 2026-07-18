import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";

import { cn } from "@/shared/utils/utils";

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer data-[state=checked]:bg-primary data-[state=unchecked]:bg-gray-300 dark:data-[state=unchecked]:bg-gray-600 focus-visible:border-primary focus-visible:shadow-focus inline-flex h-7 w-12 shrink-0 items-center rounded-full border-2 border-transparent data-[state=unchecked]:border-gray-300 dark:data-[state=unchecked]:border-gray-600 shadow-[inset_0_1px_3px_rgba(0,0,0,0.15)] transition-all outline-none disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          "bg-white dark:bg-gray-100 pointer-events-none block size-5 rounded-full ring-0 shadow-md shadow-black/20 border border-gray-200 dark:border-gray-300 transition-transform data-[state=checked]:translate-x-[22px] data-[state=unchecked]:translate-x-0.5"
        )}
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };