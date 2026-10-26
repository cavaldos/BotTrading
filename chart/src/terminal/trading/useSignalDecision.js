import { useEffect, useRef, useState } from 'react';
import { shortReason } from './orderText';

const CONFIRM_MS = 3000;

/**
 * Approve / reject one bot signal. Approve takes two clicks (Approve, then Confirm
 * within 3 s) so an order is never placed by a stray click.
 * `onDecide(id, action)` is useTrading().decide.
 */
export function useSignalDecision(signalId, onDecide) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const decide = async (action) => {
    clearTimeout(timer.current);
    setBusy(action);
    setError(null);
    try {
      const res = await onDecide(signalId, action);
      if (!res?.ok) setError(shortReason(res?.message) || 'Not applied');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
      setConfirming(false);
    }
  };

  const approve = () => {
    if (!confirming) {
      setConfirming(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setConfirming(false), CONFIRM_MS);
      return;
    }
    decide('approve');
  };

  return { confirming, busy, error, approve, reject: () => decide('reject') };
}
