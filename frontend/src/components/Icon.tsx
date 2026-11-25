const PATHS = {
  chat: "M4 5h16v11H8l-4 4z",
  live: "M3 6h18v12H3zM10 9.5v5l4.5-2.5z",
  ops: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  send: "M5 12h13M13 6l6 6-6 6",
  plus: "M12 5v14M5 12h14",
  cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9",
  reset: "M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4",
  chevron: "M9 6l6 6-6 6",
} as const;

export type IconName = keyof typeof PATHS;

export default function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={PATHS[name]} />
    </svg>
  );
}
