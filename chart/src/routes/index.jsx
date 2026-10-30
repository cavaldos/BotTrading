import { lazy } from 'react';

const TerminalPage = lazy(() => import('@/terminal/TerminalPage'));
const OrdersPage = lazy(() => import('@/terminal/OrdersPage'));
const SnapshotPage = lazy(() => import('@/pages/Snapshot'));
const BacktestRedirect = lazy(() => import('@/terminal/LegacyRedirects').then(m => ({ default: m.BacktestRedirect })));
const LiveRedirect = lazy(() => import('@/terminal/LegacyRedirects').then(m => ({ default: m.LiveRedirect })));
const IndicatorsRedirect = lazy(() => import('@/terminal/LegacyRedirects').then(m => ({ default: m.IndicatorsRedirect })));

// One TradingView-style chart for backtest, SMC indicator and live data.
// The old page routes redirect to it with the matching ?source=.
const Router = [
    {
        name: 'Chart',
        path: '/',
        component: TerminalPage,
        Layout: null,
    },
    {
        // Every order and position on the exchange accounts (read-only)
        name: 'Exchange orders',
        path: '/orders',
        component: OrdersPage,
        Layout: null,
    },
    {
        name: 'Live Trade',
        path: '/live-trade',
        component: LiveRedirect,
        Layout: null,
        hidden: true,
    },
    {
        name: 'Indicators',
        path: '/indicator',
        component: IndicatorsRedirect,
        Layout: null,
        hidden: true,
    },
    {
        name: 'Combine',
        path: '/combine',
        component: BacktestRedirect,
        Layout: null,
        hidden: true,
    },
    {
        // Render-only page screenshotted by the live bot (not shown in navigation)
        name: 'Snapshot',
        path: '/snapshot',
        component: SnapshotPage,
        Layout: null,
        hidden: true,
    },

];

export default Router;
