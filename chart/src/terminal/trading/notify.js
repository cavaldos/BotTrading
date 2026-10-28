// Attention cues for a new bot signal and for order fills: a short chime, and a
// system notification when the tab is in the background (permission is asked when connecting).
import { ORDER_CHANGES, orderDetail, shortReason, sideLabel, typeLabel } from './orderText';

let audio = null;

// Rising two-tone: new signal / filled. Falling: not filled / problem.
const UP = [880, 1320];
const DOWN = [660, 440];

function chime(tones = UP) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  audio = audio || new Ctx();
  if (audio.state === 'suspended') audio.resume();
  const t = audio.currentTime;
  tones.forEach((freq, i) => {
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t + i * 0.12);
    gain.gain.exponentialRampToValueAtTime(0.12, t + i * 0.12 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.12 + 0.25);
    osc.connect(gain).connect(audio.destination);
    osc.start(t + i * 0.12);
    osc.stop(t + i * 0.12 + 0.3);
  });
}

/** 'granted' | 'denied' | 'default' | 'unsupported' */
export function notificationPermission() {
  try {
    return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
  } catch {
    return 'unsupported';
  }
}

/** Ask for desktop notifications; call it from a click. Resolves to the new permission. */
export async function requestNotifications() {
  try {
    if (typeof Notification === 'undefined') return 'unsupported';
    if (Notification.permission !== 'default') return Notification.permission;
    return await Notification.requestPermission();
  } catch {
    return notificationPermission();
  }
}

// The page is not in front: the tab is hidden or another window (the bot terminal) has focus
const away = () => document.hidden || !document.hasFocus();

function desktopNotice(title, options, onClick) {
  if (!away() || notificationPermission() !== 'granted') return;
  const n = new Notification(title, options);
  n.onclick = () => {
    window.focus();
    onClick?.();
    n.close();
  };
}

/**
 * New bot signal: waiting for approval, or (`auto`) already being traded by the bot.
 * `onClick` runs when the desktop notification is clicked.
 */
export function announceSignal(signal, { onClick, auto = false } = {}) {
  try {
    chime();
  } catch {
    // audio blocked until the user interacts with the page
  }
  try {
    const side = sideLabel(signal.side);
    desktopNotice(`New signal: ${side} ${signal.symbol} ${signal.signalTimeframe || ''}`.trim(), {
      body: `Entry ${signal.entry} · Stop ${signal.stopLoss} · Target ${signal.takeProfit}\n${auto ? 'Auto mode: placing the order' : 'Waiting for your approval'}`,
      tag: `signal-${signal.id}`,
      // Stays on screen until clicked when the signal needs a decision
      requireInteraction: !auto,
    }, onClick);
  } catch {
    // notifications not supported
  }
}

/** An approved signal whose order the exchange refused. */
export function announceSignalFailed(signal, { onClick } = {}) {
  try {
    chime(DOWN);
  } catch {
    // audio blocked until the user interacts with the page
  }
  try {
    desktopNotice(`Order failed: ${sideLabel(signal.side)} ${signal.symbol} ${signal.signalTimeframe || ''}`.trim(), {
      body: shortReason(signal.resultText) || 'The exchange did not accept the order',
      tag: `signal-${signal.id}`,
    }, onClick);
  } catch {
    // notifications not supported
  }
}

export function announceOrder(order, change, { onClick } = {}) {
  const bad = change === 'not_filled' || change === 'unknown' || change === 'cancelled';
  try {
    chime(bad ? DOWN : UP);
  } catch {
    // audio blocked until the user interacts with the page
  }
  try {
    const title = `${ORDER_CHANGES[change]?.title || 'Order update'}: ${sideLabel(order.side)} ${order.symbol}`;
    desktopNotice(title, {
      body: `${typeLabel(order.orderType)} · ${orderDetail(order)}`,
      tag: `order-${order.id}`,
    }, onClick);
  } catch {
    // notifications not supported
  }
}
