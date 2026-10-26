// Icon set for the terminal: 1.5px strokes, currentColor, 24px grid.
// Drawn here because drawing-tool glyphs (fib, ray, position) are not in common packs.

const base = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: false,
};

const make = (children, extra) => {
  function Icon({ size = 18, ...rest }) {
    return <svg {...base} {...extra} width={size} height={size} {...rest}>{children}</svg>;
  }
  return Icon;
};

const dot = (cx, cy) => <circle cx={cx} cy={cy} r="1.8" fill="currentColor" stroke="none" />;

export const IconCursor = make(<path d="M6 3.5l12 7.2-5.4 1.3 3.2 5.6-2.2 1.3-3.2-5.6-4.4 3.4z" />);
export const IconCrosshair = make(<><path d="M12 3v7M12 14v7M3 12h7M14 12h7" /></>);
export const IconTrend = make(<><path d="M5 19L19 5" />{dot(5, 19)}{dot(19, 5)}</>);
export const IconRay = make(<><path d="M5 17L21 6" />{dot(5, 17)}{dot(12, 12.2)}</>);
export const IconHLine = make(<><path d="M2 12h20" />{dot(12, 12)}</>);
export const IconVLine = make(<><path d="M12 2v20" />{dot(12, 12)}</>);
export const IconRect = make(<><rect x="5" y="7" width="14" height="10" rx="0.5" />{dot(5, 7)}{dot(19, 17)}</>);
export const IconFib = make(<><path d="M3 5h18M3 9.5h18M3 13.5h18M3 19h18" /><path d="M6 19L18 5" strokeDasharray="2 2" /></>);
export const IconLong = make(<><path d="M4 12h16" /><path d="M4 4h16v8H4z" fill="currentColor" fillOpacity="0.18" stroke="none" /><path d="M4 12h16v8H4z" fill="currentColor" fillOpacity="0.06" stroke="none" /><path d="M12 10V5M9.5 7.5L12 5l2.5 2.5" /></>);
export const IconShort = make(<><path d="M4 12h16" /><path d="M4 12h16v8H4z" fill="currentColor" fillOpacity="0.18" stroke="none" /><path d="M12 14v5M9.5 16.5L12 19l2.5-2.5" /></>);
export const IconRuler = make(<><path d="M3.5 16.5L16.5 3.5l4 4-13 13z" /><path d="M7 13l1.5 1.5M10 10l1.5 1.5M13 7l1.5 1.5" /></>);
export const IconText = make(<><path d="M5 6V4.5h14V6M12 4.5v15M9 19.5h6" /></>);
export const IconBrush = make(<><path d="M4 18c2.5 0 3-1.5 3.5-3 .5-1.4 1.6-2.2 3-1.8l1.5 1.5c.4 1.4-.4 2.6-1.8 3C8.6 18.2 7 19 4 18z" /><path d="M10.5 13.2L19 4.7a1.4 1.4 0 012 2l-8.5 8.5" /></>);
export const IconMagnet = make(<><path d="M6 4v8a6 6 0 0012 0V4" /><path d="M6 8h4M14 8h4M10 4v8a2 2 0 004 0V4" /></>);
export const IconLock = make(<><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 018 0v3" /></>);
export const IconUnlock = make(<><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 017.6-1.8" /></>);
export const IconEye = make(<><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="3" /></>);
export const IconEyeOff = make(<><path d="M4 4l16 16" /><path d="M9.9 6A9.6 9.6 0 0112 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 01-2.6 3.4M6.6 7.6A16 16 0 002.5 12S6 18.5 12 18.5a9 9 0 004.4-1.1" /></>);
export const IconTrash = make(<><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></>);
export const IconUndo = make(<><path d="M9 14L4 9l5-5" /><path d="M4 9h10a6 6 0 010 12h-3" /></>);
export const IconSearch = make(<><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></>);
export const IconChevronDown = make(<path d="M6 9l6 6 6-6" />);
export const IconChevronUp = make(<path d="M6 15l6-6 6 6" />);
export const IconChevronRight = make(<path d="M9 6l6 6-6 6" />);
export const IconChevronLeft = make(<path d="M15 6l-6 6 6 6" />);
export const IconClose = make(<path d="M6 6l12 12M18 6L6 18" />);
export const IconSettings = make(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z" /></>);
export const IconPlus = make(<path d="M12 5v14M5 12h14" />);
export const IconIndicators = make(<><path d="M3 17l5-6 4 4 4-7 5 6" /><path d="M3 21h18" /></>);
export const IconCamera = make(<><path d="M4 8h3l1.5-2.5h7L17 8h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></>);
export const IconCandles = make(<><path d="M8 3v3M8 17v4M16 5v3M16 15v4" /><rect x="5.5" y="6" width="5" height="11" rx="0.5" fill="currentColor" fillOpacity="0.25" /><rect x="13.5" y="8" width="5" height="7" rx="0.5" /></>);
export const IconHollow = make(<><path d="M8 3v3M8 17v4M16 5v3M16 15v4" /><rect x="5.5" y="6" width="5" height="11" rx="0.5" /><rect x="13.5" y="8" width="5" height="7" rx="0.5" /></>);
export const IconHeikin = make(<><path d="M8 4v3M8 16v4M16 6v3M16 14v4" /><rect x="5.5" y="7" width="5" height="9" rx="2" /><rect x="13.5" y="9" width="5" height="5" rx="2" fill="currentColor" fillOpacity="0.25" /></>);
export const IconBars = make(<><path d="M8 4v15M5 7h3M8 15h3M16 6v13M13 10h3M16 16h3" /></>);
export const IconLine = make(<path d="M3 17l5-6 4 3 4-7 5 5" />);
export const IconArea = make(<><path d="M3 17l5-6 4 3 4-7 5 5" /><path d="M3 17l5-6 4 3 4-7 5 5V20H3z" fill="currentColor" fillOpacity="0.2" stroke="none" /></>);
export const IconToEnd = make(<><path d="M7 7l5 5-5 5M13 7l5 5-5 5" /></>);
export const IconWatchlist = make(<><path d="M4 6h16M4 12h16M4 18h10" /></>);
export const IconTrades = make(<><path d="M4 18l5-6 4 3 7-9" /><path d="M15 6h5v5" /></>);
export const IconOrder = make(<><path d="M7 4v16M7 8h-2.5v7H7M17 4v16M17 9h2.5v6H17" /><path d="M10.5 12h3" /></>);
export const IconMaximize = make(<><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></>);
export const IconMinimize = make(<><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" /></>);
export const IconCopy = make(<><rect x="8" y="8" width="12" height="12" rx="1.5" /><path d="M16 8V5.5A1.5 1.5 0 0014.5 4h-9A1.5 1.5 0 004 5.5v9A1.5 1.5 0 005.5 16H8" /></>);
export const IconSort = make(<><path d="M8 10l4-4 4 4M8 14l4 4 4-4" /></>);
export const IconRefresh = make(<><path d="M20 11a8 8 0 00-14.5-4.5L4 8M4 4v4h4M4 13a8 8 0 0014.5 4.5L20 16M20 20v-4h-4" /></>);
export const IconStrategy = make(<><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>);
export const IconMore = make(<>{dot(5, 12)}{dot(12, 12)}{dot(19, 12)}</>);
export const IconDot = make(<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />);
export const IconCalendar = make(<><rect x="3.5" y="5" width="17" height="15" rx="2" /><path d="M3.5 9.5h17M8 3v4M16 3v4" />{[[8, 13.5], [12, 13.5], [16, 13.5], [8, 17], [12, 17]].map(([x, y]) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.1" fill="currentColor" stroke="none" />)}</>);
export const IconClock = make(<><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>);
