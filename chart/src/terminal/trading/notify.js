// Attention cues for a new bot signal and for order fills: a short chime, and a
// system notification when the tab is in the background (permission is asked when connecting).
import { ORDER_CHANGES, orderDetail, sideLabel, typeLabel } from './orderText';

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

export function announceSignal(signal) {
  try {
    chime();
  } catch {
    // audio blocked until the user interacts with the page
  }
  try {
    if (document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      const n = new Notification(`${signal.symbol} ${signal.side} · ${signal.signalTimeframe}`, {
        body: `Entry ${signal.entry} · SL ${signal.stopLoss} · TP ${signal.takeProfit}\nWaiting for approval`,
        tag: `signal-${signal.id}`,
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    }
  } catch {
    // notifications not supported
  }
}

export function announceOrder(order, change) {
  const good = change === 'filled' || change === 'partial';
  try {
    chime(good ? UP : DOWN);
  } catch {
    // audio blocked until the user interacts with the page
  }
  try {
    if (document.hidden && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      const title = `${ORDER_CHANGES[change]?.title || 'Order update'}: ${sideLabel(order.side)} ${order.symbol}`;
      const n = new Notification(title, {
        body: `${typeLabel(order.orderType)} · ${orderDetail(order)}`,
        tag: `order-${order.id}`,
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    }
  } catch {
    // notifications not supported
  }
}
