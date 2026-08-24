import { type ClassValue, clsx } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// The custom font-size scale needs to be declared or twMerge would treat
// `text-caption` and `text-h1` as unrelated classes.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        {
          text: [
            'h1', 'h2', 'h3', 'body-lg', 'body', 'small',
            'caption', 'label', 'micro', 'tiny',
          ],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
