const intradayTimeFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: '2-digit',
  month: 'short',
  year: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const dateOnlyFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: '2-digit',
  month: 'short',
  year: '2-digit',
});

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function normalizeTimeToUnixSeconds(time) {
  if (Number.isFinite(time)) {
    return Number(time);
  }

  if (typeof time === 'string') {
    const asNumber = Number(time);
    if (Number.isFinite(asNumber)) {
      return asNumber;
    }

    const parsedTime = Date.parse(time);
    if (Number.isFinite(parsedTime)) {
      return Math.floor(parsedTime / 1000);
    }

    return null;
  }

  if (time && typeof time === 'object') {
    if (Number.isFinite(time.timestamp)) {
      return Number(time.timestamp);
    }

    if (Number.isFinite(time.year) && Number.isFinite(time.month) && Number.isFinite(time.day)) {
      return Math.floor(new Date(time.year, time.month - 1, time.day).getTime() / 1000);
    }
  }

  return null;
}

function chartTimeToDate(time) {
  if (Number.isFinite(time)) {
    return new Date(Number(time) * 1000);
  }

  if (typeof time === 'string') {
    const asNumber = Number(time);
    if (Number.isFinite(asNumber)) {
      return new Date(asNumber * 1000);
    }

    const parsedTime = Date.parse(time);
    if (Number.isFinite(parsedTime)) {
      return new Date(parsedTime);
    }

    return null;
  }

  if (time && typeof time === 'object') {
    if (Number.isFinite(time.timestamp)) {
      return new Date(Number(time.timestamp) * 1000);
    }

    if (Number.isFinite(time.year) && Number.isFinite(time.month) && Number.isFinite(time.day)) {
      return new Date(time.year, time.month - 1, time.day);
    }
  }

  return null;
}

function isIntradayTime(time) {
  if (Number.isFinite(time)) {
    return true;
  }

  if (typeof time === 'string') {
    return Number.isFinite(Number(time)) || /T\d{2}:\d{2}|\d{2}:\d{2}/.test(time);
  }

  return Boolean(time && typeof time === 'object' && Number.isFinite(time.timestamp));
}

function formatCrosshairTimeLabel(time) {
  const date = chartTimeToDate(time);
  if (!date || Number.isNaN(date.getTime())) {
    return null;
  }

  return isIntradayTime(time)
    ? intradayTimeFormatter.format(date)
    : dateOnlyFormatter.format(date);
}

export const crosshairTimeAxisLabelStyle = Object.freeze({
  position: 'absolute',
  left: '-9999px',
  bottom: '10px',
  transform: 'translateX(-50%)',
  maxWidth: 'calc(100% - 16px)',
  padding: '6px 10px',
  borderRadius: '6px',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  background: 'rgba(46, 51, 64, 0.96)',
  boxShadow: '0 4px 18px rgba(0, 0, 0, 0.32)',
  color: '#f2f4f8',
  fontSize: '12px',
  fontWeight: 500,
  lineHeight: 1.2,
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  pointerEvents: 'none',
  zIndex: 110,
  opacity: 0,
  transition: 'opacity 0.12s ease',
});

export function hideCrosshairTimeLabel(labelElement) {
  if (!labelElement) {
    return;
  }

  labelElement.style.opacity = '0';
  labelElement.style.left = '-9999px';
}

function resolveHoveredTime({ chart, x, width, time }) {
  const timeScale = chart.timeScale();
  const directTime = timeScale.coordinateToTime(x) ?? time;
  if (directTime != null) {
    return directTime;
  }

  const visibleRange = timeScale.getVisibleRange();
  if (!visibleRange) {
    return null;
  }

  const from = normalizeTimeToUnixSeconds(visibleRange.from);
  const to = normalizeTimeToUnixSeconds(visibleRange.to);
  if (!Number.isFinite(from) || !Number.isFinite(to) || width <= 0) {
    return null;
  }

  const ratio = clamp(x / width, 0, 1);
  return from + (to - from) * ratio;
}

export function updateCrosshairTimeLabel({ chart, container, labelElement, pointX, time }) {
  if (!chart || !container || !labelElement || !Number.isFinite(pointX)) {
    hideCrosshairTimeLabel(labelElement);
    return;
  }

  const x = pointX;
  const parentElement = labelElement.parentElement;
  const containerWidth = container.clientWidth;
  const parentWidth = parentElement?.clientWidth ?? containerWidth;

  if (!Number.isFinite(x) || containerWidth <= 0 || parentWidth <= 0 || x < 0 || x > containerWidth) {
    hideCrosshairTimeLabel(labelElement);
    return;
  }

  const hoveredTime = resolveHoveredTime({ chart, x, width: containerWidth, time });
  const nextLabel = formatCrosshairTimeLabel(hoveredTime);

  if (!nextLabel) {
    hideCrosshairTimeLabel(labelElement);
    return;
  }

  if (labelElement.textContent !== nextLabel) {
    labelElement.textContent = nextLabel;
  }

  const halfWidth = labelElement.offsetWidth / 2;
  const containerOffsetLeft = container.offsetLeft - (parentElement?.offsetLeft ?? 0);
  const relativeX = x + containerOffsetLeft;
  const leftPadding = halfWidth + 8;
  const rightPadding = Math.max(leftPadding, parentWidth - halfWidth - 8);

  labelElement.style.left = `${clamp(relativeX, leftPadding, rightPadding)}px`;
  labelElement.style.opacity = '1';
}
