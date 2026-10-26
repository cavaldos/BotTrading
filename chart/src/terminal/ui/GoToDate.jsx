import { useEffect, useRef, useState } from 'react';
import { Dialog } from './Dialog';
import { IconCalendar, IconChevronLeft, IconChevronRight, IconClock } from './icons';

// All dates are UTC, like the chart's time axis. Times are unix seconds.
const DAY = 86400;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
const pad = (n) => String(n).padStart(2, '0');

const dayStart = (sec) => Math.floor(sec / DAY) * DAY;

function fmtDate(sec) {
  const d = new Date(sec * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function fmtTime(sec) {
  const s = ((sec % DAY) + DAY) % DAY;
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}`;
}

/** "2026-10-26" → seconds at UTC midnight, or null. */
function parseDate(str) {
  const m = /^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$/.exec(str || '');
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  const ms = Date.UTC(y, mo, d);
  const back = new Date(ms);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo || back.getUTCDate() !== d) return null;
  return ms / 1000;
}

/** "9:30" / "0930" → seconds into the day, or null. */
function parseTime(str) {
  const m = /^\s*(\d{1,2}):?(\d{2})\s*$/.exec(str || '');
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 3600 + min * 60;
}

const monthOf = (sec) => {
  const d = new Date(sec * 1000);
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() };
};

function Calendar({ month, onMonth, minDay, maxDay, selected, rangeFrom, rangeTo, onPick }) {
  const gridRef = useRef(null);
  const moveFocus = useRef(false);
  const [focusDay, setFocusDay] = useState(null);

  const first = Date.UTC(month.y, month.m, 1) / 1000;
  const count = new Date(Date.UTC(month.y, month.m + 1, 0)).getUTCDate();
  const lead = (new Date(first * 1000).getUTCDay() + 6) % 7;
  const last = first + (count - 1) * DAY;
  const today = dayStart(Date.now() / 1000);
  const enabled = (day) => day >= minDay && day <= maxDay;

  // One day in the grid is reachable with Tab; arrows move from there
  const candidates = [focusDay, ...selected, today, maxDay, minDay];
  const tabbable = candidates.find(d => d != null && d >= first && d <= last && enabled(d))
    ?? Array.from({ length: count }, (_, i) => first + i * DAY).find(enabled);

  useEffect(() => {
    if (!moveFocus.current || focusDay == null) return;
    moveFocus.current = false;
    gridRef.current?.querySelector(`[data-day="${focusDay}"]`)?.focus();
  }, [focusDay, month]);

  const onKeyDown = (e) => {
    const steps = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    const day = Number(e.target.dataset?.day);
    if (!steps[e.key] || !Number.isFinite(day)) return;
    e.preventDefault();
    const next = Math.max(minDay, Math.min(maxDay, day + steps[e.key] * DAY));
    const nm = monthOf(next);
    if (nm.y !== month.y || nm.m !== month.m) onMonth(nm);
    moveFocus.current = true;
    setFocusDay(next);
  };

  const shift = (delta) => {
    const d = new Date(Date.UTC(month.y, month.m + delta, 1));
    onMonth({ y: d.getUTCFullYear(), m: d.getUTCMonth() });
  };
  const canPrev = first > minDay;
  const canNext = last < maxDay;

  return (
    <div className="tv-cal">
      <div className="tv-cal-head">
        <button type="button" className="tv-btn tv-btn--icon" onClick={() => shift(-1)} disabled={!canPrev} aria-label="Previous month">
          <IconChevronLeft size={18} />
        </button>
        <span className="tv-cal-title" aria-live="polite">{MONTHS[month.m]} {month.y}</span>
        <button type="button" className="tv-btn tv-btn--icon" onClick={() => shift(1)} disabled={!canNext} aria-label="Next month">
          <IconChevronRight size={18} />
        </button>
      </div>
      <div className="tv-cal-dow" aria-hidden="true">
        {WEEKDAYS.map(w => <span key={w}>{w}</span>)}
      </div>
      <div className="tv-cal-grid" ref={gridRef} role="grid" aria-label={`${MONTHS[month.m]} ${month.y}`} onKeyDown={onKeyDown}>
        {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} />)}
        {Array.from({ length: count }, (_, i) => {
          const day = first + i * DAY;
          const isSel = selected.includes(day);
          const inRange = rangeFrom != null && rangeTo != null && day > rangeFrom && day < rangeTo;
          return (
            <button
              key={day}
              type="button"
              data-day={day}
              className={`tv-cal-day num${isSel ? ' is-selected' : ''}${inRange ? ' is-in-range' : ''}${day === today ? ' is-today' : ''}`}
              disabled={!enabled(day)}
              tabIndex={day === tabbable ? 0 : -1}
              aria-pressed={isSel}
              aria-label={fmtDate(day)}
              onClick={() => {
                setFocusDay(day);
                onPick(day);
              }}
            >
              {i + 1}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function DateTimeFields({ label, value, onChange, onFocus, noTime, dateRef, active }) {
  return (
    <div className={`tv-goto-row${active ? ' is-active' : ''}`}>
      {label && <span className="tv-goto-label">{label}</span>}
      <label className="tv-goto-input">
        <input
          ref={dateRef}
          className="num"
          value={value.d}
          onChange={(e) => onChange({ ...value, d: e.target.value })}
          onFocus={onFocus}
          placeholder="YYYY-MM-DD"
          inputMode="numeric"
          autoComplete="off"
          spellCheck={false}
          aria-label={`${label || 'Date'} (YYYY-MM-DD)`}
        />
        <IconCalendar size={18} />
      </label>
      <label className="tv-goto-input tv-goto-time">
        <input
          className="num"
          value={noTime ? '00:00' : value.t}
          onChange={(e) => onChange({ ...value, t: e.target.value })}
          onFocus={onFocus}
          disabled={noTime}
          placeholder="HH:MM"
          inputMode="numeric"
          autoComplete="off"
          aria-label={`${label || 'Time'} time, UTC (HH:MM)`}
          title={noTime ? 'Daily and longer bars have no time of day' : 'UTC'}
        />
        <IconClock size={18} />
      </label>
    </div>
  );
}

/**
 * "Go to" dialog: jump to one date/time, or fit a custom range on screen.
 * firstTime/lastTime: the first and last bar; view: { from, to } on screen now.
 */
export default function GoToDate({ firstTime, lastTime, barSeconds, view, onDate, onRange, onClose }) {
  const minDay = dayStart(firstTime);
  const maxDay = dayStart(lastTime);
  const noTime = barSeconds >= DAY;
  const field = (sec) => ({ d: fmtDate(sec), t: fmtTime(noTime ? 0 : sec) });

  const [tab, setTab] = useState('date');
  const [date, setDate] = useState(() => ({ d: fmtDate(maxDay), t: '00:00' }));
  const [range, setRange] = useState(() => ({
    from: field(view?.from ?? Math.max(firstTime, lastTime - 30 * DAY)),
    to: field(view?.to ?? lastTime),
  }));
  const [active, setActive] = useState('from');
  const [month, setMonth] = useState(() => monthOf(maxDay));
  const [error, setError] = useState(null);
  const dateRef = useRef(null);
  const fromRef = useRef(null);

  // Show the month of what is being edited
  const showMonth = (str) => {
    const d = parseDate(str);
    if (d != null && d >= minDay && d <= maxDay) setMonth(monthOf(d));
  };

  const switchTab = (next) => {
    setTab(next);
    setError(null);
    showMonth(next === 'date' ? date.d : range[active].d);
    requestAnimationFrame(() => (next === 'date' ? dateRef : fromRef).current?.focus());
  };

  const fromDay = parseDate(range.from.d);
  const toDay = parseDate(range.to.d);
  const selected = tab === 'date' ? [parseDate(date.d)] : [fromDay, toDay];

  const pick = (day) => {
    setError(null);
    if (tab === 'date') {
      setDate(v => ({ ...v, d: fmtDate(day) }));
      return;
    }
    setRange(r => {
      const next = { ...r, [active]: { ...r[active], d: fmtDate(day) } };
      // Keep "from" before "to"
      const a = parseDate(next.from.d);
      const b = parseDate(next.to.d);
      return a != null && b != null && a > b ? { from: next.to, to: next.from } : next;
    });
    if (active === 'from') setActive('to');
  };

  // Seconds for a date + time field, or an error message
  const resolve = (v, name) => {
    const d = parseDate(v.d);
    if (d == null) return `${name}: enter the date as YYYY-MM-DD`;
    const t = noTime ? 0 : parseTime(v.t);
    if (t == null) return `${name}: enter the time as HH:MM`;
    if (d < minDay || d > maxDay) return `${name}: data runs from ${fmtDate(minDay)} to ${fmtDate(maxDay)}`;
    return d + t;
  };

  const submit = (e) => {
    e?.preventDefault();
    if (tab === 'date') {
      const t = resolve(date, 'Date');
      if (typeof t === 'string') return setError(t);
      onDate(t);
    } else {
      const a = resolve(range.from, 'From');
      if (typeof a === 'string') return setError(a);
      const b = resolve(range.to, 'To');
      if (typeof b === 'string') return setError(b);
      onRange(Math.min(a, b), Math.max(a, b));
    }
    return onClose();
  };

  return (
    <Dialog
      title="Go to"
      size="xs"
      onClose={onClose}
      initialFocusRef={dateRef}
      footer={(
        <>
          <button type="button" className="tv-btn tv-btn--ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="tv-btn tv-btn--primary" onClick={submit}>Go to</button>
        </>
      )}
    >
      <div className="tv-bottom-head tv-goto-tabs" role="tablist">
        <button type="button" role="tab" className="tv-tab" aria-selected={tab === 'date'} onClick={() => switchTab('date')}>Date</button>
        <button type="button" role="tab" className="tv-tab" aria-selected={tab === 'range'} onClick={() => switchTab('range')}>Custom range</button>
      </div>
      <form className="tv-dialog-body tv-goto" onSubmit={submit}>
        {tab === 'date' ? (
          <DateTimeFields
            value={date}
            onChange={(v) => { setDate(v); setError(null); showMonth(v.d); }}
            noTime={noTime}
            dateRef={dateRef}
          />
        ) : (
          <>
            <DateTimeFields
              label="From"
              value={range.from}
              active={active === 'from'}
              onFocus={() => { setActive('from'); showMonth(range.from.d); }}
              onChange={(v) => { setRange(r => ({ ...r, from: v })); setError(null); showMonth(v.d); }}
              noTime={noTime}
              dateRef={fromRef}
            />
            <DateTimeFields
              label="To"
              value={range.to}
              active={active === 'to'}
              onFocus={() => { setActive('to'); showMonth(range.to.d); }}
              onChange={(v) => { setRange(r => ({ ...r, to: v })); setError(null); showMonth(v.d); }}
              noTime={noTime}
            />
          </>
        )}
        <Calendar
          month={month}
          onMonth={setMonth}
          minDay={minDay}
          maxDay={maxDay}
          selected={selected}
          rangeFrom={tab === 'range' ? fromDay : null}
          rangeTo={tab === 'range' ? toDay : null}
          onPick={pick}
        />
        <p className={`tv-goto-note${error ? ' is-error' : ''}`} role={error ? 'alert' : undefined}>
          {error || `Times are UTC · data ${fmtDate(minDay)} to ${fmtDate(maxDay)}`}
        </p>
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Dialog>
  );
}
